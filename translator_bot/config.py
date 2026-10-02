from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class Settings:
    discord_token: str
    guild_id: int
    database_path: Path
    log_level: str
    ollama_url: str
    ollama_model: str
    ollama_timeout_seconds: int
    additional_guild_ids: tuple[int, ...] = ()
    default_off_guild_ids: tuple[int, ...] = ()
    burst_window_seconds: float = 15.0

    @property
    def guild_ids(self) -> tuple[int, ...]:
        return tuple(dict.fromkeys((self.guild_id, *self.additional_guild_ids)))

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env")
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "DISCORD_TOKEN is missing. Copy .env.example to .env and add the bot token."
            )

        guild_raw = os.getenv("DISCORD_GUILD_ID", "").strip()
        try:
            guild_id = int(guild_raw)
        except ValueError as exc:
            raise RuntimeError("DISCORD_GUILD_ID must be a numeric Discord server ID.") from exc

        try:
            additional_ids = tuple(int(value.strip()) for value in os.getenv("DISCORD_ADDITIONAL_GUILD_IDS", "").split(",") if value.strip())
            default_off_ids = tuple(int(value.strip()) for value in os.getenv("DISCORD_DEFAULT_OFF_GUILD_IDS", "").split(",") if value.strip())
        except ValueError as exc:
            raise RuntimeError("Additional/default-off guild IDs must be comma-separated numeric IDs.") from exc
        if not set(default_off_ids).issubset({guild_id, *additional_ids}):
            raise RuntimeError("Default-off servers must also be in the allowed server list.")

        db_raw = os.getenv("DATABASE_PATH", "data/translator.sqlite3").strip()
        database_path = Path(db_raw)
        if not database_path.is_absolute():
            database_path = PROJECT_ROOT / database_path

        try:
            ollama_timeout = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "90"))
        except ValueError as exc:
            raise RuntimeError("OLLAMA_TIMEOUT_SECONDS must be an integer.") from exc

        try:
            burst_window = float(os.getenv("BURST_WINDOW_SECONDS", "15"))
        except ValueError as exc:
            raise RuntimeError("BURST_WINDOW_SECONDS must be a number.") from exc
        if not 1 <= burst_window <= 600:
            raise RuntimeError("BURST_WINDOW_SECONDS must be between 1 and 600.")

        return cls(
            discord_token=token,
            guild_id=guild_id,
            database_path=database_path,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
            ollama_model=os.getenv("OLLAMA_MODEL", "translategemma:12b").strip(),
            ollama_timeout_seconds=ollama_timeout,
            additional_guild_ids=additional_ids,
            default_off_guild_ids=default_off_ids,
            burst_window_seconds=burst_window,
        )
