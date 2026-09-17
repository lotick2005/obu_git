"""Уведомление о лиде в Saved Messages.

Единственный адресат отправки во всём проекте — 'me'. В отслеживаемые
чаты клиент не пишет никогда.
"""

import asyncio
import logging

from telethon.errors import FloodWaitError

log = logging.getLogger(__name__)

PREVIEW_CHARS = 300


def build_message_link(chat_id: int, message_id: int, username: str | None) -> str:
    """Прямая ссылка на сообщение. Пустая строка — если ссылку не построить."""
    if chat_id > 0:                      # личка, публичной ссылки не существует
        return ""
    if username:
        return f"https://t.me/{username}/{message_id}"
    internal = str(chat_id)
    internal = internal[4:] if internal.startswith("-100") else internal.lstrip("-")
    return f"https://t.me/c/{internal}/{message_id}"


def format_notification(row: dict, link: str) -> str:
    """Текст уведомления. Пустые «Заход» и ссылка не выводятся вовсе."""
    text = (row.get("text") or "")[:PREVIEW_CHARS]
    lines = [
        f"🎯 {row.get('score')} | {row.get('chat_title')}",
        text,
        "",
        f"Почему: {row.get('reason')}",
    ]
    opener = (row.get("suggested_opener") or "").strip()
    if opener:
        lines.append(f"Заход: {opener}")
    lines.append(f"👤 @{row.get('author') or 'скрыт'}")
    if link:
        lines.append(f"🔗 {link}")
    return "\n".join(lines)


async def send_lead(client, text: str) -> None:
    """Отправить уведомление себе. Одна повторная попытка после FloodWait."""
    for attempt in (1, 2):
        try:
            await client.send_message("me", text, link_preview=False)
            return
        except FloodWaitError as exc:
            if attempt == 2:
                log.error("FloodWait повторно (%s с), уведомление пропущено",
                          exc.seconds)
                return
            log.warning("FloodWait на отправке: ждём %s с", exc.seconds)
            await asyncio.sleep(exc.seconds + 5)


if __name__ == "__main__":
    import sys

    long_text = "а" * 450
    CASES = [
        ("публичный чат с username",
         lambda: build_message_link(-1001234567890, 77, "shopchat"),
         lambda r: r == "https://t.me/shopchat/77"),
        ("приватная супергруппа",
         lambda: build_message_link(-1001234567890, 77, None),
         lambda r: r == "https://t.me/c/1234567890/77"),
        ("личка (положительный chat_id)",
         lambda: build_message_link(12345678, 77, None),
         lambda r: r == ""),
        ("format без suggested_opener",
         lambda: format_notification(
             {"score": 74, "chat_title": "Оптовики РФ", "text": "заявки теряются",
              "reason": "теряет заявки", "suggested_opener": "", "author": None},
             "https://t.me/c/1/77"),
         lambda r: "Заход:" not in r and "👤 @скрыт" in r),
        ("format с текстом длиннее 300",
         lambda: format_notification(
             {"score": 90, "chat_title": "Чат", "text": long_text,
              "reason": "ищет бота", "suggested_opener": "спросить объёмы",
              "author": "vasya"},
             ""),
         lambda r: r.split("\n")[1] == "а" * 300 and "🔗" not in r),
    ]

    print(f"{'#':>2}  {'РЕЗУЛЬТАТ':<9} ОПИСАНИЕ")
    print("-" * 100)
    failed = 0
    for i, (name, run, expect) in enumerate(CASES, 1):
        got = run()
        ok = expect(got)
        failed += not ok
        print(f"{i:>2}  {'PASS' if ok else 'FAIL':<9} {name}")
        preview = got if len(got) <= 320 else got[:320] + "…"
        print("    факт: " + preview.replace("\n", "\n          "))
    print("-" * 100)
    print(f"{len(CASES) - failed}/{len(CASES)} passed")
    sys.exit(1 if failed else 0)
