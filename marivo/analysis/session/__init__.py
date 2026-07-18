"""Session management for analysis.

The public surface is intentionally narrow:

- ``mv.session.get_or_create(name=...)`` — idempotent: attach if a session
  with that name already exists in the project, otherwise create it. Sets
  the new or attached session as current.
- ``mv.session.current()`` — return the current ``Session`` or ``None``
  when there is no current session. Safe probe: check and continue work.
- ``mv.session.list()`` — list sessions in the project.
- ``mv.session.recent()`` — return a bounded newest-first page for reference.
- ``mv.session.inspect(name)`` — read a bounded historical metadata snapshot
  without resuming or touching the session.
- ``mv.session.delete(name)`` — permanently delete a session.

Removed names: ``archive``, ``attach``, ``create``, ``switch``, ``active``.
These are no longer part of the public surface.
"""

from __future__ import annotations

import builtins
import shutil
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

from marivo.analysis.session._store import SessionSummary
from marivo.analysis.session.history import SessionInspection, SessionSummaryPage

__all__ = ["current", "delete", "get_or_create", "inspect", "list", "recent"]

_PUBLIC_NAMES = frozenset(__all__)

_INTERNAL_NAMES = frozenset({"_reset_process_state"})


def current() -> Any:
    """Return the current session, or ``None`` when no session is current.

    Resolution order:
    1. Process-current session (set by ``get_or_create``).
    2. Persisted ``current_session_id`` in the store.
    3. ``None`` if neither resolves to a live session.
    """
    from marivo.analysis.session._runtime import current as _current

    return _current()


def _resolve_report_timezone(report_timezone: str | None) -> Any:
    from marivo.analysis import timezone as _tz_mod

    if report_timezone is None:
        return _tz_mod.resolve_system_timezone()
    return _tz_mod.ResolvedTimezone(
        name=report_timezone,
        tz=_tz_mod.zoneinfo_from_name(report_timezone),
        resolution="iana",
    )


def _report_tz_fields(resolved: Any) -> dict[str, str | None]:
    return {
        "report_tz": resolved.name,
        "report_tz_resolution": resolved.resolution,
        "report_tz_warning": resolved.warning,
    }


