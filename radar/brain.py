"""Клиент Gemini: классификация сообщения, прошедшего префильтр.

Клиент создаётся один раз в configure() и переиспользуется.
Telethon здесь не при чём и не импортируется.
"""

import json
import logging
import os
import re

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from radar import storage

log = logging.getLogger(__name__)

CATEGORIES = [
    "нужен_бот",
    "боль_с_заявками",
    "просто_обсуждение",
    "реклама",
    "мусор",
]

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "is_lead": {"type": "BOOLEAN"},
        "score": {"type": "INTEGER"},
        "category": {"type": "STRING", "enum": CATEGORIES},
        "reason": {"type": "STRING"},
        "suggested_opener": {"type": "STRING"},
    },
    "required": ["is_lead", "score", "category", "reason"],
}

MAX_TEXT_CHARS = 2000
MAX_REASON_CHARS = 200

# Снимает обёртку ```json ... ``` — модель иногда добавляет её вопреки schema.
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)

# Состояние, заполняется configure() при старте.
_client = None
_model: str = ""
_system_prompt: str = ""
_max_calls_per_hour: int = 200


def configure(config: dict) -> None:
    """Выставить прокси, взять ключ из окружения, создать клиент один раз.

    Порядок критичен: httpx внутри SDK читает HTTPS_PROXY из окружения
    в момент инстанцирования клиента. Выставлять прокси после — поздно.
    """
    global _client, _model, _system_prompt, _max_calls_per_hour

    proxy = (config.get("gemini_proxy") or "").strip()
    if proxy:
        os.environ["HTTPS_PROXY"] = proxy
        os.environ["HTTP_PROXY"] = proxy
        log.info("Gemini через прокси %s", proxy.split("@")[-1])

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY не задан. Скопируйте .env.example в .env "
            "и впишите ключ Gemini API."
        )

    _model = config["gemini_model"]
    _system_prompt = config["gemini_system_prompt"]
    _max_calls_per_hour = int(config.get("max_gemini_calls_per_hour", 200))
    _client = genai.Client(api_key=api_key)
    log.info("Gemini готов: модель %s, лимит %d вызовов в час",
             _model, _max_calls_per_hour)


def build_payload(text: str, chat_title: str, author: str | None) -> str:
    """Пользовательская часть запроса. Текст режется до 2000 символов."""
    body = text or ""
    if len(body) > MAX_TEXT_CHARS:
        body = body[:MAX_TEXT_CHARS] + " […обрезано]"
    return (
        f"Чат: {chat_title}\n"
        f"Автор: @{author or 'скрыт'}\n"
        f"Сообщение:\n{body}"
    )


def parse_verdict(raw: str) -> dict | None:
    """Разобрать ответ модели. None — если это не вердикт.

    Чистая функция: ни клиента, ни сети, тестируется в изоляции.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None

    candidate = raw.strip()
    fenced = _FENCE_RE.match(candidate)
    if fenced:
        candidate = fenced.group(1)

    try:
        data = json.loads(candidate)
    except (ValueError, TypeError):
        return None

    if not isinstance(data, dict):
        return None

    is_lead = data.get("is_lead")
    if not isinstance(is_lead, bool):
        return None

    score = data.get("score")
    if isinstance(score, bool) or not isinstance(score, int):
        try:
            score = int(score)
        except (TypeError, ValueError):
            return None
    score = max(0, min(100, score))

    category = data.get("category")
    if category not in CATEGORIES:
        category = "мусор"

    reason = data.get("reason")
    reason = ("" if reason is None else str(reason))[:MAX_REASON_CHARS]

    opener = data.get("suggested_opener")
    opener = "" if opener is None else str(opener)

    return {
        "is_lead": is_lead,
        "score": score,
        "category": category,
        "reason": reason,
        "suggested_opener": opener,
    }


def _call_api(payload: str) -> str:
    """Один вызов Gemini. Возвращает сырой текст ответа."""
    response = _client.models.generate_content(
        model=_model,
        contents=payload,
        config=types.GenerateContentConfig(
            system_instruction=_system_prompt,
            temperature=0,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
        ),
    )
    return response.text or ""


def classify(conn, text: str, chat_title: str, author: str | None) -> dict | None:
    """Классифицировать сообщение. None — лимит, сетевая ошибка или мусор.

    Вызывающий при None оставляет сообщение в pending либо помечает failed.
    Исключения наружу не летят.
    """
    if storage.api_calls_last_hour(conn) >= _max_calls_per_hour:
        log.info("Лимит Gemini исчерпан (%d вызовов в час), сообщение ждёт "
                 "в pending", _max_calls_per_hour)
        return None

    payload = build_payload(text, chat_title, author)
    raw = ""

    for attempt in (1, 2):
        # Считаем попытки, а не успехи: иначе серия ошибок обходит лимит.
        storage.register_api_call(conn)
        try:
            raw = _call_api(payload)
        except genai_errors.APIError as exc:
            log.warning("Gemini API вернул ошибку: %s", exc)
            return None
        except Exception as exc:  # сеть, таймаут, прокси — без ретрая
            log.warning("Сбой вызова Gemini: %s: %s", type(exc).__name__, exc)
            return None

        verdict = parse_verdict(raw)
        if verdict is not None:
            log.debug("Вердикт Gemini (попытка %d): %s", attempt, verdict)
            return verdict

        log.debug("Невалидный ответ Gemini, попытка %d", attempt)

    log.warning("Gemini дважды вернул невалидный JSON, сообщение пропущено. "
                "Сырой ответ: %s", raw[:500])
    return None


if __name__ == "__main__":
    import sys

    VALID = ('{"is_lead": true, "score": 82, "category": "боль_с_заявками", '
             '"reason": "теряет заявки в переписке", '
             '"suggested_opener": "спросить, сколько заявок в день"}')

    CASES = [
        ("валидный JSON, все поля", VALID,
         lambda v: v is not None and v["score"] == 82
         and v["category"] == "боль_с_заявками"),
        ("тот же JSON в ```json-обёртке", "```json\n" + VALID + "\n```",
         lambda v: v is not None and v["score"] == 82),
        ("score = 150", VALID.replace('"score": 82', '"score": 150'),
         lambda v: v is not None and v["score"] == 100),
        ("category = непонятно",
         VALID.replace('"боль_с_заявками"', '"непонятно"'),
         lambda v: v is not None and v["category"] == "мусор"),
        ("без suggested_opener",
         '{"is_lead": false, "score": 10, "category": "мусор", '
         '"reason": "шутка"}',
         lambda v: v is not None and v["suggested_opener"] == ""),
        ("мусор вместо JSON", "извините, не могу",
         lambda v: v is None),
    ]

    print(f"{'#':>2}  {'РЕЗУЛЬТАТ':<9} ОПИСАНИЕ")
    print("-" * 100)
    failed = 0
    for i, (name, raw_in, expect) in enumerate(CASES, 1):
        got = parse_verdict(raw_in)
        ok = expect(got)
        failed += not ok
        print(f"{i:>2}  {'PASS' if ok else 'FAIL':<9} {name}")
        print(f"    вход: {raw_in[:70]!r}")
        print(f"    факт: {got}")
    print("-" * 100)
    print(f"{len(CASES) - failed}/{len(CASES)} passed")
    sys.exit(1 if failed else 0)
