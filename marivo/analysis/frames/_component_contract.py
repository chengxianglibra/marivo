"""Metadata-only component continuation admission for artifact contracts."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from marivo.analysis.errors import ComponentFrameUnavailableError

if TYPE_CHECKING:
    from marivo.analysis.frames.base import ArtifactPrecondition, BaseFrame


def _component_frame_contract_precondition(frame: BaseFrame) -> ArtifactPrecondition:
    """Describe whether the persisted component sidecar is mechanically available.

    This metadata-only check reads the authoritative artifact registration and
    sidecar metadata without materializing the component DataFrame. Content
    integrity remains validated by ``frame.components()`` at call time.
    """
    from marivo.analysis.frames.base import ArtifactPrecondition

    component_ref = getattr(frame.meta, "component_ref", None)
    composition = getattr(frame.meta, "composition", None)
    context: dict[str, object] = {
        "parent_ref": frame.ref,
        "parent_kind": frame.meta.kind,
        "composition": composition,
    }
    if component_ref is None:
        error = ComponentFrameUnavailableError(
            message="components are unavailable because no component sidecar was persisted",
            context=context,
        )
        assert error.repair is not None
        return ArtifactPrecondition(
            check="component_frame_available",
            status="fail",
            reason=error.message,
            repair=error.repair,
        )

    context["component_ref"] = component_ref
    project_root = Path(frame.meta.project_root).resolve()
    db_path = project_root / ".marivo" / "analysis" / "session_store.db"
    row: sqlite3.Row | None = None
    if db_path.is_file():
        try:
            connection = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            try:
                row = connection.execute(
                    "SELECT kind, path, meta_path FROM artifacts "
                    "WHERE session_id = ? AND artifact_id = ?",
                    (frame.meta.session_id, component_ref),
                ).fetchone()
            finally:
                connection.close()
        except sqlite3.Error:
            row = None

    if row is None:
        error = ComponentFrameUnavailableError(
            message=(
                f"component frame referenced by this {frame.meta.kind} is not registered "
                "in the owning session"
            ),
            context=context,
        )
        assert error.repair is not None
        return ArtifactPrecondition(
            check="component_frame_available",
            status="fail",
            reason=error.message,
            repair=error.repair,
        )

    registered_kind = str(row["kind"])
    if registered_kind != "component_frame":
        context["loaded_kind"] = registered_kind
        error = ComponentFrameUnavailableError(
            message=(f"component_ref resolved to {registered_kind!r}, expected component_frame"),
            context=context,
        )
        assert error.repair is not None
        return ArtifactPrecondition(
            check="component_frame_available",
            status="fail",
            reason=error.message,
            repair=error.repair,
        )

    paths: dict[str, Path] = {}
    for key in ("path", "meta_path"):
        path = (project_root / str(row[key])).resolve()
        try:
            path.relative_to(project_root)
        except ValueError:
            path = Path()
        paths[key] = path
    if not all(path.is_file() and path.stat().st_size > 0 for path in paths.values()):
        error = ComponentFrameUnavailableError(
            message=(
                f"component frame referenced by this {frame.meta.kind} is no longer "
                "available on disk"
            ),
            context=context,
        )
        assert error.repair is not None
        return ArtifactPrecondition(
            check="component_frame_available",
            status="fail",
            reason=error.message,
            repair=error.repair,
        )

    try:
        persisted_kind = json.loads(paths["meta_path"].read_text()).get("kind")
    except (OSError, json.JSONDecodeError):
        persisted_kind = None
    if persisted_kind != "component_frame":
        context["loaded_kind"] = str(persisted_kind or "invalid_meta")
        error = ComponentFrameUnavailableError(
            message=(
                f"component_ref resolved to {context['loaded_kind']!r}, expected component_frame"
            ),
            context=context,
        )
        assert error.repair is not None
        return ArtifactPrecondition(
            check="component_frame_available",
            status="fail",
            reason=error.message,
            repair=error.repair,
        )

    return ArtifactPrecondition(
        check="component_frame_available",
        status="pass",
        reason=(
            f"component_ref={component_ref} is registered with persisted component sidecar "
            "files; content integrity is validated at call time"
        ),
    )
