from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


BASE_DIR = Path(__file__).resolve().parent
TRUE_VALUES = {"1", "true", "yes", "on"}
SUPPORTED_DATABASE_ENGINES = {"sqlite", "postgresql"}
INSECURE_SESSION_SECRETS = {
    "replace-with-at-least-32-random-characters",
    "local-development-session-secret-change-me",
}


def _as_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


@dataclass(frozen=True)
class Settings:
    environment: str
    session_secret: str | None
    https_only: bool
    database_engine: str
    database_path: Path
    database_url: str | None
    database_host: str | None
    database_port: int
    database_name: str | None
    database_user: str | None
    database_password: str | None
    database_connect_timeout: int
    sqlite_busy_timeout_ms: int
    log_level: str

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def validate(self) -> None:
        if self.environment not in {"development", "test", "production"}:
            raise RuntimeError(
                "SAT_SCHEDULER_ENV must be development, test, or production."
            )
        if self.database_engine not in SUPPORTED_DATABASE_ENGINES:
            raise RuntimeError(
                "SAT_SCHEDULER_DB_ENGINE must be sqlite or postgresql."
            )
        if self.is_production:
            if (
                not self.session_secret
                or len(self.session_secret) < 32
                or self.session_secret in INSECURE_SESSION_SECRETS
            ):
                raise RuntimeError(
                    "SAT_SCHEDULER_SESSION_SECRET must be a unique, randomly generated "
                    "value containing at least 32 characters in production."
                )
            if not self.https_only:
                raise RuntimeError(
                    "SAT_SCHEDULER_HTTPS_ONLY must be enabled in production."
                )
        if self.database_engine == "postgresql":
            if self.database_url:
                if not self.database_url.startswith(("postgresql://", "postgres://")):
                    raise RuntimeError(
                        "SAT_SCHEDULER_DATABASE_URL (or DATABASE_URL) must be a "
                        "PostgreSQL connection URL."
                    )
            else:
                missing = [
                    name
                    for name, value in (
                        ("SAT_SCHEDULER_DB_HOST", self.database_host),
                        ("SAT_SCHEDULER_DB_NAME", self.database_name),
                        ("SAT_SCHEDULER_DB_USER", self.database_user),
                        ("SAT_SCHEDULER_DB_PASSWORD", self.database_password),
                    )
                    if not value
                ]
                if missing:
                    raise RuntimeError(
                        "Missing PostgreSQL settings: " + ", ".join(missing)
                    )


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if environ is None else environ
    environment = env.get("SAT_SCHEDULER_ENV", "development").strip().lower()
    database_url = (
        env.get("SAT_SCHEDULER_DATABASE_URL") or env.get("DATABASE_URL") or None
    )
    database_engine = env.get(
        "SAT_SCHEDULER_DB_ENGINE",
        "postgresql" if database_url else "sqlite",
    ).strip().lower()
    database_path = Path(
        env.get("SAT_SCHEDULER_DATABASE_PATH", str(BASE_DIR / "scheduler.db"))
    ).expanduser()
    default_https_only = environment == "production"

    try:
        database_port = int(env.get("SAT_SCHEDULER_DB_PORT", "5432"))
        database_connect_timeout = int(
            env.get("SAT_SCHEDULER_DB_CONNECT_TIMEOUT", "10")
        )
        sqlite_busy_timeout_ms = int(
            env.get("SAT_SCHEDULER_SQLITE_BUSY_TIMEOUT_MS", "30000")
        )
    except ValueError as exc:
        raise RuntimeError("Database timeout and port settings must be integers.") from exc

    return Settings(
        environment=environment,
        session_secret=env.get("SAT_SCHEDULER_SESSION_SECRET") or None,
        https_only=_as_bool(
            env.get("SAT_SCHEDULER_HTTPS_ONLY"), default=default_https_only
        ),
        database_engine=database_engine,
        database_path=database_path,
        database_url=database_url,
        database_host=env.get("SAT_SCHEDULER_DB_HOST") or None,
        database_port=database_port,
        database_name=env.get("SAT_SCHEDULER_DB_NAME") or None,
        database_user=env.get("SAT_SCHEDULER_DB_USER") or None,
        database_password=env.get("SAT_SCHEDULER_DB_PASSWORD") or None,
        database_connect_timeout=max(1, database_connect_timeout),
        sqlite_busy_timeout_ms=max(1000, sqlite_busy_timeout_ms),
        log_level=env.get("SAT_SCHEDULER_LOG_LEVEL", "INFO").strip().upper(),
    )


SETTINGS = load_settings()
