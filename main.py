"""TG Radar — точка входа.

Цепочка на каждое сообщение: дедуп → префильтр → Gemini → уведомление
в Saved Messages → запись в SQLite. Клиент только читает отслеживаемые
чаты; единственный адресат отправки — 'me'.
"""

import asyncio
import logging
import os
import signal
import sqlite3
import sys
import threading

import yaml
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.network import connection as tl_connection

from radar import brain, notify, prefilter, storage

log = logging.getLogger("tg-radar")

PENDING_INTERVAL = 300          # сек между проходами воркера pending
PENDING_LIMIT = 20              # сколько отложенных берём за проход
MAX_PARALLEL_GEMINI = 3         # потолок одновременных потоков с classify()

_gemini_sem = asyncio.Semaphore(MAX_PARALLEL_GEMINI)
_config: dict = {}


class _LockedConnection(sqlite3.Connection):
    """Соединение с общим замком на выполнение запросов.

    brain.classify() уходит в asyncio.to_thread и пишет в ту же базу,
    что и хендлер в основном потоке, поэтому замок живёт в самом
    соединении: обращения к storage есть и внутри brain, снаружи их
    обернуть нельзя.
    """

    _lock = threading.Lock()

    def execute(self, *args, **kwargs):
        with self._lock:
            return super().execute(*args, **kwargs)

    def commit(self):
        with self._lock:
            return super().commit()


def load_config(path: str = "config.yaml") -> dict:
    """Прочитать config.yaml."""
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def setup_logging(level: str) -> None:
    """Логи в stdout, уровень из конфига."""
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def open_db(db_path: str) -> sqlite3.Connection:
    """Схему создаёт storage, рабочее соединение переоткрываем потокобезопасным."""
    storage.close(storage.init_db(db_path))
    conn = sqlite3.connect(db_path, check_same_thread=False,
                           factory=_LockedConnection)
    conn.row_factory = sqlite3.Row
    return conn


def build_telegram_client(config: dict, api_id: int, api_hash: str) -> TelegramClient:
    """TelegramClient, при необходимости — через прокси из конфига."""
    session = config["session_name"]
    raw = (config.get("telegram_proxy") or "").strip()
    if not raw:
        return TelegramClient(session, api_id, api_hash)

    if raw.startswith("mtproto://"):
        host, port, secret = raw[len("mtproto://"):].rsplit(":", 2)
        log.info("Telegram через MTProto-прокси %s:%s", host, port)
        return TelegramClient(
            session, api_id, api_hash,
            connection=tl_connection.ConnectionTcpMTProxyRandomizedIntermediate,
            proxy=(host, int(port), secret),
        )

    if raw.startswith("socks5://"):
        try:
            import python_socks  # noqa: F401  проверка наличия, не зависимость проекта
        except ImportError:
            raise RuntimeError(
                "SOCKS5-прокси в Telethon требует пакет python-socks, которого "
                "нет в requirements.txt. Используйте mtproto://... или "
                "согласуйте добавление зависимости."
            )
        creds, _, hostport = raw[len("socks5://"):].rpartition("@")
        host, port = hostport.rsplit(":", 1)
        user, _, password = creds.partition(":")
        log.info("Telegram через SOCKS5 %s:%s", host, port)
        proxy = (("socks5", host, int(port), True, user, password) if creds
                 else ("socks5", host, int(port)))
        return TelegramClient(session, api_id, api_hash, proxy=proxy)

    raise RuntimeError(
        f"Не понимаю telegram_proxy={raw!r}. Ожидается mtproto://host:port:secret "
        "или socks5://user:pass@host:port."
    )


async def resolve_chats(client: TelegramClient, chats: list) -> list:
    """Развернуть список из конфига в entity. Битый чат не роняет запуск."""
    resolved = []
    for item in chats:
        for attempt in (1, 2):
            try:
                resolved.append(await client.get_entity(item))
                break
            except FloodWaitError as exc:
                if attempt == 2:
                    log.error("FloodWait повторно на чате %s (%s с), пропускаем",
                              item, exc.seconds)
                    break
                log.warning("FloodWait на резолве %s: ждём %s с", item, exc.seconds)
                await asyncio.sleep(exc.seconds + 5)
            except Exception as exc:
                log.error("Чат %s не резолвится (%s: %s), пропускаем",
                          item, type(exc).__name__, exc)
                break
    return resolved


async def _chat_username(client: TelegramClient, chat_id: int) -> str | None:
    """username чата для публичной ссылки. Не вышло — ссылка будет приватной."""
    try:
        return getattr(await client.get_entity(chat_id), "username", None)
    except Exception as exc:
        log.debug("username чата %s недоступен: %s", chat_id, exc)
        return None


