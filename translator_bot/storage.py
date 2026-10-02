from __future__ import annotations

import sqlite3
import threading
import json
from pathlib import Path
from typing import Literal, cast


TranslationMode = Literal["auto", "en_to_ru", "ru_to_en", "off"]
VALID_MODES = frozenset({"auto", "en_to_ru", "ru_to_en", "off"})


class SettingsStore:
    """Small, thread-safe SQLite settings store."""

    def __init__(self, path: Path, channel_defaults: dict[int, bool] | None = None) -> None:
        self._channel_defaults = channel_defaults
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._connection:
            self._connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS feedback_flag_cleanup (feedback_id INTEGER PRIMARY KEY, next_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS feedback_history (feedback_id INTEGER PRIMARY KEY, record_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS feedback_reactions (
                    feedback_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
                    PRIMARY KEY (feedback_id, message_id)
                );
                CREATE TABLE IF NOT EXISTS mirrored_reactions (
                    translation_id INTEGER NOT NULL, source_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL, emoji TEXT NOT NULL, user_id INTEGER NOT NULL,
                    PRIMARY KEY (translation_id, emoji, user_id)
                );
                CREATE TABLE IF NOT EXISTS translation_retries (
                    source_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    next_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS message_batches (
                    source_message_id INTEGER PRIMARY KEY,
                    batch_message_id INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS message_batches_batch ON message_batches(batch_message_id);
                CREATE TABLE IF NOT EXISTS global_language_settings (
                    guild_id INTEGER PRIMARY KEY, languages TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS channel_language_settings (
                    guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, languages TEXT NOT NULL,
                    PRIMARY KEY (guild_id, channel_id)
                );
                CREATE TABLE IF NOT EXISTS temporary_translations (
                    message_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL, webhook_id INTEGER NOT NULL,
                    source_language TEXT NOT NULL, target_language TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS temporary_sources (
                    message_id INTEGER NOT NULL, source_id INTEGER NOT NULL,
                    PRIMARY KEY (message_id, source_id)
                );
                CREATE INDEX IF NOT EXISTS temporary_sources_source ON temporary_sources(source_id);
                CREATE TABLE IF NOT EXISTS translation_links (
                    translation_message_id INTEGER PRIMARY KEY,
                    source_message_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    webhook_id INTEGER NOT NULL,
                    source_language TEXT NOT NULL,
                    target_language TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS user_settings (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    mode TEXT NOT NULL CHECK(mode IN ('auto','en_to_ru','ru_to_en','off')),
                    PRIMARY KEY (guild_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS channel_settings (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                    PRIMARY KEY (guild_id, channel_id)
                );
                CREATE TABLE IF NOT EXISTS translation_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    source_message_id INTEGER NOT NULL,
                    translation_message_id INTEGER NOT NULL,
                    source_author_id INTEGER NOT NULL,
                    reporter_id INTEGER NOT NULL,
                    source_language TEXT NOT NULL,
                    target_language TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    translated_text TEXT NOT NULL,
                    corrected_text TEXT,
                    note TEXT,
                    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'reviewed', 'applied', 'rejected')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (translation_message_id, reporter_id)
                );
                """
            )

            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS translation_links_source ON translation_links(source_message_id)"
            )
            columns = {row["name"] for row in self._connection.execute("PRAGMA table_info(translation_links)")}
            if "status" not in columns:
                self._connection.execute(
                    "ALTER TABLE translation_links ADD COLUMN status TEXT NOT NULL DEFAULT 'complete'"
                )
            schema = self._connection.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'translation_feedback'"
            ).fetchone()[0]
            if "CHECK(source_language IN" in schema:
                upgraded = schema.replace('translation_feedback', 'translation_feedback_upgrade', 1)
                upgraded = upgraded.replace(" CHECK(source_language IN ('en', 'ru'))", '')
                upgraded = upgraded.replace(" CHECK(target_language IN ('en', 'ru'))", '')
                self._connection.execute(upgraded)
                self._connection.execute('INSERT INTO translation_feedback_upgrade SELECT * FROM translation_feedback')
                self._connection.execute('DROP TABLE translation_feedback')
                self._connection.execute('ALTER TABLE translation_feedback_upgrade RENAME TO translation_feedback')

    def get_channel_languages(self, guild_id: int, channel_id: int) -> tuple[str, ...]:
        with self._lock:
            row = self._connection.execute(
                'SELECT languages FROM channel_language_settings WHERE guild_id = ? AND channel_id = ?',
                (guild_id, channel_id),
            ).fetchone() or self._connection.execute(
                'SELECT languages FROM global_language_settings WHERE guild_id = ?', (guild_id,),
            ).fetchone()
        return tuple(json.loads(row[0])) if row else ('en', 'ru')

    def set_languages(self, guild_id: int, languages: tuple[str, ...], channel_id: int | None = None) -> None:
        from .languages import parse_languages
        languages = parse_languages(','.join(languages))
        with self._lock, self._connection:
            if channel_id is None:
                self._connection.execute('INSERT OR REPLACE INTO global_language_settings VALUES (?, ?)',
                                         (guild_id, json.dumps(languages)))
            else:
                self._connection.execute('INSERT OR REPLACE INTO channel_language_settings VALUES (?, ?, ?)',
                                         (guild_id, channel_id, json.dumps(languages)))

    def add_temporary(self, message_id: int, guild_id: int, channel_id: int, webhook_id: int,
                      source: str, target: str, expires_at: float, source_ids: list[int]) -> None:
        with self._lock, self._connection:
            self._connection.execute('INSERT OR REPLACE INTO temporary_translations VALUES (?, ?, ?, ?, ?, ?, ?)',
                                     (message_id, guild_id, channel_id, webhook_id, source, target, expires_at))
            self._connection.executemany('INSERT OR IGNORE INTO temporary_sources VALUES (?, ?)',
                                         [(message_id, source_id) for source_id in source_ids])

    def temporary_posts(self, *, expires_before: float | None = None, source_ids: set[int] | None = None) -> list[sqlite3.Row]:
        with self._lock:
            if source_ids:
                slots = ','.join('?' for _ in source_ids)
                return self._connection.execute(
                    f'SELECT DISTINCT t.* FROM temporary_translations t JOIN temporary_sources s ON s.message_id = t.message_id WHERE s.source_id IN ({slots})',
                    tuple(source_ids),
                ).fetchall()
            if expires_before is not None:
                return self._connection.execute('SELECT * FROM temporary_translations WHERE expires_at <= ?',
                                                (expires_before,)).fetchall()
            return self._connection.execute('SELECT * FROM temporary_translations').fetchall()

    def temporary_sources(self, message_id: int) -> list[int]:
        with self._lock:
            return [row[0] for row in self._connection.execute(
                'SELECT source_id FROM temporary_sources WHERE message_id = ? ORDER BY source_id', (message_id,),
            )]

    def remove_temporary(self, message_id: int) -> None:
        with self._lock, self._connection:
            self._connection.execute('DELETE FROM temporary_translations WHERE message_id = ?', (message_id,))
            self._connection.execute('DELETE FROM temporary_sources WHERE message_id = ?', (message_id,))

    def link_translation(self, translation_id: int, source_id: int, channel_id: int,
                         webhook_id: int, source_language: str, target_language: str,
                         status: str = "complete") -> None:
        # IDs and directions only; message text is stored only when explicitly flagged.
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO translation_links "
                "(translation_message_id, source_message_id, channel_id, webhook_id, source_language, target_language, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (translation_id, source_id, channel_id, webhook_id, source_language, target_language, status),
            )
            self._connection.execute("INSERT OR IGNORE INTO message_batches VALUES (?, ?)", (source_id, source_id))

    def add_batch_source(self, source_id: int, batch_id: int) -> None:
        with self._lock, self._connection:
            self._connection.execute("INSERT OR REPLACE INTO message_batches VALUES (?, ?)", (source_id, batch_id))

    def get_batch_sources(self, batch_id: int) -> list[int]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT source_message_id FROM message_batches WHERE batch_message_id = ? ORDER BY source_message_id",
                (batch_id,),
            ).fetchall()
        return [row["source_message_id"] for row in rows] or [batch_id]

    def get_batch_id(self, source_id: int) -> int | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT batch_message_id FROM message_batches WHERE source_message_id = ?", (source_id,),
            ).fetchone()
        return row[0] if row else None

    def get_translation_link(self, translation_id: int) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(
                "SELECT * FROM translation_links WHERE translation_message_id = ?", (translation_id,)
            ).fetchone()

    def remove_batch_sources(self, batch_id: int, deleted_ids: set[int]) -> list[int]:
        """Remove deleted originals and re-anchor surviving groups atomically."""
        with self._lock, self._connection:
            self._connection.executemany(
                "DELETE FROM message_batches WHERE source_message_id = ? AND batch_message_id = ?",
                [(source_id, batch_id) for source_id in deleted_ids],
            )
            remaining = [row[0] for row in self._connection.execute(
                "SELECT source_message_id FROM message_batches WHERE batch_message_id = ? ORDER BY source_message_id",
                (batch_id,),
            )]
            if remaining:
                self._connection.execute(
                    "UPDATE message_batches SET batch_message_id = ? WHERE batch_message_id = ?",
                    (remaining[0], batch_id),
                )
                self._connection.execute(
                    "UPDATE translation_links SET source_message_id = ? WHERE source_message_id = ?",
                    (remaining[0], batch_id),
                )
            return remaining

    def get_source_links(self, source_id: int) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(
                "SELECT * FROM translation_links WHERE source_message_id = "
                "COALESCE((SELECT batch_message_id FROM message_batches WHERE source_message_id = ?), ?) "
                "ORDER BY translation_message_id",
                (source_id, source_id),
            ).fetchall()

    def unlink_translation(self, translation_id: int) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM translation_links WHERE translation_message_id = ?", (translation_id,)
            )

    def get_user_mode(self, guild_id: int, user_id: int) -> TranslationMode:
        with self._lock:
            row = self._connection.execute(
                "SELECT mode FROM user_settings WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
        return cast(TranslationMode, row["mode"] if row else "auto")

    def set_user_mode(self, guild_id: int, user_id: int, mode: str) -> None:
        if mode not in VALID_MODES:
            raise ValueError(f"Unknown translation mode: {mode}")
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO user_settings (guild_id, user_id, mode) VALUES (?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET mode = excluded.mode
                """,
                (guild_id, user_id, mode),
            )

    def is_channel_enabled(self, guild_id: int, channel_id: int) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT enabled FROM channel_settings WHERE guild_id = ? AND channel_id = ?",
                (guild_id, channel_id),
            ).fetchone()
        default = self._channel_defaults.get(guild_id, False) if self._channel_defaults is not None else True
        return bool(row["enabled"]) if row else default

    def set_channel_enabled(self, guild_id: int, channel_id: int, enabled: bool) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO channel_settings (guild_id, channel_id, enabled) VALUES (?, ?, ?)
                ON CONFLICT(guild_id, channel_id) DO UPDATE SET enabled = excluded.enabled
                """,
                (guild_id, channel_id, int(enabled)),
            )

    def add_feedback(
        self,
        *,
        guild_id: int,
        channel_id: int,
        source_message_id: int,
        translation_message_id: int,
        source_author_id: int,
        reporter_id: int,
        source_language: str,
        target_language: str,
        source_text: str,
        translated_text: str,
        corrected_text: str | None,
        note: str | None,
        flag_message_id: int | None = None,
    ) -> bool:
        """Store a user-selected example. Return False when already flagged by them."""
        with self._lock, self._connection:
            previous = self._connection.execute('SELECT * FROM translation_feedback WHERE translation_message_id=? AND reporter_id=?',
                (translation_message_id, reporter_id)).fetchone()
            if previous and previous['status'] != 'open':
                self._connection.execute('INSERT OR IGNORE INTO feedback_history VALUES (?,?)',
                    (previous['id'], json.dumps(dict(previous), ensure_ascii=False)))
                self._connection.execute('DELETE FROM translation_feedback WHERE id=?', (previous['id'],))
                self._connection.execute('DELETE FROM feedback_reactions WHERE feedback_id=?', (previous['id'],))
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO translation_feedback (
                    guild_id, channel_id, source_message_id, translation_message_id,
                    source_author_id, reporter_id, source_language, target_language,
                    source_text, translated_text, corrected_text, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    channel_id,
                    source_message_id,
                    translation_message_id,
                    source_author_id,
                    reporter_id,
                    source_language,
                    target_language,
                    source_text,
                    translated_text,
                    corrected_text or None,
                    note or None,
                ),
            )
            report = self._connection.execute('SELECT id FROM translation_feedback WHERE translation_message_id=? AND reporter_id=?',
                (translation_message_id, reporter_id)).fetchone()
            self._connection.execute('INSERT OR IGNORE INTO feedback_reactions VALUES (?,?)',
                (report['id'], flag_message_id or translation_message_id))
        return cursor.rowcount == 1

    def feedback_flag_messages(self, feedback_id: int) -> list[int]:
        with self._lock:
            rows = self._connection.execute('SELECT message_id FROM feedback_reactions WHERE feedback_id=?', (feedback_id,)).fetchall()
            if rows:
                return [row[0] for row in rows]
            report = self._connection.execute('SELECT translation_message_id FROM translation_feedback WHERE id=?', (feedback_id,)).fetchone()
            return [report[0]] if report else []

    def open_feedback_count(self, guild_id: int) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM translation_feedback WHERE guild_id = ? AND status = 'open'",
                (guild_id,),
            ).fetchone()
        return int(row["count"])

    def remove_feedback(self, translation_message_id: int, reporter_id: int) -> bool:
        """Undo one reporter's flag when they remove their 🚩 reaction."""
        with self._lock, self._connection:
            reports = self._connection.execute("SELECT id FROM translation_feedback WHERE reporter_id=? AND status='open' "
                "AND (translation_message_id=? OR id IN (SELECT feedback_id FROM feedback_reactions WHERE message_id=?))",
                (reporter_id, translation_message_id, translation_message_id)).fetchall()
            removed = False
            for report in reports:
                self._connection.execute('DELETE FROM feedback_reactions WHERE feedback_id=? AND message_id=?', (report[0],translation_message_id))
                if not self._connection.execute('SELECT 1 FROM feedback_reactions WHERE feedback_id=?', (report[0],)).fetchone():
                    self._connection.execute('DELETE FROM translation_feedback WHERE id=?', (report[0],))
                    removed = True
        return removed

    def mirror_reaction(self, translation_id, source_id, channel_id, emoji, user_id):
        with self._lock, self._connection:
            self._connection.execute('INSERT OR IGNORE INTO mirrored_reactions VALUES (?,?,?,?,?)',
                (translation_id, source_id, channel_id, emoji, user_id))

    def unmirror_reaction(self, translation_id, emoji, user_id=None):
        with self._lock, self._connection:
            rows = self._connection.execute('SELECT DISTINCT source_id,channel_id,emoji FROM mirrored_reactions '
                'WHERE translation_id=? AND (? IS NULL OR emoji=?) AND (? IS NULL OR user_id=?)',
                (translation_id, emoji, emoji, user_id, user_id)).fetchall()
            self._connection.execute('DELETE FROM mirrored_reactions WHERE translation_id=? '
                'AND (? IS NULL OR emoji=?) AND (? IS NULL OR user_id=?)',
                (translation_id, emoji, emoji, user_id, user_id))
            return [dict(row) for row in rows if not self._connection.execute(
                'SELECT 1 FROM mirrored_reactions WHERE source_id=? AND channel_id=? AND emoji=?',
                (row['source_id'], row['channel_id'], row['emoji'])).fetchone()]

    def schedule_retry(self, source_id, guild_id, channel_id, next_at):
        with self._lock, self._connection:
            self._connection.execute('INSERT OR IGNORE INTO translation_retries VALUES (?,?,?,0,?)',
                (source_id, guild_id, channel_id, next_at))

    def due_retries(self, now):
        with self._lock:
            return [dict(row) for row in self._connection.execute(
                'SELECT * FROM translation_retries WHERE next_at<=? AND attempts<3', (now,))]

    def advance_retry(self, source_id, next_at):
        with self._lock, self._connection:
            self._connection.execute('UPDATE translation_retries SET attempts=attempts+1,next_at=? WHERE source_id=?',
                (next_at, source_id))

    def cancel_retry(self, source_id):
        with self._lock, self._connection:
            self._connection.execute('DELETE FROM translation_retries WHERE source_id=?', (source_id,))

    def failed_links(self):
        with self._lock:
            return [dict(row) for row in self._connection.execute("SELECT * FROM translation_links WHERE status='failed'")]

    def unfinished_links(self):
        with self._lock:
            return [dict(row) for row in self._connection.execute("SELECT * FROM translation_links WHERE status IN ('failed','pending')")]

    def corrected_flags_due(self, now):
        with self._lock:
            return [dict(r) for r in self._connection.execute("SELECT f.* FROM translation_feedback f LEFT JOIN feedback_flag_cleanup c "
                "ON c.feedback_id=f.id WHERE f.status='applied' AND f.corrected_text IS NOT NULL AND COALESCE(c.next_at,0)<=?", (now,))]

    def defer_flag_cleanup(self, feedback_id, next_at):
        with self._lock, self._connection:
            self._connection.execute('INSERT OR REPLACE INTO feedback_flag_cleanup VALUES (?,?)', (feedback_id, next_at))

    def close(self) -> None:
        with self._lock:
            self._connection.close()
