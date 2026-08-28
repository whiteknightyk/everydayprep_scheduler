from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from settings import SETTINGS

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # SQLite-only local installations remain supported.
    psycopg = None
    dict_row = None


DB_ENGINE = SETTINGS.database_engine
DB_PATH = SETTINGS.database_path
INTEGRITY_ERRORS = (
    (sqlite3.IntegrityError, psycopg.IntegrityError)
    if psycopg is not None
    else (sqlite3.IntegrityError,)
)

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner','admin','instructor','student')),
    login_id TEXT COLLATE NOCASE UNIQUE,
    password_hash TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    timezone TEXT NOT NULL DEFAULT 'Asia/Tokyo',
    email TEXT,
    last_login_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS availability (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    UNIQUE(user_id, weekday, start_time, end_time)
);

CREATE TABLE IF NOT EXISTS exceptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    start_utc TEXT NOT NULL,
    end_utc TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instructor_id INTEGER NOT NULL REFERENCES users(id),
    student_id INTEGER NOT NULL REFERENCES users(id),
    subject TEXT NOT NULL DEFAULT 'SAT',
    duration_minutes INTEGER NOT NULL DEFAULT 60,
    status TEXT NOT NULL DEFAULT 'draft',
    confirmed_start_utc TEXT,
    confirmed_end_utc TEXT,
    meeting_url TEXT,
    zoom_meeting_id TEXT,
    zoom_password TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    start_utc TEXT NOT NULL,
    end_utc TEXT NOT NULL,
    proposed_by TEXT CHECK (proposed_by IN ('instructor','student','admin')),
    instructor_response TEXT NOT NULL DEFAULT 'pending' CHECK (instructor_response IN ('pending','accept','reject')),
    student_response TEXT NOT NULL DEFAULT 'pending' CHECK (student_response IN ('pending','accept','reject')),
    expires_at_utc TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(lesson_id, start_utc)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_id INTEGER REFERENCES lessons(id) ON DELETE CASCADE,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

