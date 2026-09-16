"""Локальный префильтр: нормализация текста и отбор по ключевым словам.

Ни сети, ни денег, ни telethon — модуль тестируется в изоляции.
"""

import re
from dataclasses import dataclass, field

# Эмодзи и пиктограммы. Диапазоны перечислены явно, без внешних библиотек;
# кириллица и латиница сюда не попадают.
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # эмодзи, символы, флаги, дополнительные пиктограммы
    "\U00002600-\U000027BF"   # разные символы и дингбаты
    "\U00002B00-\U00002BFF"   # стрелки и геометрия
    "\U00002190-\U000021FF"   # стрелки
    "\U00002000-\U0000200D"   # типографика и zero-width joiner
    "\U0000FE00-\U0000FE0F"   # селекторы начертания
    "\U0001F1E6-\U0001F1FF"   # региональные индикаторы
    "\U000024C2-\U0001F251"
    "]+",
    flags=re.UNICODE,
)

# Состояние, заполняется configure() из config.yaml при старте.
_keywords: list[str] = []
_stop_words: list[str] = []
_min_length: int = 15


@dataclass
class PrefilterResult:
    passed: bool
    reason: str                              # ok | empty | self | too_short
                                             # | stop_word | no_keyword
    matched: list[str] = field(default_factory=list)


def normalize(text: str) -> str:
    """Нижний регистр, ё->е, без эмодзи и пунктуации, схлопнутые пробелы."""
    if not isinstance(text, str):
        return ""
    s = text.lower().replace("ё", "е")
    s = _EMOJI_RE.sub(" ", s)
    s = "".join(ch if (ch.isalnum() or ch in "-@") else " " for ch in s)
    return " ".join(s.split())


def configure(config: dict) -> None:
    """Загрузить ключи из конфига, прогнав их через ту же normalize().

    КРИТИЧНО: без этого ключ с «ё» («приём заявок») никогда не совпадёт
    с нормализованным текстом сообщения. Пустые после нормализации выбрасываем.
    """
    global _keywords, _stop_words, _min_length
    _keywords = [k for k in (normalize(x) for x in config.get("keywords", [])) if k]
    _stop_words = [s for s in (normalize(x) for x in config.get("stop_words", [])) if s]
    _min_length = int(config.get("min_message_length", 15))


def _find_all(normalized: str, needles: list[str]) -> list[str]:
    """Совпадения подстрокой, в порядке следования в конфиге.

    Именно подстрокой, а не по границам слов: так ловится морфология
    («прием заказов» найдётся внутри «приема заказов»).
    """
    return [n for n in needles if n in normalized]


def check(text: str, is_self: bool) -> PrefilterResult:
    """Решение префильтра. Выход на первом сработавшем правиле."""
    normalized = normalize(text)

    if not normalized:
        return PrefilterResult(False, "empty")

    if is_self:
        return PrefilterResult(False, "self")

    if len(normalized) < _min_length:
        return PrefilterResult(False, "too_short")

    stop_hits = _find_all(normalized, _stop_words)
    if stop_hits:
        return PrefilterResult(False, "stop_word", [stop_hits[0]])

    hits = _find_all(normalized, _keywords)
    if not hits:
        return PrefilterResult(False, "no_keyword")

    return PrefilterResult(True, "ok", hits)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    import yaml

    config = yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "config.yaml").read_text(
            encoding="utf-8"
        )
    )
    configure(config)

    # (текст, is_self, ожидаемый reason)
    CASES = [
        ("ребят у нас заявки теряются в вотсапе кто что использует", False, "ok"),
        ("ищу разработчика для телеграм бота", False, "ok"),
        ("нужен приём заявок круглосуточно", False, "ok"),
        ("подписывайтесь на мой канал про ботов", False, "stop_word"),
        ("продаю оптом бакалею по области заявки теряются в вотсапе", False, "ok"),
        ("бот", False, "too_short"),
        ("а бот у вас платный", False, "no_keyword"),
        ("🔥 Нужен разработчик!!! 🔥", False, "ok"),
        ("автоматизировали учёт в 1С полгода назад", False, "ok"),
        ("всем доброе утро как там погода", False, "no_keyword"),
    ]

    print(f"{'#':>2}  {'РЕЗУЛЬТАТ':<9} {'ОЖИДАНИЕ':<10} {'ФАКТ':<10} ВХОД")
    print("-" * 100)
    failed = 0
    for i, (text, is_self, expected) in enumerate(CASES, 1):
        result = check(text, is_self)
        ok = result.reason == expected
        failed += not ok
        print(
            f"{i:>2}  {'PASS' if ok else 'FAIL':<9} {expected:<10} "
            f"{result.reason:<10} {text[:45]!r}"
            + (f"  matched={result.matched}" if result.matched else "")
        )
    print("-" * 100)
    print(f"{len(CASES) - failed}/{len(CASES)} passed")
    sys.exit(1 if failed else 0)
