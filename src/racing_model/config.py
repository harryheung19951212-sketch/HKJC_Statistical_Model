from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None


@dataclass(frozen=True)
class Settings:
    db_path: Path | str
    openai_api_key: str | None
    openai_model: str
    codex_cli_command: str
    codex_model: str
    codex_reasoning_effort: str
    codex_timeout_seconds: int
    user_agent: str
    request_delay_seconds: float
    odds_provider: str
    hkjc_graphql_url: str
    hkjc_mqtt_host: str
    hkjc_mqtt_port: int
    hkjc_mqtt_username: str
    hkjc_mqtt_password: str
    hkjc_mqtt_wait_seconds: float


def get_settings() -> Settings:
    if load_dotenv:
        load_dotenv(override=True)
    else:
        load_simple_env(Path(".env"), override=True)

    return Settings(
        db_path=os.getenv("DATABASE_URL") or Path(os.getenv("RACING_DB_PATH", "data/racing.db")),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.5"),
        codex_cli_command=os.getenv("RACING_CODEX_CLI", "codex"),
        codex_model=os.getenv("RACING_CODEX_MODEL", "gpt-5.5"),
        codex_reasoning_effort=os.getenv("RACING_CODEX_REASONING_EFFORT", "high"),
        codex_timeout_seconds=int(os.getenv("RACING_CODEX_TIMEOUT_SECONDS", "180")),
        user_agent=os.getenv("RACING_USER_AGENT", "RacingModelResearchBot/0.1"),
        request_delay_seconds=float(os.getenv("RACING_REQUEST_DELAY_SECONDS", "2.0")),
        odds_provider=os.getenv("RACING_ODDS_PROVIDER", "auto"),
        hkjc_graphql_url=os.getenv("HKJC_GRAPHQL_URL", "https://info.cld.hkjc.com/graphql/base/"),
        hkjc_mqtt_host=os.getenv("HKJC_MQTT_HOST", "ueb.hkjc.com"),
        hkjc_mqtt_port=int(os.getenv("HKJC_MQTT_PORT", "52443")),
        hkjc_mqtt_username=os.getenv("HKJC_MQTT_USERNAME", ""),
        hkjc_mqtt_password=os.getenv("HKJC_MQTT_PASSWORD", ""),
        hkjc_mqtt_wait_seconds=float(os.getenv("HKJC_MQTT_WAIT_SECONDS", "8")),
    )


def load_simple_env(path: Path, override: bool = False) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and (override or key not in os.environ):
            os.environ[key] = value


def display_database_target(target: Path | str) -> str:
    value = str(target)
    if not value.startswith(("postgres://", "postgresql://")):
        return value
    parts = urlsplit(value)
    if not parts.password:
        return value
    username = parts.username or ""
    hostname = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    auth = f"{username}:***@" if username else ""
    return urlunsplit((parts.scheme, f"{auth}{hostname}{port}", parts.path, parts.query, parts.fragment))
