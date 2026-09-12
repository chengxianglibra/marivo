"""Project-authored target selection and exact, deferred credential references."""

from __future__ import annotations

import os
import re
from pathlib import Path

from marivo._compat import tomllib
from marivo.analysis.materialization.targets import (
    LocalTarget,
    MaterializationTarget,
    ObjectTarget,
    S3Access,
    selection_error,
)
from marivo.config import PROJECT_MANIFEST

_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_FIELDS = frozenset(
    (
        "endpoint_url",
        "bucket",
        "region",
        "access_key_id_env",
        "secret_access_key_env",
        "session_token_env",
    )
)


def _table(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        selection_error("an analysis storage TOML table", "invalid project configuration shape")
    return {str(key): item for key, item in value.items()}


def _analysis(root: Path) -> dict[str, object]:
    manifest = root / PROJECT_MANIFEST
    try:
        with manifest.open("rb") as stream:
            document: object = tomllib.load(stream)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        selection_error(
            "a readable valid marivo.toml", "unreadable or malformed project configuration"
        )
    return _table(_table(document).get("analysis", {}))


def configured_target(root: Path) -> MaterializationTarget:
    """Resolve only the selected target, after a new Run has been admitted."""
    analysis = _analysis(root)
    selection = analysis.get("storage", "local")
    if selection == "local":
        return LocalTarget()
    if not isinstance(selection, str) or not selection.startswith("object:"):
        selection_error("analysis.storage = local or object:<name>", "unsupported storage target")
    reference = selection.removeprefix("object:")
    if not _REFERENCE.fullmatch(reference) or reference not in _table(
        analysis.get("object_stores", {})
    ):
        selection_error(
            "one named analysis.object_stores table", "missing or invalid selected object store"
        )
    return ObjectTarget(reference)


def configured_access(root: Path, reference: str) -> S3Access | None:
    """Resolve only the selected receipt's binding; never consult SDK defaults."""
    stores = _table(_analysis(root).get("object_stores", {}))
    if reference not in stores:
        return None
    fields = _table(stores[reference])
    if fields.keys() - _FIELDS:
        selection_error(
            "object location fields and credential *_env references", "unknown object store fields"
        )

    def text(name: str, default: str | None = None) -> str:
        value = fields.get(name, default)
        if not isinstance(value, str) or not value:
            selection_error(
                "complete nonempty object configuration fields", "missing or invalid object field"
            )
        return value

    def secret(name: str) -> str:
        value = os.environ.get(text(name))
        if not value:
            selection_error(
                "the selected credential environment reference to be set",
                "missing current object credentials",
            )
        return value

    return S3Access(
        reference,
        text("endpoint_url"),
        text("bucket"),
        secret("access_key_id_env"),
        secret("secret_access_key_env"),
        text("region", "us-east-1"),
        secret("session_token_env") if "session_token_env" in fields else None,
    )
