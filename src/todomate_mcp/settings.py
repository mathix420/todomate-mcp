"""Local application configuration."""

import os
from pathlib import Path
from typing import Mapping


def environment_path(environ: Mapping[str, str] | None = None) -> Path:
    return Path((os.environ if environ is None else environ).get("TODOMATE_ENV_FILE", ".env"))


def load_firebase_api_key(
    path: Path | None = None, environ: Mapping[str, str] | None = None
) -> str | None:
    return load_environment(path, environ).get("TODOMATE_FIREBASE_API_KEY")


def load_environment(path: Path | None = None, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    values = _read_dotenv(path or environment_path(environ))
    values.update({key: value for key, value in (environ or os.environ).items() if value})
    return values


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and key and not key.startswith("#"):
            values[key] = value.strip().strip("\"'")
    return values
