from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_ENV_KEYS = (
    "MINIMAX_BASE_URL",
    "MINIMAX_API_KEY",
    "MINIMAX_MODEL",
    "SDD_DB_PATH",
    "POWERCONTEXT_SERVER_URL",
    "POWERCONTEXT_SCOPE_ID",
    "POWERCONTEXT_TASK_ID",
)


@dataclass(frozen=True)
class Settings:
    minimax_base_url: str
    minimax_api_key: str
    minimax_model: str
    db_path: Path
    powercontext_server_url: str
    powercontext_scope_id: str
    powercontext_task_id: str


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def load_settings(env_file: str | None = None) -> Settings:
    candidates: list[Path] = []
    if env_file:
        candidates.append(Path(env_file).expanduser())
    if os.environ.get("SDD_ENV_FILE"):
        candidates.append(Path(os.environ["SDD_ENV_FILE"]).expanduser())
    candidates.append(PROJECT_ROOT / ".env")

    values: dict[str, str] = {}
    for candidate in candidates:
        for key, value in _parse_env_file(candidate).items():
            values.setdefault(key, value)
    values.update({key: os.environ[key] for key in _ENV_KEYS if key in os.environ})

    db_path = Path(values.get("SDD_DB_PATH") or ".runtime/seekdb").expanduser()
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    return Settings(
        minimax_base_url=values.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/"),
        minimax_api_key=values.get("MINIMAX_API_KEY", ""),
        minimax_model=values.get("MINIMAX_MODEL", ""),
        db_path=db_path,
        powercontext_server_url=values.get("POWERCONTEXT_SERVER_URL", "http://127.0.0.1:8010").rstrip("/"),
        powercontext_scope_id=values.get("POWERCONTEXT_SCOPE_ID", ""),
        powercontext_task_id=values.get("POWERCONTEXT_TASK_ID", "tasklite-min-priority"),
    )
