"""Named Session lifecycle and bounded retained metadata reads."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.errors import (
    AnalysisRepair,
    SessionNotFoundError,
    SessionStateError,
)
from marivo.analysis.materialization.contracts import invalid
from marivo.analysis.materialization.layout import MaterializationLayout
from marivo.analysis.materialization.reconciliation import reconcile_session
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.session._lazy_read_model import SessionInspection, SessionSummaryPage
from marivo.analysis.session.core import Session
from marivo.introspection.live.model import LiveHelpTarget
from marivo.project import resolve_project_root

__all__ = ["create", "inspect", "recent", "reconcile_run", "resume"]


def _invalid(expected: str, received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected=expected,
        received=received,
        repair="Inspect mv.session.recent() for identities, or create a new named Session with mv.session.create(name).",
        location="session.identity",
    )


def _existing_store(root: Path) -> SessionStore | None:
    """Read only the current-generation Store; absence never creates state."""
    layout = MaterializationLayout(root)
    try:
        layout.store_db.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise invalid(f"selected Store {layout.generation} is unavailable") from None
    return SessionStore.open_existing(root)


def _validate_project_manifest(root: Path) -> None:
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
                help_target=LiveHelpTarget(surface="analysis", canonical_id="session.create"),
            ),
        ) from exc


def create(
    name: str,
    *,
    question: str | None = None,
    timezone: str | None = None,
    domains: str | Sequence[str] | None = None,
) -> Session:
    """Create a new named Session in the current project's Store.

    Args:
        name: Unique exact Session name without surrounding whitespace.
        question: Optional creation-time investigation question.
        timezone: Optional IANA or explicit UTC-offset report timezone, frozen at creation.
        domains: Fixed semantic domain name or nonempty sequence. None selects all domains; selection is frozen at creation.

    Returns: The new Session, with authored semantics loaded only on demand.
    Example: ``session = mv.session.create('revenue-review')``.
    Constraints: Earlier Stores remain untouched and cannot be resumed or bypassed with another name. Use a fresh project for a new-format Store; no Session is migrated.
    """
    from marivo.analysis.materialization.admission import DatasetRuntime

    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise _invalid(
            "a nonempty exact Session name without surrounding whitespace", "empty or invalid name"
        )
    if question is not None and not isinstance(question, str):
        raise _invalid("a question string or None", "invalid question")
    root = resolve_project_root()
    _validate_project_manifest(root)
    selected_domains = None
    if domains is not None:
        names = (domains,) if isinstance(domains, str) else domains
        if (
            not isinstance(names, Sequence)
            or not names
            or any(not isinstance(name, str) or not name or name != name.strip() for name in names)
        ):
            from marivo.analysis.errors import SessionStateError

            raise SessionStateError(
                message="domains must be a nonempty exact domain name or sequence of names.",
                expected="one exact domain name or a nonempty sequence",
                received=repr(domains),
                location="mv.session.create(domains=...)",
                hint="Pass exact domain names without surrounding whitespace.",
            )
        selected_domains = tuple(sorted(set(names)))
    runtime = DatasetRuntime.create(
        root,
        name,
        question=question,
        timezone=timezone,
        domains=selected_domains,
    )
    return Session._from_runtime(runtime)


def resume(session_id: str) -> Session:
    """Recover one existing Session by its exact immutable ID.

    Args: session_id: Existing Session ID returned by create or recent.
    Returns: The recovered Session with its saved context.
    Example: ``session = mv.session.resume(session_id)``.
    Constraints: Never creates identity or loads current semantics and sources.
    """
    from marivo.analysis.materialization.admission import DatasetRuntime

    if not isinstance(session_id, str) or not session_id.strip():
        raise _invalid("an exact existing Session ID", "empty or invalid identity")
    root = resolve_project_root()
    store = SessionStore.open_existing(root)
    record = store.session(session_id)
    if record is None:
        from marivo.analysis.session import _lazy_history

        raise SessionNotFoundError(
            message="The selected Session ID does not exist in this project.",
            expected="an exact existing Session ID from recent().items",
            received=session_id,
            location="mv.session.resume(session_id)",
            repair=AnalysisRepair(
                kind="inspect",
                action="Read recent() and resume a returned Session ID.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="session.recent"),
                candidates=tuple(
                    item.id for item in _lazy_history.recent(store, limit=10, after=None).items
                ),
            ),
        )
    runtime = DatasetRuntime.open(root, session_id)
    with session_writer_guard(store.layout.lock_path(session_id), session_ref=session_id):
        reconcile_session(store, session_id, event=runtime._event)
    return Session._from_runtime(runtime)


def recent(*, limit: int = 20, cursor: str | None = None) -> SessionSummaryPage:
    """Read a bounded page of existing Sessions.

    Args:
        limit: Page size from 1 through 100.
        cursor: The previous page's next_cursor, or None.
    Returns: A newest-first SessionSummaryPage, empty when the current-generation Store is absent.
    Example: ``mv.session.recent(limit=5).show()``.
    Constraints: History reads do not create a Store, activate or recover Sessions. Other generations are ignored; an invalid current Store still raises.
    """
    from marivo.analysis.session import _lazy_history
    from marivo.analysis.session._lazy_runtime_reads import page_after

    after = page_after(limit, cursor, operation="recent")
    store = _existing_store(resolve_project_root())
    if store is None:
        return SessionSummaryPage(items=(), limit=limit, has_more=False, next_cursor=None)
    return _lazy_history.recent(
        store,
        limit=limit,
        after=after,
    )


def inspect(
    session_id: str, *, run_limit: int = 5, run_cursor: str | None = None
) -> SessionInspection:
    """Read a Session summary and one bounded Run page without recovery.

    Args:
        session_id: Exact existing Session ID.
        run_limit: Run page size from 1 through 100.
        run_cursor: The previous Run page's next_cursor, or None.
    Returns: The typed SessionInspection.
    Example: ``mv.session.inspect(session_id).show()``.
    Constraints: This metadata read never consults current datasource state.
    """
    from marivo.analysis.session import _lazy_history
    from marivo.analysis.session._lazy_runtime_reads import page_after

    after = page_after(run_limit, run_cursor, operation="inspect")
    return _lazy_history.inspect(
        SessionStore.open_existing(resolve_project_root()),
        session_id,
        run_limit=run_limit,
        after=after,
    )


def reconcile_run(*, session_id: str, run_id: str) -> None:
    """Reconcile one incomplete or failed Run under its Session's writer guard.

    Args:
        session_id: Exact owning Session identity.
        run_id: Exact same-Session incomplete or already failed Run identity.
    Returns: None after guarded reconciliation succeeds.
    Example: ``mv.session.reconcile_run(session_id=session_id, run_id=run_id)``.
    Constraints: Local publication safety is mandatory; remote read status may remain unknown. Committed success cannot be reconciled as failure.
    """
    store = SessionStore.open_existing(resolve_project_root())
    with session_writer_guard(store.layout.lock_path(session_id), session_ref=session_id):
        reconcile_session(
            store,
            session_id,
            event=lambda point: None,
            run_ref=run_id,
        )


def _install_telemetry() -> None:
    from marivo.telemetry import tracked_capability

    global create, resume, reconcile_run
    create = tracked_capability(
        surface="analysis", capability_id="session.create", capability_kind="callable"
    )(create)
    resume = tracked_capability(
        surface="analysis", capability_id="session.resume", capability_kind="callable"
    )(resume)
    reconcile_run = tracked_capability(
        surface="analysis", capability_id="session.reconcile_run", capability_kind="callable"
    )(reconcile_run)


_install_telemetry()
