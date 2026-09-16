"""Клиент Gemini и классификация сообщения в строгий JSON."""


def build_client(config: dict, api_key: str):
    """Выставить HTTPS_PROXY (если задан gemini_proxy) и создать genai.Client."""
    raise NotImplementedError


def build_prompt(text: str, chat_title: str, username: str | None) -> str:
    """Собрать пользовательскую часть запроса."""
    raise NotImplementedError


def parse_verdict(raw: str) -> dict | None:
    """Разобрать ответ модели в dict по схеме. None — если JSON невалиден."""
    raise NotImplementedError


def classify(client, config: dict, text: str, chat_title: str,
             username: str | None) -> dict | None:
    """Вызов Gemini с одним ретраем. None — если оба раза мусор."""
    raise NotImplementedError
