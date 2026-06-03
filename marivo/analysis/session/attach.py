"""Session lifecycle and per-project session index."""

from __future__ import annotations

import secrets
import shutil
import sqlite3
import sys
import types
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marivo.analysis.errors import (
    DuplicateSessionNameError,
    NoActiveSessionError,
    SessionStateError,
)
from marivo.analysis.session.active import (
    clear_active_session,
    read_active_session_name,
    resolve_project_root,
    write_active_session_name,
)
from marivo.analysis.session.core import Session, SessionState
from marivo.analysis.session.persistence import (
    PersistenceLayout,
    read_session_meta,
    write_session_meta,
)
from marivo.analysis.timezone import resolve_system_timezone

_CURRENT_SESSION: Session | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    cwd TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_state ON sessions(state);
"""


@dataclass(frozen=True)
class SessionSummary:
    id: str
    name: str
    state: SessionState
    created_at: str
    updated_at: str


def _reset_process_state() -> None:
    global _CURRENT_SESSION
    _CURRENT_SESSION = None


def _index_path(project_root: Path) -> Path:
    return project_root / ".marivo" / "analysis" / "index.db"


def _connect_index(project_root: Path) -> sqlite3.Connection:
    path = _index_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def _gen_session_id() -> str:
    return f"sess_{secrets.token_hex(4)}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _compile_backend_factory(
    backends: dict[str, Callable[[], Any]] | None,
    backend_factory: Callable[[str], Any] | None,
    *,
    use_datasources: bool = True,
) -> Callable[[str], Any] | None:
    if backends is not None and backend_factory is not None:
        raise SessionStateError(
            message="supply either backends={...} or backend_factory=..., not both",
        )
    if backends is not None:
        backend_map = dict(backends)

        def from_mapping(name: str) -> Any:
            return backend_map[name]()

        return from_mapping
    if backend_factory is not None:
        return backend_factory
    if use_datasources:
        from marivo.analysis import datasources as _datasources

        def from_datasources(name: str) -> Any:
            return _datasources.build_backend(name)

        return from_datasources
    return None


def _build_semantic_project(project_root: Path) -> Any:
    from marivo.semantic import SemanticProject

    project = SemanticProject(root=str(project_root / ".marivo" / "semantic"))
    project.load()
    # Return the project even if not ready; callers should check is_ready()
    # and handle errors as needed.
    return project


def _system_tz_meta() -> dict[str, Any]:
    resolved = resolve_system_timezone()
    return {
        "tz": resolved.name,
        "tz_resolution": resolved.resolution,
        "tz_warning": resolved.warning,
    }


def _ensure_v1_2_meta(
    layout: PersistenceLayout,
    meta: dict[str, Any],
    *,
    default_calendar: str | None = None,
) -> dict[str, Any]:
    updated = dict(meta)
    changed = False
    system_meta = _system_tz_meta()
    previous_tz = updated.get("tz")
    if previous_tz != system_meta["tz"] and isinstance(previous_tz, str):
        updated["previous_tz"] = previous_tz
        changed = True
    for key, value in system_meta.items():
        if updated.get(key) != value:
            updated[key] = value
            changed = True
    if default_calendar is not None and updated.get("default_calendar") != default_calendar:
        updated["default_calendar"] = default_calendar
        changed = True
    elif "default_calendar" not in updated:
        updated["default_calendar"] = None
        changed = True
    if "known_calendars" not in updated:
        updated["known_calendars"] = []
        changed = True
    if changed:
        updated["updated_at"] = _now()
        write_session_meta(layout, updated)
    return updated


def _session_from_row(
    *,
    project_root: Path,
    row: sqlite3.Row | dict[str, Any],
    factory: Callable[[str], Any] | None,
    default_calendar: str | None = None,
) -> Session:
    layout = PersistenceLayout(project_root=project_root, session_id=row["id"])
    meta = _ensure_v1_2_meta(
        layout,
        read_session_meta(layout),
        default_calendar=default_calendar,
    )
    return Session(
        id=row["id"],
        name=row["name"],
        question=meta.get("question"),
        cwd=Path(meta["cwd"]),
        project_root=project_root,
        state=row["state"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        backend_factory=factory,
        layout=layout,
        semantic_project=_build_semantic_project(project_root),
        tz=resolve_system_timezone().tz,
        default_calendar=meta.get("default_calendar"),
        known_calendars=set(meta.get("known_calendars", [])),
        known_datasources=set(meta.get("known_datasources", [])),
    )


def _lookup_session_by_name(project_root: Path, name: str) -> dict[str, Any] | None:
    with closing(_connect_index(project_root)) as conn:
        row = conn.execute(
            "SELECT id, name, state, cwd, created_at, updated_at FROM sessions WHERE name = ?",
            (name,),
        ).fetchone()
    return dict(row) if row is not None else None


def create(
    name: str,
    question: str | None = None,
    set_active: bool = True,
    *,
    default_calendar: str | None = None,
    backends: dict[str, Callable[[], Any]] | None = None,
    backend_factory: Callable[[str], Any] | None = None,
    use_datasources: bool = True,
) -> Session:
    """Create a new analysis session with the given name.

    When to use: call this when you need a fresh session and are certain no
    session with this name exists yet. Raises on duplicate names. For
    idempotent scripts, prefer ``get_or_create``.

    Args:
        name: Unique session name within the project.
        question: Optional guiding question for the analysis.
        set_active: Whether to mark this session as the project-wide active
            session (persisted to disk).
        default_calendar: Default calendar name for time-based analysis.
        backends: Explicit mapping of datasource name to zero-arg factory
            callable returning an ibis backend. Use for a fixed set of
            backends.
        backend_factory: Single callable taking a datasource name and returning
            an ibis backend. Use for dynamic/lazy resolution.
        use_datasources: When True (default), auto-discovers datasource
            definitions from ``.marivo/datasource/*.py``. Set to False to
            disable auto-discovery.

    Raises:
        DuplicateSessionNameError: A session with this name already exists.

    Example:
        >>> session = mv.session.create("q4-revenue", question="Why did Q4 drop?")
    """
    project_root = resolve_project_root()
    factory = _compile_backend_factory(backends, backend_factory, use_datasources=use_datasources)
    sid = _gen_session_id()
    now = _now()
    cwd = str(Path.cwd())

    with closing(_connect_index(project_root)) as conn:
        try:
            conn.execute(
                "INSERT INTO sessions (id, name, state, cwd, created_at, updated_at) "
                "VALUES (?, ?, 'active', ?, ?, ?)",
                (sid, name, cwd, now, now),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise DuplicateSessionNameError(
                message=f"session name '{name}' already exists in this project",
                hint="Use mv.session.get_or_create(name=...) for rerunnable scripts, or mv.session.attach(name=...) to open the existing session.",
            ) from exc

    layout = PersistenceLayout(project_root=project_root, session_id=sid)
    write_session_meta(
        layout,
        {
            "id": sid,
            "name": name,
            "question": question,
            "cwd": cwd,
            "state": "active",
            "created_at": now,
            "updated_at": now,
            "project_root": str(project_root),
            **_system_tz_meta(),
            "default_calendar": default_calendar,
            "known_calendars": [],
            "known_datasources": [],
        },
    )
    session = _session_from_row(
        project_root=project_root,
        row={
            "id": sid,
            "name": name,
            "state": "active",
            "cwd": cwd,
            "created_at": now,
            "updated_at": now,
        },
        factory=factory,
    )
    if set_active:
        write_active_session_name(project_root, name)
    global _CURRENT_SESSION
    _CURRENT_SESSION = session
    return session


def attach(
    name: str,
    *,
    default_calendar: str | None = None,
    backends: dict[str, Callable[[], Any]] | None = None,
    backend_factory: Callable[[str], Any] | None = None,
    use_datasources: bool = True,
) -> Session:
    """Attach to an existing session by name.

    When to use: call this to resume work on a session that was previously
    created. Does not create a new session if the name is missing. Use
    ``get_or_create`` for idempotent attach-or-create semantics.

    Args:
        name: Name of the existing session to attach to.
        default_calendar: Override the default calendar for this attachment.
        backends: Explicit mapping of datasource name to zero-arg factory
            callable returning an ibis backend.
        backend_factory: Single callable taking a datasource name and returning
            an ibis backend for dynamic resolution.
        use_datasources: When True (default), auto-discovers datasource
            definitions from ``.marivo/datasource/*.py``.

    Raises:
        NoActiveSessionError: No session with this name exists in the project.

    Example:
        >>> session = mv.session.attach("q4-revenue")
    """
    project_root = resolve_project_root()
    row = _lookup_session_by_name(project_root, name)
    if row is None:
        raise NoActiveSessionError(
            message=f"no session named '{name}' in project '{project_root}'",
            hint="Use mv.session.get_or_create(name=...) to create or attach by name.",
        )
    session = _session_from_row(
        project_root=project_root,
        row=row,
        factory=_compile_backend_factory(
            backends, backend_factory, use_datasources=use_datasources
        ),
        default_calendar=default_calendar,
    )
    global _CURRENT_SESSION
    _CURRENT_SESSION = session
    return session


def get_or_create(
    name: str,
    question: str | None = None,
    set_active: bool = True,
    *,
    default_calendar: str | None = None,
    backends: dict[str, Callable[[], Any]] | None = None,
    backend_factory: Callable[[str], Any] | None = None,
    use_datasources: bool = True,
) -> Session:
    """Attach to an existing session or create a new one if it does not exist.

    When to use: the default choice for idempotent scripts and notebooks.
    Safe to call repeatedly with the same name -- the first call creates,
    subsequent calls attach. Prefer this over ``create`` unless you
    specifically need duplicate-name detection.

    Args:
        name: Session name. Creates if absent, attaches if present.
        question: Guiding question (only used when creating a new session).
        set_active: Whether to mark this session as the project-wide active
            session.
        default_calendar: Default calendar name for time-based analysis.
        backends: Explicit mapping of datasource name to zero-arg factory
            callable returning an ibis backend.
        backend_factory: Single callable taking a datasource name and returning
            an ibis backend for dynamic resolution.
        use_datasources: When True (default), auto-discovers datasource
            definitions from ``.marivo/datasource/*.py``.

    Example:
        >>> session = mv.session.get_or_create("q4-revenue", question="Why did Q4 drop?")
    """
    project_root = resolve_project_root()
    row = _lookup_session_by_name(project_root, name)
    if row is None:
        return create(
            name=name,
            question=question,
            set_active=set_active,
            default_calendar=default_calendar,
            backends=backends,
            backend_factory=backend_factory,
            use_datasources=use_datasources,
        )
    session = attach(
        name=name,
        default_calendar=default_calendar,
        backends=backends,
        backend_factory=backend_factory,
        use_datasources=use_datasources,
    )
    if set_active:
        write_active_session_name(project_root, name)
    return session


def switch(
    name: str,
    *,
    default_calendar: str | None = None,
    backends: dict[str, Callable[[], Any]] | None = None,
    backend_factory: Callable[[str], Any] | None = None,
    use_datasources: bool = True,
) -> Session:
    """Switch the project-wide active session to an existing session.

    When to use: call this to change which session is active without creating
    a new one. Unlike ``attach``, this always persists the active marker so
    future ``active()`` calls return this session. Refuses to switch to
    archived sessions.

    Args:
        name: Name of the existing session to switch to.
        default_calendar: Override the default calendar for this attachment.
        backends: Explicit mapping of datasource name to zero-arg factory
            callable returning an ibis backend.
        backend_factory: Single callable taking a datasource name and returning
            an ibis backend for dynamic resolution.
        use_datasources: When True (default), auto-discovers datasource
            definitions from ``.marivo/datasource/*.py``.

    Raises:
        NoActiveSessionError: No session with this name exists.
        SessionStateError: The session is archived and cannot be activated.

    Example:
        >>> session = mv.session.switch("q4-revenue")
    """
    project_root = resolve_project_root()
    row = _lookup_session_by_name(project_root, name)
    if row is None:
        raise NoActiveSessionError(message=f"no session named '{name}'")
    if row["state"] == "archived":
        raise SessionStateError(message=f"session '{name}' is archived; cannot make it active")
    session = attach(
        name=name,
        default_calendar=default_calendar,
        backends=backends,
        backend_factory=backend_factory,
        use_datasources=use_datasources,
    )
    write_active_session_name(project_root, name)
    return session


def active() -> Session:
    """Return the current in-process session, or load the project-wide active session.

    When to use: call this to retrieve the session that is already active
    without specifying a name. Useful in downstream helpers and intents that
    expect a session to have been established earlier in the script.

    Raises:
        NoActiveSessionError: No session has been attached in this process and
            no active session marker exists on disk.

    Example:
        >>> session = mv.session.active()
    """
    if _CURRENT_SESSION is not None:
        return _CURRENT_SESSION
    project_root = resolve_project_root()
    active_name = read_active_session_name(project_root)
    if active_name is None:
        raise NoActiveSessionError(
            message="no active session and none set via attach()",
            hint="Use mv.session.get_or_create(name=...) or mv.session.attach(name=...).",
        )
    return attach(name=active_name)


def current() -> Session | None:
    """Return the active session, or None when no session is active.

    Unlike ``active()`` which raises ``NoActiveSessionError``, ``current()``
    returns ``None`` when there is no active session. The returned ``Session``
    has all analysis methods so you can check and continue work:

        session = mv.session.current()
        if session is not None:
            result = session.observe(...)
    """
    try:
        sess = active()
    except NoActiveSessionError:
        return None
    return sess


def history(limit: int = 5) -> list[Any]:
    """Return recent jobs for the active session, capped at ``limit`` entries."""
    if limit <= 0:
        return []

    try:
        sess = active()
    except NoActiveSessionError:
        return []

    jobs_attr = getattr(sess, "jobs", [])
    jobs = jobs_attr() if callable(jobs_attr) else jobs_attr
    return list(jobs)[-limit:]


def active_or_create(
    name_hint: str,
    question: str | None = None,
    *,
    default_calendar: str | None = None,
    backends: dict[str, Callable[[], Any]] | None = None,
    backend_factory: Callable[[str], Any] | None = None,
    use_datasources: bool = True,
) -> Session:
    try:
        sess = active()
    except NoActiveSessionError:
        return create(
            name=name_hint,
            question=question,
            default_calendar=default_calendar,
            backends=backends,
            backend_factory=backend_factory,
            use_datasources=use_datasources,
        )
    if default_calendar is not None:
        return attach(
            name=sess.name,
            default_calendar=default_calendar,
            backends=backends,
            backend_factory=backend_factory,
            use_datasources=use_datasources,
        )
    return sess


def list_sessions(include_archived: bool = False) -> list[SessionSummary]:
    project_root = resolve_project_root()
    where = "" if include_archived else "WHERE state = 'active'"
    with closing(_connect_index(project_root)) as conn:
        rows = conn.execute(
            f"SELECT id, name, state, created_at, updated_at FROM sessions {where} "
            "ORDER BY created_at",
        ).fetchall()
    return [
        SessionSummary(
            id=row["id"],
            name=row["name"],
            state=row["state"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]


def archive(name: str) -> None:
    project_root = resolve_project_root()
    updated_at = _now()
    with closing(_connect_index(project_root)) as conn:
        conn.execute(
            "UPDATE sessions SET state = 'archived', updated_at = ? WHERE name = ?",
            (updated_at, name),
        )
        conn.commit()
    row = _lookup_session_by_name(project_root, name)
    if row is not None:
        layout = PersistenceLayout(project_root=project_root, session_id=row["id"])
        meta = read_session_meta(layout)
        meta["state"] = "archived"
        meta["updated_at"] = updated_at
        write_session_meta(layout, meta)
    global _CURRENT_SESSION
    if _CURRENT_SESSION is not None and _CURRENT_SESSION.name == name:
        _CURRENT_SESSION.state = "archived"


def delete(name: str) -> None:
    project_root = resolve_project_root()
    row = _lookup_session_by_name(project_root, name)
    if row is None:
        return
    layout = PersistenceLayout(project_root=project_root, session_id=row["id"])
    if layout.session_dir.is_dir():
        shutil.rmtree(layout.session_dir)
    with closing(_connect_index(project_root)) as conn:
        conn.execute("DELETE FROM sessions WHERE name = ?", (name,))
        conn.commit()
    if read_active_session_name(project_root) == name:
        clear_active_session(project_root)
    global _CURRENT_SESSION
    if _CURRENT_SESSION is not None and _CURRENT_SESSION.name == name:
        _CURRENT_SESSION = None


class _CallableAttachModule(types.ModuleType):
    def __call__(self, name: str, **kwargs: Any) -> Session:
        return attach(name=name, **kwargs)


sys.modules[__name__].__class__ = _CallableAttachModule