POSTGRES_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id BIGSERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('owner','admin','instructor','student')),
        login_id TEXT UNIQUE,
        password_hash TEXT,
        is_active SMALLINT NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
        timezone TEXT NOT NULL DEFAULT 'Asia/Tokyo',
        email TEXT,
        last_login_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS availability (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        UNIQUE(user_id, weekday, start_time, end_time)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS exceptions (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        start_utc TEXT NOT NULL,
        end_utc TEXT NOT NULL,
        reason TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS lessons (
        id BIGSERIAL PRIMARY KEY,
        instructor_id BIGINT NOT NULL REFERENCES users(id),
        student_id BIGINT NOT NULL REFERENCES users(id),
        subject TEXT NOT NULL DEFAULT 'SAT',
        duration_minutes INTEGER NOT NULL DEFAULT 60,
        status TEXT NOT NULL DEFAULT 'draft',
        confirmed_start_utc TEXT,
        confirmed_end_utc TEXT,
        meeting_url TEXT,
        zoom_meeting_id TEXT,
        zoom_password TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS candidates (
        id BIGSERIAL PRIMARY KEY,
        lesson_id BIGINT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
        start_utc TEXT NOT NULL,
        end_utc TEXT NOT NULL,
        proposed_by TEXT CHECK (proposed_by IN ('instructor','student','admin')),
        instructor_response TEXT NOT NULL DEFAULT 'pending'
            CHECK (instructor_response IN ('pending','accept','reject')),
        student_response TEXT NOT NULL DEFAULT 'pending'
            CHECK (student_response IN ('pending','accept','reject')),
        expires_at_utc TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(lesson_id, start_utc)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_logs (
        id BIGSERIAL PRIMARY KEY,
        lesson_id BIGINT REFERENCES lessons(id) ON DELETE CASCADE,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        detail TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS lessons_instructor_id_idx ON lessons(instructor_id)",
    "CREATE INDEX IF NOT EXISTS lessons_student_id_idx ON lessons(student_id)",
    "CREATE INDEX IF NOT EXISTS candidates_lesson_id_idx ON candidates(lesson_id)",
    "CREATE INDEX IF NOT EXISTS exceptions_user_id_idx ON exceptions(user_id)",
    "ALTER TABLE lessons ADD COLUMN IF NOT EXISTS zoom_meeting_id TEXT",
    "ALTER TABLE lessons ADD COLUMN IF NOT EXISTS zoom_password TEXT",
    "ALTER TABLE candidates ADD COLUMN IF NOT EXISTS proposed_by TEXT",
)


def _postgresql_sql(sql: str, *, return_insert_id: bool = True) -> tuple[str, bool]:
    """Translate the small SQLite SQL subset used by the application."""
    translated = sql.replace("?", "%s")
    normalized = translated.lstrip().upper()
    if normalized.rstrip("; ") == "BEGIN IMMEDIATE":
        # SQLite uses BEGIN IMMEDIATE to serialize the one-time owner setup.
        # PostgreSQL does not support the IMMEDIATE modifier. SERIALIZABLE
        # preserves the one-time setup invariant if two requests race.
        return "BEGIN ISOLATION LEVEL SERIALIZABLE", False
    ignore_conflict = normalized.startswith("INSERT OR IGNORE INTO")
    if ignore_conflict:
        translated = translated.replace("INSERT OR IGNORE INTO", "INSERT INTO", 1)
        translated = translated.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    returns_id = (
        return_insert_id
        and normalized.startswith("INSERT INTO")
        and not ignore_conflict
        and " RETURNING " not in normalized
    )
    if returns_id:
        translated = translated.rstrip().rstrip(";") + " RETURNING id"
    return translated, returns_id


class PostgresCursor:
    def __init__(self, cursor: Any, *, returns_id: bool = False):
        self._cursor = cursor
        self.lastrowid = None
        if returns_id:
            row = cursor.fetchone()
            if row:
                self.lastrowid = row["id"]

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


class PostgresConnection:
    def __init__(self, connection: Any):
        self._connection = connection

    def execute(self, sql: str, params: tuple | list = ()) -> PostgresCursor:
        translated, returns_id = _postgresql_sql(sql)
        cursor = self._connection.execute(translated, params)
        return PostgresCursor(cursor, returns_id=returns_id)

    def executemany(self, sql: str, params: list | tuple) -> PostgresCursor:
        translated, _ = _postgresql_sql(sql, return_insert_id=False)
        cursor = self._connection.cursor()
        cursor.executemany(translated, params)
        return PostgresCursor(cursor)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


def connect():
    if DB_ENGINE == "postgresql":
        if psycopg is None:
            raise RuntimeError(
                "PostgreSQL support requires psycopg. Install requirements.txt."
            )
        connection_options = {
            "connect_timeout": SETTINGS.database_connect_timeout,
            "row_factory": dict_row,
        }
        if SETTINGS.database_url:
            connection = psycopg.connect(
                SETTINGS.database_url,
                **connection_options,
            )
        else:
            connection = psycopg.connect(
                host=SETTINGS.database_host,
                port=SETTINGS.database_port,
                dbname=SETTINGS.database_name,
                user=SETTINGS.database_user,
                password=SETTINGS.database_password,
                **connection_options,
            )
        return PostgresConnection(connection)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(
        DB_PATH,
        timeout=SETTINGS.sqlite_busy_timeout_ms / 1000,
    )
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(f"PRAGMA busy_timeout = {SETTINGS.sqlite_busy_timeout_ms}")
    return con


@contextmanager
def get_db():
    con = connect()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    if DB_ENGINE == "postgresql":
        with closing(connect()) as con:
            for statement in POSTGRES_SCHEMA:
                con.execute(statement)
            con.commit()
        return

    with closing(connect()) as con:
        con.executescript(SCHEMA)
        # CREATE TABLE IF NOT EXISTS does not add columns to an existing SQLite
        # database, so migrate databases created by earlier MVP versions.
        user_columns = {
            row["name"] for row in con.execute("PRAGMA table_info(users)").fetchall()
        }
        user_table_sql = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()["sql"]
        required_user_columns = {
            "login_id",
            "password_hash",
            "is_active",
            "last_login_at",
        }
        if not required_user_columns.issubset(user_columns) or "'owner'" not in user_table_sql:
            # SQLite cannot alter a CHECK constraint, so rebuild the users table.
            # The credential columns intentionally stay nullable for records created
            # before authentication was introduced; those users cannot sign in until
            # an administrator replaces/recreates the account.
            con.commit()
            con.execute("PRAGMA foreign_keys = OFF")
            con.execute("DROP TABLE IF EXISTS users_with_auth")
            con.execute(
                """
                CREATE TABLE users_with_auth (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL
                        CHECK (role IN ('owner','admin','instructor','student')),
                    login_id TEXT COLLATE NOCASE UNIQUE,
                    password_hash TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
                    timezone TEXT NOT NULL DEFAULT 'Asia/Tokyo',
                    email TEXT,
                    last_login_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            login_id = "login_id" if "login_id" in user_columns else "NULL"
            password_hash = (
                "password_hash" if "password_hash" in user_columns else "NULL"
            )
            is_active = "is_active" if "is_active" in user_columns else "1"
            last_login_at = (
                "last_login_at" if "last_login_at" in user_columns else "NULL"
            )
            con.execute(
                f"""
                INSERT INTO users_with_auth(
                    id, name, role, login_id, password_hash, is_active,
                    timezone, email, last_login_at, created_at
                )
                SELECT id, name, role, {login_id}, {password_hash}, {is_active},
                       timezone, email, {last_login_at}, created_at
                FROM users
                """
            )
            con.execute("DROP TABLE users")
            con.execute("ALTER TABLE users_with_auth RENAME TO users")
            con.commit()
            con.execute("PRAGMA foreign_keys = ON")

        lesson_columns = {
            row["name"] for row in con.execute("PRAGMA table_info(lessons)").fetchall()
        }
        if "zoom_meeting_id" not in lesson_columns:
            con.execute("ALTER TABLE lessons ADD COLUMN zoom_meeting_id TEXT")
        if "zoom_password" not in lesson_columns:
            con.execute("ALTER TABLE lessons ADD COLUMN zoom_password TEXT")

        candidate_columns = {
            row["name"] for row in con.execute("PRAGMA table_info(candidates)").fetchall()
        }
        if "proposed_by" not in candidate_columns:
            con.execute(
                "ALTER TABLE candidates ADD COLUMN proposed_by TEXT "
                "CHECK (proposed_by IN ('instructor','student','admin'))"
            )
        else:
            candidate_table_sql = con.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='candidates'"
            ).fetchone()["sql"]
            if "'admin'" not in candidate_table_sql:
                con.execute("ALTER TABLE candidates RENAME TO candidates_before_admin")
                con.execute(
                    """
                    CREATE TABLE candidates (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
                        start_utc TEXT NOT NULL,
                        end_utc TEXT NOT NULL,
                        proposed_by TEXT CHECK (proposed_by IN ('instructor','student','admin')),
                        instructor_response TEXT NOT NULL DEFAULT 'pending'
                            CHECK (instructor_response IN ('pending','accept','reject')),
                        student_response TEXT NOT NULL DEFAULT 'pending'
                            CHECK (student_response IN ('pending','accept','reject')),
                        expires_at_utc TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(lesson_id, start_utc)
                    )
                    """
                )
                con.execute(
                    """
                    INSERT INTO candidates(
                        id, lesson_id, start_utc, end_utc, proposed_by,
                        instructor_response, student_response, expires_at_utc, created_at
                    )
                    SELECT id, lesson_id, start_utc, end_utc, proposed_by,
                           instructor_response, student_response, expires_at_utc, created_at
                    FROM candidates_before_admin
                    """
                )
                con.execute("DROP TABLE candidates_before_admin")
        con.commit()
