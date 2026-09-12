"""Named v3 Session lifecycle and bounded retained metadata reads."""

from __future__ import annotations

from typing import Literal

from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.errors import (
    AnalysisRepair,
    SessionIdentityAmbiguousError,
    SessionNotFoundError,
    SessionStateError,
)
from marivo.analysis.materialization.layout import MaterializationLayout
from marivo.analysis.materialization.reconciliation import reconcile_session
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.targets import ProjectObjectBindings, ProjectTarget
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.session._lazy_read_model import SessionInspection, SessionSummaryPage
from marivo.analysis.session.core import Session
from marivo.introspection.live.model import LiveHelpTarget
from marivo.project import resolve_project_root

__all__ = ["abandon_run", "current", "get_or_create", "inspect", "recent", "resume"]


def _invalid(expected: str, received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected=expected,
        received=received,
        repair="Inspect mv.session.recent() for v3 identities, or create a new named Session with mv.session.get_or_create(name).",
        location="session.identity",
    )


def get_or_create(
    name: str, question: str | None = None, *, report_timezone: str | None = None
) -> Session:
    """Create or recover a named Session in the current project's v3 Store.

    Args:
        name: Nonempty Session name.
        question: Optional current investigation question.
        report_timezone: Optional IANA report timezone, fixed on first creation.
    Returns: The activated Session, with authored semantics loaded only on demand.
    Example: ``session = mv.session.get_or_create('revenue-review')``.
    Constraints: Existing eager Stores remain untouched; no Session is migrated.
    """
    from marivo.analysis.materialization.admission import DatasetRuntime

    if not isinstance(name, str) or not name.strip():
        raise _invalid("a nonempty Session name", "empty or invalid name")
    if question is not None and not isinstance(question, str):
        raise _invalid("a question string or None", "invalid question")
    root = resolve_project_root()
    from marivo.config import PROJECT_MANIFEST, load_project_config

    try:
        load_project_config(root)
    except (OSError, ValueError) as exc:
        raise SessionStateError(
            message="The project manifest cannot configure a Session.",
            expected="a valid project configuration",
            received=str(exc),
            location=str(root / PROJECT_MANIFEST),
            repair=AnalysisRepair(
                kind="retry",
                action="Repair marivo.toml, then create or resume the named Session.",
                help_target=LiveHelpTarget(
                    surface="analysis", canonical_id="session.get_or_create"
                ),
            ),
        ) from exc
    runtime = DatasetRuntime.create(
        root,
        name,
        target=ProjectTarget(root),
        object_bindings=(ProjectObjectBindings(root),),
        question=question,
        report_timezone=report_timezone,
    )
    return Session._from_runtime(runtime)


def current() -> Session | None:
    """Read the current existing v3 Session without activation or source loading.

    Args: None.
    Returns: The persisted current Session, or None when absent.
    Example: ``session = mv.session.current()``.
    Constraints: This probe never creates a Store or reconciles pending work.
    """
    from marivo.analysis.materialization.admission import DatasetRuntime

    root = resolve_project_root()
    if not MaterializationLayout(root).store_db.is_file():
        return None
    store = SessionStore.open_existing(root)
    record = store.current()
    return (
        None
        if record is None
        else Session._from_runtime(
            DatasetRuntime.open(
                root,
                record.session_ref,
                target=ProjectTarget(root),
                object_bindings=(ProjectObjectBindings(root),),
            )
        )
    )


