"""Локальный префильтр: нормализация текста и отбор по ключевым словам.

Ни сети, ни денег. Отсекает всё, что не должно доходить до Gemini.
"""


def normalize(text: str) -> str:
    """Нижний регистр, убрать эмодзи, ё->е, схлопнуть пробелы."""
    raise NotImplementedError


def find_keyword(normalized: str, keywords: list[str]) -> str | None:
    """Первое найденное ключевое слово или None."""
    raise NotImplementedError


def find_stop_word(normalized: str, stop_words: list[str]) -> str | None:
    """Первое найденное стоп-слово или None."""
    raise NotImplementedError


def check(text: str, is_own: bool, config: dict) -> tuple[bool, str]:
    """Решение префильтра.

    Возвращает (passed, reason) — reason всегда заполнен, идёт в DEBUG-лог.
    """
    raise NotImplementedError
