from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import ValidationError

from marivo.analysis.calendar.model import Calendar
from marivo.analysis.errors import CalendarNotFoundError, CalendarPolicyError

_CALENDAR_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class CalendarCache:
    def __init__(self, project_root: Path) -> None:
        self._calendar_dir = Path(project_root) / ".marivo" / "calendar"
        self._calendar_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Calendar] = {}

    def get(self, name: str) -> Calendar:
        self._validate_calendar_name(name)

        cached = self._cache.get(name)
        if cached is not None:
            return cached

        calendar_file = self._calendar_dir / f"{name}.json"
        if not calendar_file.is_file():
            raise CalendarNotFoundError(
                message=f"calendar '{name}' was not found",
                details={
                    "kind": "CalendarNotFound",
                    "calendar_name": name,
                    "calendar_path": str(calendar_file),
                },
            )

        try:
            raw_text = calendar_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise CalendarPolicyError(
                message=f"calendar '{name}' file read failed",
                details={
                    "kind": "CalendarFileReadFailed",
                    "calendar_name": name,
                    "calendar_path": str(calendar_file),
                    "cause": str(exc),
                },
            ) from exc

        try:
            raw = json.loads(raw_text)
            calendar = Calendar.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            details: dict[str, object] = {
                "kind": "CalendarFileInvalid",
                "calendar_name": name,
                "calendar_path": str(calendar_file),
            }
            if isinstance(exc, ValidationError):
                details["validation_errors"] = exc.errors()
            else:
                details["cause"] = str(exc)
            raise CalendarPolicyError(
                message=f"calendar '{name}' file is invalid",
                details=details,
            ) from exc

        self._cache[name] = calendar
        return calendar

    def list_available(self) -> list[str]:
        if not self._calendar_dir.is_dir():
            return []
        return sorted(path.stem for path in self._calendar_dir.glob("*.json") if path.is_file())

    def _validate_calendar_name(self, name: str) -> None:
        reason: str | None = None
        if not name:
            reason = "empty"
        elif "/" in name or "\\" in name:
            reason = "path_separator"
        elif ".." in name:
            reason = "path_traversal"
        elif _CALENDAR_NAME_PATTERN.fullmatch(name) is None:
            reason = "invalid_character"
        if reason is not None:
            raise CalendarPolicyError(
                message=f"calendar name '{name}' is invalid",
                details={
                    "kind": "CalendarNameInvalid",
                    "calendar_name": name,
                    "reason": reason,
                },
            )
