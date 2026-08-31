"""Session management for analysis.

The public surface is intentionally narrow:

- ``mv.session.get_or_create(name=...)`` — attach if that session already
  exists, otherwise create it. An explicit question becomes the session's
  current guiding question. Sets the new or attached session as current.
- ``mv.session.resume(identity, by=...)`` — explicitly resume one existing
  session by its exact name or immutable id and set it current; ``by`` resolves
  the rare case where one value matches different name and id rows.
- ``mv.session.current()`` — return the current ``Session`` or ``None``
  when there is no current session. Safe probe: check and continue work.
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
import threading
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from ibis.backends import BaseBackend

from marivo.analysis.session.core import Session
from marivo.analysis.session.history import SessionInspection, SessionSummaryPage

__all__ = ["current", "delete", "get_or_create", "inspect", "recent", "resume"]

_PUBLIC_NAMES = frozenset(__all__)

_INTERNAL_NAMES = frozenset({"_reset_process_state"})

_SESSION_ACTIVATION_LOCK = threading.RLock()


def current() -> Session | None:
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


def _activate_session(
    *,
    store: Any,
    row: Any,
    connection_runtime: Any,
    report_timezone: str | None,
    question_update: str | None = None,
) -> Session:
    """Activate one persisted session row with a caller-owned connection runtime."""
    import json as _json

    from marivo.analysis.errors import SessionTimezoneConflict
    from marivo.analysis.session._layout import PersistenceLayout as _Layout
    from marivo.analysis.session._layout import _atomic_write_text
    from marivo.analysis.session._runtime import (
        _session_from_row as _from_row,
    )
    from marivo.analysis.session._runtime import (
        set_process_current as _set_proc,
    )

    with _SESSION_ACTIVATION_LOCK:
        layout = _Layout(project_root=store.project_root, session_id=row["id"])
        store.validate_session_runtime_schema(str(row["id"]))
        if layout.session_dir.is_dir():
            from marivo.analysis.session._runs import reconcile_incomplete_runs

            recovery_session = _from_row(store, row, connection_runtime)
            try:
                reconcile_incomplete_runs(recovery_session)
            finally:
                if recovery_session._judgment_store is not None:
                    recovery_session._judgment_store.close()
                    recovery_session._judgment_store = None
        layout.session_dir.mkdir(parents=True, exist_ok=True)
        meta_path = layout.session_dir / "meta.json"

        meta: dict[str, object]
        if meta_path.is_file():
            meta = _json.loads(meta_path.read_text())
            persisted = meta.get("report_tz")
            requested = (
                _resolve_report_timezone(report_timezone) if report_timezone is not None else None
            )
            if isinstance(persisted, str) and report_timezone is not None:
                assert requested is not None
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
            meta = {}
            requested = (
                _resolve_report_timezone(report_timezone) if report_timezone is not None else None
            )

        with store.activate_session(
            session_id=row["id"], question_update=question_update
        ) as refreshed:
            row = refreshed
            if not meta:
                resolved_report_tz = requested or _resolve_report_timezone(None)
                meta = {
                    "id": row["id"],
                    "name": row["name"],
                    "question": row["question"],
                    "cwd": row["cwd"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "project_root": str(store.project_root),
                    **_report_tz_fields(resolved_report_tz),
                    "known_datasources": [],
                }
            else:
                if not isinstance(meta.get("report_tz"), str):
                    resolved_report_tz = requested or _resolve_report_timezone(None)
                    meta.update(_report_tz_fields(resolved_report_tz))
                meta.pop("tz", None)
                meta.pop("tz_resolution", None)
                meta.pop("tz_warning", None)
                meta.pop("previous_tz", None)
                meta.pop("default_calendar", None)
                meta.pop("known_calendars", None)
                if "known_datasources" not in meta:
                    meta["known_datasources"] = []
                meta["question"] = row["question"]
                meta["updated_at"] = row["updated_at"]
            _atomic_write_text(meta_path, _json.dumps(meta, indent=2, sort_keys=True))

        session = _from_row(store, row, connection_runtime)
        _set_proc(session)
        return session


def get_or_create(
    name: str,
    question: str | None = None,
    *,
    report_timezone: str | None = None,
    backends: dict[str, Callable[[], BaseBackend]] | None = None,
    backend_factory: Callable[[str], BaseBackend] | None = None,
    use_datasources: bool = True,
) -> Session:
    """Attach to an existing session or create a new one if it does not exist.

    When to use: the default choice for idempotent scripts and notebooks.
    Safe to call repeatedly with the same name -- the first call creates and
    subsequent calls attach to the same immutable session id. An explicit
    ``question`` becomes the current guiding question; omit it to resume without
    changing the persisted question. Prefer :func:`resume` when an existing
    session identity is available and creation must fail closed.

    Args:
        name: Session name. Creates if absent, attaches if present.
        question: Current guiding question. An explicit string updates the
            persisted value for an existing name; omitting it deliberately
            resumes without changing the question.
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
    )

    return _activate_session(
        store=store,
        row=row,
        connection_runtime=connection_runtime,
        report_timezone=report_timezone,
        question_update=question,
    )


