from __future__ import annotations

import tomllib
from pathlib import Path

from app.contracts.errors import ErrorCode, ValidationError


class TomlRuntimeConfig:
    """TOML-based RuntimeConfig for local mode."""

    def __init__(self, config_path: Path) -> None:
        self._path = config_path
        self._data: dict[str, object] | None = None

    def get(self, key: str) -> str | None:
        data = self._load()
        parts = key.split(".")
        value: object = data
        for part in parts:
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return str(value) if value is not None else None

    def _load(self) -> dict[str, object]:
        if self._data is not None:
            return self._data
        if not self._path.is_file():
            self._data = {}
            return self._data
        try:
            with self._path.open("rb") as f:
                self._data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ValidationError(
                code=ErrorCode.VALIDATION,
                message=f"Configuration file '{self._path}' is invalid: {e}",
            ) from e
        return self._data
