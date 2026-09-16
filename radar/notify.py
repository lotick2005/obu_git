"""Уведомление о лиде в Saved Messages. Единственный адресат отправки — 'me'."""


def build_message_link(chat_id: int, message_id: int,
                       chat_username: str | None) -> str:
    """t.me/{username}/{id} для публичных, t.me/c/{internal_id}/{id} для приватных."""
    raise NotImplementedError


def format_notification(score: int, chat_title: str, text: str, reason: str,
                        suggested_opener: str, username: str | None,
                        link: str) -> str:
    """Текст уведомления по заданному шаблону."""
    raise NotImplementedError


async def send_to_saved(client, body: str) -> None:
    """Отправить в Saved Messages с обработкой FloodWaitError."""
    raise NotImplementedError
