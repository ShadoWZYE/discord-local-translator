from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from translator_bot.bot import create_bot
from translator_bot.config import PROJECT_ROOT, Settings
from translator_bot.single_instance import AlreadyRunning, project_instance


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
    try:
        with project_instance(PROJECT_ROOT / 'data' / 'translator.instance.lock'):
            logging.getLogger('translator_bot').info('Translator instance acquired pid=%s', os.getpid())
            bot = create_bot(settings)
            bot.run(settings.discord_token, log_handler=None)
    except AlreadyRunning:
        logging.getLogger('translator_bot').warning('Translator already running; duplicate startup skipped')


if __name__ == "__main__":
    main()
