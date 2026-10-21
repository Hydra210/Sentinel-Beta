"""Persistent storage for Sentinel.

Production uses PostgreSQL or a MySQL-compatible database supplied through
DATABASE_URL. SQLite support is restricted to isolated tests; the application
never falls back to a local sentinel.db file.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, unquote, urlparse

import pymysql
from pymysql.cursors import DictCursor
import psycopg
from psycopg.rows import dict_row


class StorageError(RuntimeError):
    pass


class Storage:
    def __init__(self, database_url: str | None = None):
        configured_url = database_url or os.environ.get("DATABASE_URL", "")
        if not configured_url:
            # The original Render service used a persistent disk with DB_PATH.
            # Keep that disk usable, but start Sentinel in a new file so legacy
            # profiles and incompatible tables are never imported.
            legacy_path = os.environ.get("DB_PATH", "")
            if legacy_path:
                fresh_path = os.environ.get("SENTINEL_DB_PATH", "")
                if not fresh_path:
                    fresh_path = str(Path(legacy_path).with_name("sentinel-fresh.db"))
                configured_url = f"sqlite://{fresh_path}"
        self.database_url = configured_url
        self.is_sqlite = self.database_url.startswith("sqlite://")
        if not self.database_url:
            self.kind = "unconfigured"
        elif self.is_sqlite:
            self.kind = "sqlite"
        elif self.database_url.startswith(("mysql://", "mysql+pymysql://")):
            self.kind = "mysql"
        elif self.database_url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
            self.kind = "postgres"
        else:
            raise StorageError("DATABASE_URL must be a PostgreSQL or MySQL-compatible URL")

    @property
    def configured(self) -> bool:
        return self.kind in {"mysql", "postgres", "sqlite"}

    def _connect(self):
        if self.kind == "sqlite":
            raw_path = self.database_url.removeprefix("sqlite://")
            path = ":memory:" if raw_path in {"", "/:memory:"} else raw_path
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            return conn
        if self.kind == "postgres":
            url = self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
            return psycopg.connect(url, row_factory=dict_row, connect_timeout=10)
        if self.kind != "mysql":
            raise StorageError("DATABASE_URL is not configured")
        parsed = urlparse(self.database_url.replace("mysql+pymysql://", "mysql://", 1))
        if not parsed.hostname or not parsed.path.strip("/"):
            raise StorageError("DATABASE_URL is missing a database host or database name")
        query = parse_qs(parsed.query)
        local_host = parsed.hostname in {"localhost", "127.0.0.1"}
        ssl_disabled = query.get("ssl", [""])[0].lower() in {"0", "false", "disabled"}
        ssl: dict[str, Any] | None = None if (local_host and ssl_disabled) else {"check_hostname": False}
        return pymysql.connect(
            host=parsed.hostname,
            port=parsed.port or 3306,
            user=unquote(parsed.username or ""),
            password=unquote(parsed.password or ""),
            database=parsed.path.lstrip("/"),
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
            ssl=ssl,
            connect_timeout=10,
            read_timeout=20,
            write_timeout=20,
        )

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.kind in {"mysql", "postgres"} else sql

    @contextmanager
    def connection(self) -> Iterator[Any]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self.connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(self._sql(sql), params)
                return cur.rowcount
            finally:
                cur.close()

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(self._sql(sql), params)
                row = cur.fetchone()
                return dict(row) if row else None
            finally:
                cur.close()

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(self._sql(sql), params)
                return [dict(row) for row in cur.fetchall()]
            finally:
                cur.close()

    def scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        row = self.fetch_one(sql, params)
        return next(iter(row.values())) if row else None

    def init_schema(self) -> None:
        if not self.configured:
            return
        timestamp = "TIMESTAMP" if self.kind == "postgres" else "DATETIME"
        ddl = [
            f"""CREATE TABLE IF NOT EXISTS users (
                id VARCHAR(255) PRIMARY KEY,
                email VARCHAR(320) NOT NULL DEFAULT '',
                name VARCHAR(255) NOT NULL DEFAULT '',
                avatar_url TEXT NULL,
                created_at {timestamp} NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at {timestamp} NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            f"""CREATE TABLE IF NOT EXISTS sessions (
                token_hash CHAR(64) PRIMARY KEY,
                user_id VARCHAR(255) NOT NULL,
                access_token_enc TEXT NULL,
                refresh_token_enc TEXT NULL,
                expires_at {timestamp} NOT NULL,
                created_at {timestamp} NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at {timestamp} NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS sentinel_groups (
                id VARCHAR(64) NOT NULL,
                user_id VARCHAR(255) NOT NULL,
                name VARCHAR(255) NOT NULL DEFAULT '',
                added_at DOUBLE PRECISION NOT NULL,
                PRIMARY KEY (id, user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS history (
                id VARCHAR(128) PRIMARY KEY,
                user_id VARCHAR(255) NOT NULL,
                username VARCHAR(255) NOT NULL DEFAULT '',
                display_name VARCHAR(255) NOT NULL DEFAULT '',
                roblox_user_id VARCHAR(64) NOT NULL DEFAULT '',
                audio_name VARCHAR(500) NOT NULL DEFAULT '',
                audio_id VARCHAR(64) NOT NULL DEFAULT '',
                asset_type VARCHAR(32) NOT NULL DEFAULT 'Audio',
                group_id VARCHAR(64) NOT NULL DEFAULT '',
                group_name VARCHAR(255) NOT NULL DEFAULT '',
                occurred_at VARCHAR(64) NOT NULL DEFAULT '',
                dm_status VARCHAR(32) NOT NULL DEFAULT 'n/a',
                archived BOOLEAN NOT NULL DEFAULT TRUE
            )""",
            """CREATE TABLE IF NOT EXISTS config (
                user_id VARCHAR(255) NOT NULL,
                config_key VARCHAR(100) NOT NULL,
                config_value TEXT NOT NULL,
                PRIMARY KEY (user_id, config_key)
            )""",
        ]
        if self.kind == "sqlite":
            ddl[1] = """CREATE TABLE IF NOT EXISTS sessions (
                token_hash CHAR(64) PRIMARY KEY, user_id VARCHAR(255) NOT NULL,
                access_token_enc TEXT NULL, refresh_token_enc TEXT NULL,
                expires_at DATETIME NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
            ddl[2] = """CREATE TABLE IF NOT EXISTS sentinel_groups (
                id VARCHAR(64) NOT NULL, user_id VARCHAR(255) NOT NULL,
                name VARCHAR(255) NOT NULL DEFAULT '', added_at DOUBLE NOT NULL,
                PRIMARY KEY (id, user_id)
            )"""
            ddl[3] = """CREATE TABLE IF NOT EXISTS history (
                id VARCHAR(128) PRIMARY KEY, user_id VARCHAR(255) NOT NULL,
                username VARCHAR(255) NOT NULL DEFAULT '', display_name VARCHAR(255) NOT NULL DEFAULT '',
                roblox_user_id VARCHAR(64) NOT NULL DEFAULT '', audio_name VARCHAR(500) NOT NULL DEFAULT '',
                audio_id VARCHAR(64) NOT NULL DEFAULT '', asset_type VARCHAR(32) NOT NULL DEFAULT 'Audio',
                group_id VARCHAR(64) NOT NULL DEFAULT '', group_name VARCHAR(255) NOT NULL DEFAULT '',
                occurred_at VARCHAR(64) NOT NULL DEFAULT '', dm_status VARCHAR(32) NOT NULL DEFAULT 'n/a',
                archived BOOLEAN NOT NULL DEFAULT TRUE
            )"""
        with self.connection() as conn:
            cur = conn.cursor()
            try:
                for statement in ddl:
                    cur.execute(self._sql(statement))
                indexes = [
                    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)",
                    "CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at)",
                    "CREATE INDEX IF NOT EXISTS idx_groups_user_added ON sentinel_groups(user_id, added_at)",
                    "CREATE INDEX IF NOT EXISTS idx_history_user_time ON history(user_id, occurred_at)",
                    "CREATE INDEX IF NOT EXISTS idx_history_user_asset ON history(user_id, audio_id)",
                ]
                if self.kind != "mysql":
                    for statement in indexes:
                        cur.execute(self._sql(statement))
                else:
                    # MySQL accepts indexes inline in older schemas; create them separately
                    # only when using this new portable definition.
                    for statement in indexes:
                        try:
                            cur.execute(statement)
                        except Exception:
                            pass
            finally:
                cur.close()

    def upsert_user(self, user_id: str, email: str, name: str, avatar_url: str = "") -> None:
        if self.kind == "mysql":
            sql = """INSERT INTO users (id,email,name,avatar_url) VALUES (?,?,?,?)
                     ON DUPLICATE KEY UPDATE email=VALUES(email), name=VALUES(name),
                     avatar_url=VALUES(avatar_url), updated_at=CURRENT_TIMESTAMP"""
        else:
            sql = """INSERT INTO users (id,email,name,avatar_url) VALUES (?,?,?,?)
                     ON CONFLICT(id) DO UPDATE SET email=excluded.email, name=excluded.name,
                     avatar_url=excluded.avatar_url, updated_at=CURRENT_TIMESTAMP"""
        self.execute(sql, (user_id, email, name, avatar_url))

    def upsert_config(self, user_id: str, key: str, value: Any) -> None:
        encoded = json.dumps(value, separators=(",", ":"))
        if self.kind == "mysql":
            sql = """INSERT INTO config (user_id,config_key,config_value) VALUES (?,?,?)
                     ON DUPLICATE KEY UPDATE config_value=VALUES(config_value)"""
        else:
            sql = """INSERT INTO config (user_id,config_key,config_value) VALUES (?,?,?)
                     ON CONFLICT(user_id,config_key) DO UPDATE SET config_value=excluded.config_value"""
        self.execute(sql, (user_id, key, encoded))

    def upsert_group(self, group_id: str, user_id: str, name: str, added_at: float) -> None:
        if self.kind == "mysql":
            sql = """INSERT INTO sentinel_groups (id,user_id,name,added_at) VALUES (?,?,?,?)
                     ON DUPLICATE KEY UPDATE name=VALUES(name), added_at=VALUES(added_at)"""
        else:
            sql = """INSERT INTO sentinel_groups (id,user_id,name,added_at) VALUES (?,?,?,?)
                     ON CONFLICT(id,user_id) DO UPDATE SET name=excluded.name, added_at=excluded.added_at"""
        self.execute(sql, (group_id, user_id, name, added_at))

    def insert_history_ignore(self, record: dict[str, Any]) -> None:
        columns = list(record)
        placeholders = ",".join("?" for _ in columns)
        column_names = ",".join(columns)
        if self.kind == "mysql":
            sql = f"INSERT IGNORE INTO history ({column_names}) VALUES ({placeholders})"
        elif self.kind == "postgres":
            sql = f"INSERT INTO history ({column_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
        else:
            sql = f"INSERT OR IGNORE INTO history ({column_names}) VALUES ({placeholders})"
        self.execute(sql, tuple(record[column] for column in columns))


def utcnow_string() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
