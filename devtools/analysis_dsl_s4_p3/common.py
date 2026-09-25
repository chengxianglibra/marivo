"""Small public-only helpers for standalone journey scripts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

import marivo.analysis as mv
import marivo.semantic as ms


def open_project(root: Path, name: str) -> mv.Session:
    """Load authored semantics and create one named public Session."""
    os.environ["MARIVO_PROJECT_ROOT"] = str(root.resolve())
    ms.load(workspace_dir=root)
    return mv.session.get_or_create(name, report_timezone="UTC")


def rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Return JSON-safe rows without changing Marivo's DataFrame."""
    payload: list[dict[str, Any]] = json.loads(frame.to_json(orient="records", date_format="iso"))
    return payload


def emit(payload: dict[str, Any]) -> None:
    """Write a machine-readable result for the independent controller."""
    print(json.dumps(payload, sort_keys=True, allow_nan=False))