def resume(identity: str, *, by: Literal["name", "id"] | None = None) -> Session:
    """Recover and activate one existing v3 Session by exact identity.

    Args:
        identity: Existing Session name or immutable id.
        by: Explicit name/id selector when the two identities are ambiguous.
    Returns: The activated existing Session.
    Example: ``session = mv.session.resume(session_id, by='id')``.
    Constraints: Missing, ambiguous or old-generation identities never create a Session.
    """
    from marivo.analysis.materialization.admission import DatasetRuntime

    if by not in (None, "name", "id"):
        raise SessionStateError(
            message="The Session identity selector is not supported.",
            expected='by="name", by="id", or omitted by',
            received=str(by)[:160],
            location="mv.session.resume(by=...)",
            repair=AnalysisRepair(
                kind="retry",
                action='Select by="name" or by="id", or omit by.',
                help_target=LiveHelpTarget(surface="analysis", canonical_id="session.resume"),
                candidates=("name", "id"),
            ),
        )
    if not isinstance(identity, str) or not identity.strip():
        raise _invalid("a nonempty Session name or id", "empty or invalid identity")
    root = resolve_project_root()
    store = SessionStore.open_existing(root)
    named = store.session_by_name(identity) if by != "id" else None
    keyed = store.session(identity) if by != "name" else None
    if named is not None and keyed is not None and named.session_ref != keyed.session_ref:
        raise SessionIdentityAmbiguousError(
            message="This identity names two different Sessions.",
            expected="one session matched by exact name or id",
            received=identity[:320],
            location="mv.session.resume(identity)",
            repair=AnalysisRepair(
                kind="user_choice",
                action='Choose the matching Session with by="name" or by="id".',
                help_target=LiveHelpTarget(surface="analysis", canonical_id="session.resume"),
                candidates=(
                    f'by="id" -> name={keyed.name!r}',
                    f'by="name" -> id={named.session_ref!r}',
                ),
            ),
        )
    record = named or keyed
    if record is None:
        from marivo.analysis.materialization.store import _rows, _text

        with store._read() as connection:
            rows = _rows(
                connection,
                "SELECT name,session_ref FROM sessions ORDER BY updated_at DESC,session_ref DESC LIMIT 10",
            )
        selection = "name or id" if by is None else by
        raise SessionNotFoundError(
            message="The selected Session does not exist in this project's v3 Store.",
            expected=f"an existing project session {selection} from mv.session.recent().items",
            received=identity[:320],
            location="mv.session.resume(identity)",
            repair=AnalysisRepair(
                kind="inspect",
                action="Read recent() and resume one returned Session identity.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="session.recent"),
                candidates=tuple(
                    _text(row, "session_ref" if by == "id" else "name") for row in rows
                ),
            ),
        )
    return Session._from_runtime(
        DatasetRuntime.create(
            root,
            record.name,
            target=ProjectTarget(root),
            object_bindings=(ProjectObjectBindings(root),),
        )
    )


def recent(*, limit: int = 20, cursor: str | None = None) -> SessionSummaryPage:
    """Read a bounded page of existing v3 Sessions.

    Args:
        limit: Page size from 1 through 100.
        cursor: The previous page's next_cursor, or None.
    Returns: A newest-first SessionSummaryPage.
    Example: ``mv.session.recent(limit=5).show()``.
    Constraints: History reads do not activate or recover Sessions.
    """
    from marivo.analysis.materialization.admission import DatasetRuntime

    return DatasetRuntime.recent(resolve_project_root(), limit=limit, cursor=cursor)


def inspect(name: str, *, run_limit: int = 5, run_cursor: str | None = None) -> SessionInspection:
    """Read a Session summary and one bounded Run page without activating it.

    Args:
        name: Exact existing v3 Session name.
        run_limit: Run page size from 1 through 100.
        run_cursor: The previous Run page's next_cursor, or None.
    Returns: The typed SessionInspection.
    Example: ``mv.session.inspect('revenue-review').show()``.
    Constraints: This metadata read never consults current datasource state.
    """
    from marivo.analysis.materialization.admission import DatasetRuntime

    return DatasetRuntime.inspect(
        resolve_project_root(), name, run_limit=run_limit, run_cursor=run_cursor
    )


def abandon_run(*, session_id: str, run_id: str) -> None:
    """Reconcile one stopped Run under its Session's existing writer guard.

    Args:
        session_id: Exact owning v3 Session identity.
        run_id: Exact same-Session incomplete or already failed Run identity.
    Returns: None after guarded reconciliation succeeds.
    Example: ``mv.session.abandon_run(session_id=session_id, run_id=run_id)``.
    Constraints: Runtime terminal/fencing proof is mandatory; committed success cannot be abandoned.
    """
    store = SessionStore.open_existing(resolve_project_root())
    with session_writer_guard(store.layout.lock_path(session_id), session_ref=session_id):
        reconcile_session(
            store,
            session_id,
            event=lambda point: None,
            run_ref=run_id,
            object_bindings=(ProjectObjectBindings(store.project_root),),
        )


def _install_telemetry() -> None:
    from marivo.telemetry import tracked_capability

    global get_or_create, resume, abandon_run
    get_or_create = tracked_capability(
        surface="analysis", capability_id="session.get_or_create", capability_kind="callable"
    )(get_or_create)
    resume = tracked_capability(
        surface="analysis", capability_id="session.resume", capability_kind="callable"
    )(resume)
    abandon_run = tracked_capability(
        surface="analysis", capability_id="session.abandon_run", capability_kind="callable"
    )(abandon_run)


_install_telemetry()
