"""Named Session lifecycle and bounded retained metadata reads."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.errors import (
    AnalysisRepair,
    SessionAlreadyExistsError,
    SessionNotFoundError,
    SessionStateError,
)
from marivo.analysis.materialization.contracts import invalid
from marivo.analysis.materialization.layout import MaterializationLayout
from marivo.analysis.materialization.reconciliation import reconcile_session
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.session._lazy_read_model import SessionSummary, SessionSummaryPage
from marivo.analysis.session.core import Session
from marivo.introspection.live.model import LiveHelpTarget
from marivo.project import resolve_project_root

__all__ = ["get_or_create", "inspect", "recent", "resume"]


def _invalid(expected: str, received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected=expected,
        received=received,
        repair="Inspect mv.session.recent() for identities, or create a new named Session with mv.session.get_or_create(name).",
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
                help_target=LiveHelpTarget(
                    surface="analysis", canonical_id="session.get_or_create"
                ),
            ),
        ) from exc


def get_or_create(
    name: str,
    *,
    question: str | None = None,
    timezone: str | None = None,
    domains: str | Sequence[str] | None = None,
) -> Session:
    """Create a named Session or recover the same exact project-local name.

    Args:
        name: Nonempty exact Session name without surrounding whitespace.
        question: Creation-time description; a non-None value must match on recovery.
        timezone: Creation-time IANA or explicit UTC-offset timezone; a non-None
            value must match the saved normalized name on recovery.
        domains: Creation-time domain name or nonempty sequence; a non-None value
            must match the saved sorted, deduplicated scope on recovery.

    Returns: The new or recovered Session with its frozen context.
    Example: ``session = mv.session.get_or_create('revenue-review', timezone='UTC')``.
    Constraints: None means unspecified: use creation defaults for a new name and retain saved values for an existing name. No current Session is set; saved question, timezone and domains are never rewritten. A recovery with no pending work leaves updated_at unchanged. Explicit conflicts raise SessionStateError before recovery. Recovery uses the same writer guard as resume and never replays sources. Earlier Store generations cannot be migrated or bypassed.
    """
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
            raise SessionStateError(
                message="domains must be a nonempty exact domain name or sequence of names.",
                expected="one exact domain name or a nonempty sequence",
                received=repr(domains),
                location="mv.session.get_or_create(domains=...)",
                hint="Pass exact domain names without surrounding whitespace.",
            )
        selected_domains = tuple(sorted(set(names)))
    store = _existing_store(root)
    record = None if store is None else store.session_by_name(name)
    if record is None:
        try:
            from marivo.analysis.materialization.admission import DatasetRuntime

            runtime = DatasetRuntime.create(
                root, name, question=question, timezone=timezone, domains=selected_domains
            )
            return Session._from_runtime(runtime)
        except SessionAlreadyExistsError:
            # Another creator won the unique-name transaction; recover that identity.
            record = SessionStore.open_existing(root).session_by_name(name)
            if record is None:
                raise
    selected_timezone = timezone
    if timezone is not None:
        from marivo.analysis.timezone import zoneinfo_from_name
        from marivo.datasource.timezone import parse_timezone

        try:
            selected_timezone, _ = parse_timezone(timezone)
        except (ValueError, KeyError):
            zoneinfo_from_name(timezone)
            raise
    configuration: tuple[tuple[str, object, object], ...] = (
        ("question", question, record.question),
        ("timezone", selected_timezone, record.report_timezone_name),
        ("domains", selected_domains, record.domains),
    )
    for field, requested, saved in configuration:
        if requested is not None and requested != saved:
            raise SessionStateError(
                message="Explicit Session configuration conflicts with the saved context.",
                expected=f"saved {field}: {saved!r}",
                received=f"requested {field}: {requested!r}",
                location=f"mv.session.get_or_create({field}=...)",
                context={"session_id": record.session_ref, "field": field},
                repair=AnalysisRepair(
                    kind="retry",
                    action="Omit the conflicting configuration to retain saved values, pass the saved value, or create a new Session with a unique name.",
                    help_target=LiveHelpTarget(
                        surface="analysis", canonical_id="session.get_or_create"
                    ),
                    candidates=(record.session_ref,),
                ),
            )
    return resume(record.session_ref)


def resume(session_id: str) -> Session:
    """Recover one existing Session by its exact immutable ID.

    Args: session_id: Existing Session ID returned by get_or_create or recent.
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

    after = page_after(limit, cursor)
    store = _existing_store(resolve_project_root())
    if store is None:
        return SessionSummaryPage(items=(), limit=limit, has_more=False, next_cursor=None)
    return _lazy_history.recent(
        store,
        limit=limit,
        after=after,
    )


def inspect(session_id: str) -> SessionSummary:
    """Read one existing Session summary without recovery.

    Args:
        session_id: Exact existing Session ID.
    Returns: The immutable SessionSummary with basic metadata and Artifact count.
    Example: ``mv.session.inspect(session_id).show()``.
    Constraints: Reads no result identities, result rows or current datasource state.
    """
    from marivo.analysis.session import _lazy_history

    return _lazy_history.inspect(SessionStore.open_existing(resolve_project_root()), session_id)


def _install_telemetry() -> None:
    from marivo.telemetry import tracked_capability

    global get_or_create, resume
    get_or_create = tracked_capability(
        surface="analysis", capability_id="session.get_or_create", capability_kind="callable"
    )(get_or_create)
    resume = tracked_capability(
        surface="analysis", capability_id="session.resume", capability_kind="callable"
    )(resume)


_install_telemetry()
