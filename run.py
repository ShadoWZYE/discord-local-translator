from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from translator_bot.bot import create_bot
from translator_bot.config import PROJECT_ROOT, Settings


def main() -> None:
    settings = Settings.load()
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(log_dir / "bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"),
        ],
    )
    bot = create_bot(settings)
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
