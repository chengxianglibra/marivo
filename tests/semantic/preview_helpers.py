"""Shared scoped semantic preview projects and bounded authoring scopes."""

from __future__ import annotations

import marivo.datasource as md
from marivo.datasource.source import AuthoringScope


def default_scope(*, max_rows: int = 100) -> AuthoringScope:
    return md.unpruned(max_rows=max_rows, timeout_seconds=30)