def resume(
    identity: str,
    *,
    by: Literal["name", "id"] | None = None,
    backends: dict[str, Callable[[], BaseBackend]] | None = None,
    backend_factory: Callable[[str], BaseBackend] | None = None,
    use_datasources: bool = True,
) -> Session:
    """Resume an existing project session by its exact name or immutable id.

    Args:
        identity: Exact session name or ``sess_...`` id returned by a session
            or :func:`recent` summary.
        by: Explicit identity kind for resolving a name/id collision. Omit for
            normal exact matching; pass ``"name"`` or ``"id"`` only when the
            intended kind must be selected.
        backends: Explicit mapping of datasource name to zero-arg factory
            callable returning an ibis backend.
        backend_factory: Single callable taking a datasource name and returning
            an ibis backend for dynamic resolution.
        use_datasources: When True (default), auto-discovers datasource
            definitions from ``models/datasources/*.py``.

    Returns:
        The resumed live :class:`Session`, set as the current session.

    Raises:
        SessionNotFoundError: The identity is absent from the current project.
        SessionIdentityAmbiguousError: The identity matches one session id and
            a different session name.
        SessionStateError: ``by`` is not ``"name"``, ``"id"``, or ``None``;
            or both ``backends`` and ``backend_factory`` were supplied.

    Example:
        >>> page = mv.session.recent(limit=10)
        >>> by_name = mv.session.resume(page.items[0].name)
        >>> by_id = mv.session.resume(page.items[0].id)
        >>> selected = mv.session.resume(page.items[0].name, by="name")

    Constraints:
        This method never changes the persisted name, question, or report
        timezone. Ids and names are resolved only within the current project.
        Omit ``by`` unless an ambiguous identity must be resolved explicitly.
    """
    from marivo.analysis.errors import (
        AnalysisRepair,
        SessionIdentityAmbiguousError,
        SessionNotFoundError,
        SessionStateError,
    )
    from marivo.analysis.session._runtime import _build_connection_runtime
    from marivo.analysis.session._store import SessionStore as _Store
    from marivo.introspection.live.model import LiveHelpTarget

    store = _Store()
    if by not in (None, "name", "id"):
        raise SessionStateError(
            message=f"analysis session identity selector {by!r} is not supported",
            expected='by="name", by="id", or omitted by',
            received=by,
            location="mv.session.resume(by=...)",
            repair=AnalysisRepair(
                kind="retry",
                action='Retry with by="name", by="id", or omit by for automatic resolution.',
                help_target=LiveHelpTarget(surface="analysis", canonical_id="session.resume"),
                candidates=("name", "id"),
            ),
        )
    matches = store.get_sessions_by_identity(identity, by=by)
    if not matches:
        recent = store.page_sessions(limit=10, after=None)[:10]
        candidates = tuple(item.id if by == "id" else item.name for item in recent)
        expected_identity = (
            "an existing project session id"
            if by == "id"
            else (
                "an existing project session name"
                if by == "name"
                else "an existing project session name or id"
            )
        )
        raise SessionNotFoundError(
            message=f"analysis session identity {identity!r} was not found in the current project",
            expected=f"{expected_identity} from mv.session.recent().items",
            received=identity,
            location="mv.session.resume(identity)",
            repair=AnalysisRepair(
                kind="inspect",
                action=(
                    "Read mv.session.recent() and resume one of the returned "
                    f"session {by or 'name or id'} values."
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="session.recent"),
                candidates=candidates,
            ),
        )
    if len(matches) == 2:
        id_match = matches[0]
        name_match = matches[1]
        raise SessionIdentityAmbiguousError(
            message=(
                f"analysis session identity {identity!r} matches session id "
                f"{id_match['id']!r} and a different session name {name_match['name']!r}"
            ),
            expected="one session matched by exact name or id",
            received=identity,
            location="mv.session.resume(identity)",
            repair=AnalysisRepair(
                kind="user_choice",
                action=(
                    "Choose the intended matching session, then retry the same "
                    'identity with by="id" or by="name".'
                ),
                help_target=LiveHelpTarget(surface="analysis", canonical_id="session.resume"),
                candidates=(
                    f'by="id" -> name={id_match["name"]!r}',
                    f'by="name" -> id={name_match["id"]!r}',
                ),
            ),
        )
    row = matches[0]

    connection_runtime = _build_connection_runtime(
        store.project_root,
        backends,
        backend_factory,
        use_datasources=use_datasources,
    )
    return _activate_session(
        store=store,
        row=row,
        connection_runtime=connection_runtime,
        report_timezone=None,
    )


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


def recent(*, limit: int = 20, cursor: str | None = None) -> SessionSummaryPage:
    """Return one bounded page of recently updated project sessions.

    Sessions are ordered newest first by ``updated_at`` and stable id. Pass
    ``next_cursor`` back to this method when the returned page has more rows.
    This is the bounded discovery path for historical-reference reads.

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


def inspect(name: str, *, run_limit: int = 5, run_cursor: str | None = None) -> SessionInspection:
    """Read a bounded metadata snapshot of one historical session.

    Args:
        name: Exact session name returned by :func:`recent`.
        run_limit: Maximum newest Run records to retain, from 1 through 100.
        run_cursor: Optional cursor returned by the previous Run page.

    Returns:
        A :class:`SessionInspection` containing the session summary and Run page.

    Raises:
        SessionNotFoundError: The name is absent from the current project.

    Example:
        >>> snapshot = mv.session.inspect("q4-revenue", run_limit=5)
        >>> snapshot.show()

    Constraints:
        Inspection does not set the current session, update ``updated_at``,
        load datasources or semantic objects, or expose execution methods.
    """
    from marivo.analysis.session.history import inspect_session

    return inspect_session(name=name, run_limit=run_limit, run_cursor=run_cursor)


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
        if name == "list":
            raise AttributeError(
                "module 'marivo.analysis.session' has no attribute 'list'; "
                "session.list() was removed in favor of bounded mv.session.recent(limit, cursor) "
                "and mv.session.inspect(name)"
            )
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
_new.recent = recent  # type: ignore[attr-defined]
_new.resume = resume  # type: ignore[attr-defined]
_new._reset_process_state = _reset_process_state  # type: ignore[attr-defined]
sys.modules[__name__] = _new