def get_or_create(
    name: str,
    question: str | None = None,
    *,
    default_calendar: str | None = None,
    report_timezone: str | None = None,
    backends: dict[str, Callable[[], Any]] | None = None,
    backend_factory: Callable[[str], Any] | None = None,
    use_datasources: bool = True,
) -> Any:
    """Attach to an existing session or create a new one if it does not exist.

    When to use: the default choice for idempotent scripts and notebooks.
    Safe to call repeatedly with the same name -- the first call creates,
    subsequent calls attach. Prefer this over explicit create/attach.

    Args:
        name: Session name. Creates if absent, attaches if present.
        question: Guiding question (only used when creating a new session;
            preserved on resume).
        default_calendar: Default calendar name for time-based analysis.
            When provided on resume, updates the persisted value.
        report_timezone: IANA timezone name for the report axis. Persisted on
            first create; conflicting values on reopen raise
            ``SessionTimezoneConflict``. Defaults to the system timezone.
        backends: Explicit mapping of datasource name to zero-arg factory
            callable returning an ibis backend.
        backend_factory: Single callable taking a datasource name and returning
            an ibis backend for dynamic resolution.
        use_datasources: When True (default), auto-discovers datasource
            definitions from ``models/datasources/*.py``.

    Raises:
        SessionStateError: Both ``backends`` and ``backend_factory`` were
            supplied.
        SessionTimezoneConflict: A ``report_timezone`` was requested that
            conflicts with the persisted report timezone.

    Example:
        >>> session = mv.session.get_or_create("q4-revenue", question="Why did Q4 drop?")
    """
    from marivo.analysis.session._runtime import (
        _build_connection_runtime,
    )
    from marivo.analysis.session._runtime import (
        _session_from_row as _from_row,
    )
    from marivo.analysis.session._runtime import (
        set_process_current as _set_proc,
    )
    from marivo.analysis.session._store import SessionStore as _Store

    store = _Store()
    connection_runtime = _build_connection_runtime(
        store.project_root,
        backends,
        backend_factory,
        use_datasources=use_datasources,
    )

    row = store.get_or_insert_session(
        name=name,
        question=question,
        cwd=Path.cwd(),
        default_calendar=default_calendar,
    )

    # Always touch updated_at on resume
    store.touch_session(row["id"])

    # Ensure the session directory exists on disk (it may be new)
    from marivo.analysis.session._layout import PersistenceLayout as _Layout

    layout = _Layout(project_root=store.project_root, session_id=row["id"])
    layout.session_dir.mkdir(parents=True, exist_ok=True)

    # Write or upgrade meta.json with report timezone metadata
    import json as _json

    from marivo.analysis.errors import SessionTimezoneConflict

    meta_path = layout.session_dir / "meta.json"
    if not meta_path.is_file():
        resolved_report_tz = _resolve_report_timezone(report_timezone)
        meta = {
            "id": row["id"],
            "name": row["name"],
            "question": row["question"],
            "cwd": row["cwd"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "project_root": str(store.project_root),
            **_report_tz_fields(resolved_report_tz),
            "default_calendar": row["default_calendar"],
            "known_calendars": [],
            "known_datasources": [],
        }
        meta_path.write_text(_json.dumps(meta, indent=2, sort_keys=True))
    else:
        meta = _json.loads(meta_path.read_text())
        persisted = meta.get("report_tz")
        if isinstance(persisted, str):
            if report_timezone is not None:
                requested = _resolve_report_timezone(report_timezone)
                if persisted != requested.name:
                    raise SessionTimezoneConflict(
                        message="session report timezone conflicts with requested report_timezone",
                        context={
                            "session": row["name"],
                            "persisted_report_tz": persisted,
                            "requested_report_tz": requested.name,
                        },
                    )
        else:
            resolved_report_tz = _resolve_report_timezone(report_timezone)
            meta.update(_report_tz_fields(resolved_report_tz))
        meta.pop("tz", None)
        meta.pop("tz_resolution", None)
        meta.pop("tz_warning", None)
        meta.pop("previous_tz", None)
        if "default_calendar" not in meta:
            meta["default_calendar"] = row["default_calendar"]
        if "known_calendars" not in meta:
            meta["known_calendars"] = []
        if "known_datasources" not in meta:
            meta["known_datasources"] = []
        meta["updated_at"] = row["updated_at"]
        meta_path.write_text(_json.dumps(meta, indent=2, sort_keys=True))

    # Set store current
    store.set_current_session_id(row["id"])

    # Build the live Session object
    session = _from_row(store, row, connection_runtime)

    # Set process current
    _set_proc(session)
    return session


def delete(name: str) -> None:
    """Permanently delete a session and all of its on-disk data.

    Removes the session from the store, clears the current pointer if it
    pointed here, drops the in-process current session if it matches, and
    deletes the session directory. No-op semantics: silently does nothing
    when the name is unknown.

    Args:
        name: Name of the session to delete.
    """
    from marivo.analysis.session._layout import PersistenceLayout as _Layout
    from marivo.analysis.session._runtime import (
        get_process_current as _get_proc,
    )
    from marivo.analysis.session._runtime import (
        set_process_current as _set_proc,
    )
    from marivo.analysis.session._store import SessionStore as _Store

    store = _Store()
    row = store.get_session_by_name(name)
    if row is None:
        return

    sid = row["id"]
    layout = _Layout(project_root=store.project_root, session_id=sid)

    # Close process-current resources if they match
    proc = _get_proc()
    if proc is not None and proc.id == sid:
        proc.close()
        _set_proc(None)

    # Delete store rows and clear store current first
    current_id = store.get_current_session_id()
    if current_id == sid:
        store.clear_current_session_id()
    store.delete_session_rows(name)

    # Then remove files
    shutil.rmtree(layout.session_dir, ignore_errors=True)


def list() -> builtins.list[SessionSummary]:
    """List sessions in the current project, ordered by creation time.

    When to use: enumerate sessions for selection or reporting. Returns
    lightweight :class:`SessionSummary` rows with count fields, not live
    ``Session`` objects.

    Example:
        >>> for s in mv.session.list():
        ...     print(s.name, s.job_count)
    """
    from marivo.analysis.session._store import SessionStore as _Store

    store = _Store()
    return store.list_sessions()


def recent(*, limit: int = 20, cursor: str | None = None) -> SessionSummaryPage:
    """Return one bounded page of recently updated project sessions.

    Sessions are ordered newest first by ``updated_at`` and stable id. Pass
    ``next_cursor`` back to this method when the returned page has more rows.
    This is the bounded discovery path for historical-reference reads;
    :func:`list` remains the compatibility path that returns every session.

    Args:
        limit: Maximum summaries to retain, from 1 through 100.
        cursor: Opaque continuation token from a previous page.

    Returns:
        A :class:`SessionSummaryPage` of lightweight session metadata.

    Example:
        >>> page = mv.session.recent(limit=10)
        >>> if page.items:
        ...     snapshot = mv.session.inspect(page.items[0].name)

    Constraints:
        This method does not resume a session or query a datasource.
    """
    from marivo.analysis.session.history import recent_sessions

    return recent_sessions(limit=limit, cursor=cursor)


def inspect(name: str, *, frame_limit: int = 10, job_limit: int = 5) -> SessionInspection:
    """Read a bounded metadata snapshot of one historical session.

    Args:
        name: Exact session name returned by :func:`recent` or :func:`list`.
        frame_limit: Maximum newest frame summaries to retain, from 1 through 100.
        job_limit: Maximum recent job summaries to retain, from 1 through 100.

    Returns:
        A :class:`SessionInspection` containing the session summary, frame
        summary page, and recent jobs.

    Raises:
        SessionNotFoundError: The name is absent from the current project.

    Example:
        >>> snapshot = mv.session.inspect("q4-revenue", frame_limit=10, job_limit=5)
        >>> snapshot.show()

    Constraints:
        Inspection does not set the current session, update ``updated_at``,
        load datasources or semantic objects, or expose execution methods.
    """
    from marivo.analysis.session.history import inspect_session

    return inspect_session(name=name, frame_limit=frame_limit, job_limit=job_limit)


def _reset_process_state() -> None:
    """Reset the process-level current session to None.

    Internal helper used by test fixtures and teardown.
    """
    from marivo.analysis.session._runtime import reset_process_state

    reset_process_state()


class _FacadeModule(types.ModuleType):
    """Module subclass that hides all names not in ``__all__``."""

    __all__: builtins.list[str]

    def __dir__(self) -> builtins.list[str]:
        return sorted(_PUBLIC_NAMES)

    def __getattr__(self, name: str) -> Any:
        # __getattr__ is only called for attributes not found by normal lookup.
        # For names that were injected into __dict__ by Python's import system
        # after module replacement, we need to block them here too.
        if name in _PUBLIC_NAMES:
            return object.__getattribute__(self, name)
        if name in _INTERNAL_NAMES:
            return object.__getattribute__(self, name)
        if name.startswith("__") and name.endswith("__"):
            return object.__getattribute__(self, name)
        raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")

    def __getattribute__(self, name: str) -> Any:
        # Allow access to dunder names and internal Python machinery
        if name.startswith("__") and name.endswith("__"):
            return object.__getattribute__(self, name)
        # Allow public names
        if name in _PUBLIC_NAMES:
            return object.__getattribute__(self, name)
        # Allow internal names (test helpers)
        if name in _INTERNAL_NAMES:
            return object.__getattribute__(self, name)
        # Block everything else (including submodule names injected by import)
        raise AttributeError(f"module 'marivo.analysis.session' has no attribute {name!r}")


# Replace the module class so dir() and attribute access are controlled
_this = sys.modules[__name__]
_new = _FacadeModule(__name__)
_new.__doc__ = __doc__
_new.__file__ = __file__
_new.__path__ = __path__
_new.__package__ = __package__
_new.__all__ = __all__
# Copy public names into the new module
_new.current = current  # type: ignore[attr-defined]
_new.get_or_create = get_or_create  # type: ignore[attr-defined]
_new.inspect = inspect  # type: ignore[attr-defined]
_new.delete = delete  # type: ignore[attr-defined]
_new.list = list  # type: ignore[attr-defined]
_new.recent = recent  # type: ignore[attr-defined]
_new._reset_process_state = _reset_process_state  # type: ignore[attr-defined]
sys.modules[__name__] = _new
