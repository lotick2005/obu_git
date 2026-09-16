"""TG Radar — точка входа: конфиг, логи, Telethon-хендлер, главный цикл."""


def load_config(path: str = "config.yaml") -> dict:
    """Прочитать config.yaml."""
    raise NotImplementedError


def setup_logging(level: str) -> None:
    """logging в stdout с уровнем из конфига."""
    raise NotImplementedError


def build_telegram_client(config: dict, api_id: int, api_hash: str):
    """TelegramClient с опциональным telegram_proxy."""
    raise NotImplementedError


async def handle_message(event, ctx: dict) -> None:
    """Цепочка: дедуп -> префильтр -> Gemini -> уведомление -> запись."""
    raise NotImplementedError


async def process_pending(ctx: dict) -> None:
    """Разобрать накопленные pending, когда часовой лимит отпустил."""
    raise NotImplementedError


async def main() -> None:
    """Сборка, регистрация хендлера, graceful shutdown по SIGTERM/SIGINT."""
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