async def process_one(conn, client, chat_id: int, message_id: int, text: str,
                      chat_title: str, author: str | None) -> None:
    """Классифицировать сохранённое сообщение и уведомить, если это лид."""
    async with _gemini_sem:
        verdict = await asyncio.to_thread(
            brain.classify, conn, text, chat_title, author
        )

    if verdict is None:
        # Лимит или сбой — сообщение остаётся pending, вернёмся к нему воркером.
        log.debug("Вердикта нет, %s/%s остаётся в pending", chat_id, message_id)
        return

    storage.save_verdict(conn, chat_id, message_id, verdict)
    log.debug("Вердикт %s/%s: %s", chat_id, message_id, verdict)

    min_score = _config.get("min_score", 60)
    if not (verdict["is_lead"] and verdict["score"] >= min_score):
        return

    username = await _chat_username(client, chat_id)
    link = notify.build_message_link(chat_id, message_id, username)
    body = notify.format_notification(
        {
            "score": verdict["score"],
            "chat_title": chat_title,
            "text": text,
            "reason": verdict["reason"],
            "suggested_opener": verdict["suggested_opener"],
            "author": author,
        },
        link,
    )
    # Сначала отправка, потом отметка: упадёт отправка — лид не потеряется.
    await notify.send_lead(client, body)
    storage.mark_notified(conn, chat_id, message_id)
    log.info("ЛИД: %s | score=%s | %s | %s",
             chat_title, verdict["score"], verdict["category"], link)


async def pending_worker(conn, client) -> None:
    """Добирать сообщения, отложенные при исчерпанном лимите Gemini."""
    while True:
        try:
            rows = storage.pending_batch(conn, PENDING_LIMIT)
            if rows:
                log.debug("Воркер pending: %d сообщений", len(rows))
            for row in rows:
                await process_one(conn, client, row["chat_id"], row["message_id"],
                                  row["text"], row["chat_title"], row["author"])
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Сбой воркера pending")
        await asyncio.sleep(PENDING_INTERVAL)


def make_handler(conn, client):
    """Хендлер новых сообщений. Наружу не бросает ничего и никогда."""

    async def handler(event) -> None:
        try:
            if event.out:
                return

            text = event.raw_text or ""
            if not text.strip():
                return

            chat_id, message_id = event.chat_id, event.id
            if storage.seen(conn, chat_id, message_id):
                return

            result = prefilter.check(text, is_self=False)
            if not result.passed:
                log.debug("Префильтр отсёк %s/%s: %s %s",
                          chat_id, message_id, result.reason, result.matched)
                return

            chat = await event.get_chat()
            chat_title = getattr(chat, "title", None) or str(chat_id)
            sender = await event.get_sender()
            author = getattr(sender, "username", None)

            log.debug("Префильтр пропустил %s/%s: %s",
                      chat_id, message_id, result.matched)
            storage.save_incoming(conn, chat_id, message_id, chat_title,
                                  author, text)
            await process_one(conn, client, chat_id, message_id, text,
                              chat_title, author)
        except Exception:
            # Упавший хендлер тихо убивает обработку следующих событий.
            log.exception("Сбой обработки сообщения")

    return handler


async def main() -> None:
    """Сборка, запуск, graceful shutdown."""
    global _config

    load_dotenv()
    _config = load_config()
    setup_logging(_config.get("log_level", "INFO"))

    conn = open_db(_config["db_path"])
    prefilter.configure(_config)
    brain.configure(_config)

    client = build_telegram_client(
        _config,
        int(os.environ["TG_API_ID"]),
        os.environ["TG_API_HASH"],
    )
    await client.start(phone=os.environ.get("TG_PHONE"))

    entities = await resolve_chats(client, _config.get("chats") or [])
    if not entities:
        log.error("Ни один чат из config.yaml не зарезолвился, выходим")
        await client.disconnect()
        storage.close(conn)
        return
    log.info("Слушаем %d чат(ов): %s", len(entities),
             ", ".join(getattr(e, "title", str(e.id)) for e in entities))

    client.add_event_handler(make_handler(conn, client),
                             events.NewMessage(chats=entities))
    worker = asyncio.create_task(pending_worker(conn, client))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    log.info("TG Radar запущен")
    runner = asyncio.create_task(client.run_until_disconnected())
    await asyncio.wait({runner, asyncio.create_task(stop.wait())},
                       return_when=asyncio.FIRST_COMPLETED)

    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass
    runner.cancel()
    await client.disconnect()
    storage.close(conn)
    log.info("TG Radar остановлен")


if __name__ == "__main__":
    asyncio.run(main())
