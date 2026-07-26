"""Shared private Event occurrence materialization for typed analysis."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, cast
from zoneinfo import ZoneInfo

import pandas as pd

from marivo.analysis.errors import (
    AnalysisRepair,
    EventIdentityError,
    EventParticipantCardinalityError,
    InvalidEventPatternError,
)
from marivo.analysis.event import (
    CompletenessDeclaration,
    EventWatermarkReceipt,
    EventWatermarkRequest,
)
from marivo.analysis.executor.runner import ExecutionResult, execute
from marivo.analysis.executor.windowing import (
    _window_bound_predicates,
    datasource_engine_profile,
    datasource_read_timezone,
    effective_time_context,
)
from marivo.analysis.frames.event import EventInputCoverage
from marivo.analysis.intents._observe_catalog import (
    _build_entity_adapter,
    _entity_details,
)
from marivo.analysis.intents._subject_cohort import (
    ResolvedSubjectCohort,
    apply_event_subject_membership,
)
from marivo.analysis.windows.spec import AbsoluteWindow
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import EventKind, Ref, RefPayloadV1

if TYPE_CHECKING:
    from marivo.analysis.session.core import Session

type OccurrenceKey = tuple[str, tuple[object, ...]]
type OccurrenceSetKey = tuple[Ref[EventKind], str]
type CoverageBasis = Literal[
    "observed_watermark",
    "declared_complete",
    "mixed",
    "unknown",
]


@dataclass(frozen=True)
class EventOccurrence:
    """One normalized occurrence for one exact Event participant role."""

    event_ref: Ref[EventKind]
    participant_name: str
    event_identity: tuple[object, ...]
    subject_identity: tuple[object, ...]
    occurred_at: pd.Timestamp

    @property
    def occurrence_key(self) -> OccurrenceKey:
        return (self.event_ref.key, self.event_identity)


@dataclass(frozen=True)
class EventOccurrenceInput:
    """Catalog-resolved input needed to query one Event exactly once."""

    event_ref: Ref[EventKind]
    participant_names: tuple[str, ...]
    subject_identity_counts: tuple[tuple[str, int], ...]
    datasource_name: str
    event_fingerprint: str

    def __post_init__(self) -> None:
        if not self.participant_names:
            raise ValueError("EventOccurrenceInput requires at least one participant role")
        if len(set(self.participant_names)) != len(self.participant_names):
            raise ValueError("EventOccurrenceInput participant roles must be unique")
        counts = dict(self.subject_identity_counts)
        if tuple(counts) != self.participant_names:
            raise ValueError(
                "EventOccurrenceInput identity counts must exactly match participant roles"
            )
        if any(type(count) is not int or count < 1 for count in counts.values()):
            raise ValueError("EventOccurrenceInput identity counts must be positive integers")

    @property
    def subject_identity_count_by_role(self) -> dict[str, int]:
        return dict(self.subject_identity_counts)


@dataclass(frozen=True)
class EventCoverageInput:
    """Catalog-resolved facts needed for authoritative Event coverage."""

    event_ref: Ref[EventKind]
    event_fingerprint: str
    datasource_name: str
    source_entity_ref: str
    occurred_at_ref: str


def canonical_scalar(value: object) -> object:
    """Return a stable JSON-safe representation of one scalar."""

    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    item = getattr(value, "item", None)
    if callable(item):
        converted = item()
        if converted is not value:
            return canonical_scalar(converted)
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return repr(value)


def _identity_component_sort_key(value: object) -> tuple[int, Decimal | str]:
    item = getattr(value, "item", None)
    if callable(item):
        converted = item()
        if converted is not value:
            return _identity_component_sort_key(converted)
    if value is None:
        return (0, "")
    if isinstance(value, bool):
        return (1, Decimal(int(value)))
    if isinstance(value, (int, float, Decimal)):
        return (2, Decimal(str(value)))
    if isinstance(value, pd.Timestamp):
        timestamp = value
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert("UTC")
        return (3, timestamp.isoformat())
    if isinstance(value, datetime):
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert("UTC")
        return (3, timestamp.isoformat())
    if isinstance(value, date):
        return (3, value.isoformat())
    if isinstance(value, str):
        return (4, value)
    if isinstance(value, bytes):
        return (5, value.hex())
    value_type = type(value)
    return (6, f"{value_type.__module__}.{value_type.__qualname__}:{value!r}")


def identity_sort_key(
    identity: tuple[object, ...],
) -> tuple[tuple[int, Decimal | str], ...]:
    """Return the deterministic heterogeneous Event identity ordering key."""

    return tuple(_identity_component_sort_key(component) for component in identity)


def stable_digest(payload: object) -> str:
    """Hash one canonical JSON payload."""

    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def inclusive_successor(
    value: str,
    *,
    report_tz: ZoneInfo,
    granularity: str,
) -> str:
    """Return the exclusive query bound after one governed inclusive instant."""

    bound = _parse_iso_bound(value, report_tz=report_tz)
    if bound is None:
        raise ValueError(f"invalid ISO-8601 bound: {value!r}")
    local_bound = bound.tz_convert(report_tz)
    if granularity == "year":
        successor = local_bound + pd.DateOffset(years=1)
    elif granularity == "quarter":
        successor = local_bound + pd.DateOffset(months=3)
    elif granularity == "month":
        successor = local_bound + pd.DateOffset(months=1)
    elif granularity == "week":
        successor = local_bound + pd.DateOffset(weeks=1)
    elif granularity == "day":
        successor = local_bound + pd.DateOffset(days=1)
    elif granularity == "hour":
        successor = local_bound + pd.Timedelta(hours=1)
    elif granularity == "minute":
        successor = local_bound + pd.Timedelta(minutes=1)
    elif granularity == "second":
        successor = local_bound + pd.Timedelta(seconds=1)
    else:
        raise ValueError(f"unsupported Event occurred_at granularity: {granularity!r}")
    return successor.isoformat()


def _repair(*, help_target: str, action: str) -> AnalysisRepair:
    return AnalysisRepair(
        kind="inspect",
        action=action,
        help_target=LiveHelpTarget(surface="analysis", canonical_id=help_target),
    )


def _time_adapter(
    *,
    session: Session,
    resolver: Any,
    source_entity: str,
    occurred_at: str,
    help_target: str,
    occurred_at_location: str,
) -> Any:
    entity = _entity_details(session.catalog, source_entity)
    adapter = _build_entity_adapter(session.catalog, resolver, entity)
    for field in adapter.fields.values():
        if field.semantic_id == occurred_at:
            if field.time_meta is None:
                raise InvalidEventPatternError(
                    message=f"Event occurred_at {occurred_at!r} has no time metadata",
                    expected="a governed TimeDimension",
                    received=occurred_at,
                    location=occurred_at_location,
                    repair=AnalysisRepair(
                        kind="semantic_authoring",
                        action="Repair the Event occurred_at TimeDimension definition.",
                        help_target=LiveHelpTarget(
                            surface="analysis",
                            canonical_id=help_target,
                        ),
                    ),
                )
            return adapter, field
    raise InvalidEventPatternError(
        message=f"Event occurred_at {occurred_at!r} is not on its source Entity",
        expected=f"a TimeDimension owned by {source_entity!r}",
        received=occurred_at,
        location=occurred_at_location,
        repair=AnalysisRepair(
            kind="semantic_authoring",
            action="Repair the Event occurred_at reference.",
            help_target=LiveHelpTarget(surface="analysis", canonical_id=help_target),
        ),
    )


def materialize_event_occurrences(
    *,
    session: Session,
    inputs: tuple[EventOccurrenceInput, ...],
    start: str | None,
    end: str,
    normalized_end: pd.Timestamp,
    end_inclusive: bool,
    cohort: ResolvedSubjectCohort | None,
    help_target: str,
    membership_lowerer: Callable[..., Any] = apply_event_subject_membership,
    occurred_at_location: str | None = None,
) -> tuple[
    dict[OccurrenceSetKey, tuple[EventOccurrence, ...]],
    tuple[Any, ...],
]:
    """Query distinct Events once and return normalized role-specific streams.

    ``start=None`` performs an unbounded-history scan with only the governed
    exclusive upper predicate pushed into each Event query. ``end_inclusive``
    advances the physical upper bound by one occurred-at granule and retains
    rows through ``normalized_end`` after timestamp normalization.
    """

    registry = session.catalog._require_index().registry
    resolver = session.catalog._semantic_resolver(connections=session._connection_runtime)
    occurrence_sets: dict[OccurrenceSetKey, tuple[EventOccurrence, ...]] = {}
    report_tz = cast("ZoneInfo", session.report_tz)
    semantic_location = (
        occurred_at_location
        if occurred_at_location is not None
        else f"session.{help_target}.Event.occurred_at"
    )
    session._connection_runtime.begin_query_capture()
    try:
        for item in inputs:
            event_ir = registry.events[item.event_ref.path]
            table = resolver.event(item.event_ref, participants=item.participant_names)
            if cohort is not None:
                table = membership_lowerer(
                    table=table,
                    cohort=cohort,
                    participant_names=item.participant_names,
                )
            entity_adapter, occurred_adapter = _time_adapter(
                session=session,
                resolver=resolver,
                source_entity=event_ir.source_entity,
                occurred_at=event_ir.occurred_at,
                help_target=help_target,
                occurred_at_location=semantic_location,
            )
            read_tz = datasource_read_timezone(
                session._connection_runtime,
                item.datasource_name,
            )
            profile = datasource_engine_profile(
                session._connection_runtime,
                item.datasource_name,
            )
            try:
                exclusive_end = (
                    inclusive_successor(
                        end,
                        report_tz=report_tz,
                        granularity=occurred_adapter.time_meta.granularity,
                    )
                    if end_inclusive
                    else end
                )
            except ValueError as exc:
                raise InvalidEventPatternError(
                    message="Event occurred_at has an unsupported granularity",
                    expected="year | quarter | month | week | day | hour | minute | second",
                    received=repr(occurred_adapter.time_meta.granularity),
                    location=semantic_location,
                    repair=AnalysisRepair(
                        kind="semantic_authoring",
                        action="Repair the Event occurred_at TimeDimension granularity.",
                        help_target=LiveHelpTarget(
                            surface="analysis",
                            canonical_id=help_target,
                        ),
                        candidates=(
                            "year",
                            "quarter",
                            "month",
                            "week",
                            "day",
                            "hour",
                            "minute",
                            "second",
                        ),
                    ),
                ) from exc
            predicate_window = AbsoluteWindow(
                start=start if start is not None else end,
                end=exclusive_end,
            )
            lower, upper = _window_bound_predicates(
                table["__occurred_at"],
                predicate_window,
                occurred_adapter.time_meta,
                report_tz=report_tz,
                datasource_read_tz=read_tz,
                profile=profile,
            )
            predicates = (lower, upper) if start is not None else (upper,)
            result = execute(
                table.filter(*predicates),
                datasource_name=item.datasource_name,
                cache=session._connection_runtime,
                session_id=session.id,
            )
            normalized = normalize_event_rows(
                result=result,
                event_ref=item.event_ref,
                role_names=item.participant_names,
                identity_count=len(event_ir.identity),
                subject_identity_count=item.subject_identity_count_by_role,
                time_meta=occurred_adapter.time_meta,
                dataset_adapter=entity_adapter,
                report_tz=report_tz,
                datasource_read_tz=read_tz,
                help_target=help_target,
            )
            if cohort is not None:
                # The query-level semi-join excludes rows outside every role.
                # This role-specific guard preserves exact membership when one
                # Event is materialized for several participant roles.
                allowed_identities = set(cohort.identities)
                normalized = {
                    key: tuple(
                        occurrence
                        for occurrence in occurrences
                        if occurrence.subject_identity in allowed_identities
                    )
                    for key, occurrences in normalized.items()
                }
            occurrence_sets.update(
                {
                    key: tuple(
                        occurrence
                        for occurrence in occurrences
                        if (
                            occurrence.occurred_at <= normalized_end
                            if end_inclusive
                            else occurrence.occurred_at < normalized_end
                        )
                    )
                    for key, occurrences in normalized.items()
                }
            )
    except BaseException:
        session._connection_runtime.take_captured_queries()
        raise
    return occurrence_sets, tuple(session._connection_runtime.take_captured_queries())


def _is_empty_component(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value:
        return True
    missing = pd.isna(cast("Any", value))
    return bool(missing) if isinstance(missing, bool) else False


def _normalize_timestamp(
    value: object,
    *,
    column_tz: ZoneInfo,
    decode_policy: str,
) -> pd.Timestamp | None:
    if _is_empty_component(value):
        return None
    try:
        timestamp = pd.Timestamp(cast("Any", value))
    except (TypeError, ValueError):
        return None
    if timestamp.tzinfo is None:
        timezone = ZoneInfo("UTC") if decode_policy == "utc_naive_instant" else column_tz
        timestamp = timestamp.tz_localize(timezone)
    return timestamp.tz_convert("UTC")


def normalize_event_rows(
    *,
    result: ExecutionResult,
    event_ref: Ref[EventKind],
    role_names: tuple[str, ...],
    identity_count: int,
    subject_identity_count: dict[str, int],
    time_meta: Any,
    dataset_adapter: Any,
    report_tz: ZoneInfo,
    datasource_read_tz: ZoneInfo,
    help_target: str,
) -> dict[OccurrenceSetKey, tuple[EventOccurrence, ...]]:
    """Validate and normalize one materialized Event query."""

    frame = result.df.copy()
    identity_columns = tuple(f"__event_identity_{index}" for index in range(identity_count))
    required = {
        *identity_columns,
        "__occurred_at",
        "__source_identity_count",
    }
    for role_name in role_names:
        required.update(
            f"__subject_{role_name}_identity_{index}"
            for index in range(subject_identity_count[role_name])
        )
    missing = required - set(frame.columns)
    if missing:
        raise EventIdentityError(
            message=f"materialized Event {event_ref.key!r} is missing identity columns",
            expected=repr(sorted(required)),
            received=repr(sorted(frame.columns)),
            location=f"session.{help_target}.materialized[{event_ref.key!r}]",
            repair=_repair(
                help_target=help_target,
                action="Inspect the Event identity and participant path definitions.",
            ),
        )

    context = effective_time_context(
        time_meta,
        report_tz=report_tz,
        datasource_read_tz=datasource_read_tz,
        field_expr=None,
        backend_policy=cast("Any", result.backend_datetime_decode_policy),
    )
    column_tz = context.effective_column_tz or datasource_read_tz
    by_role: dict[OccurrenceSetKey, list[EventOccurrence]] = {
        (event_ref, name): [] for name in role_names
    }
    seen_event_ids: set[tuple[object, ...]] = set()
    for row in frame.to_dict("records"):
        event_identity = tuple(row[column] for column in identity_columns)
        try:
            source_identity_count = int(row["__source_identity_count"])
        except (TypeError, ValueError) as exc:
            raise EventIdentityError(
                message=f"Event {event_ref.key!r} produced an invalid source identity count",
                expected="exactly one source occurrence per declared Event identity",
                received=repr(row["__source_identity_count"]),
                location=f"session.{help_target}.materialized[{event_ref.key!r}].identity",
                repair=_repair(
                    help_target=help_target,
                    action="Inspect the Event identity dimensions and source rows.",
                ),
            ) from exc
        if source_identity_count != 1:
            raise EventIdentityError(
                message=f"Event {event_ref.key!r} declared identity is not unique",
                expected="one source occurrence per declared Event identity",
                received=(
                    f"event_identity={event_identity!r}, "
                    f"source_identity_count={source_identity_count}"
                ),
                location=f"session.{help_target}.materialized[{event_ref.key!r}].identity",
                repair=_repair(
                    help_target=help_target,
                    action="Inspect duplicate Event identities and their source rows.",
                ),
            )
        if any(_is_empty_component(component) for component in event_identity):
            raise EventIdentityError(
                message=f"Event {event_ref.key!r} produced an empty identity component",
                expected="a non-null, non-empty declared Event identity tuple",
                received=repr(event_identity),
                location=f"session.{help_target}.materialized[{event_ref.key!r}].identity",
                repair=_repair(
                    help_target=help_target,
                    action="Inspect null Event identity components and their source rows.",
                ),
            )
        if event_identity in seen_event_ids:
            raise EventParticipantCardinalityError(
                message=(
                    f"Event {event_ref.key!r} participant join produced more than "
                    "one row for an occurrence"
                ),
                expected="exactly one endpoint for every cardinality='one' role",
                received=repr(event_identity),
                location=f"session.{help_target}.materialized[{event_ref.key!r}].participants",
                repair=_repair(
                    help_target=help_target,
                    action="Inspect participant relationship keys and joined endpoint rows.",
                ),
            )
        seen_event_ids.add(event_identity)
        occurred_at = _normalize_timestamp(
            row["__occurred_at"],
            column_tz=column_tz,
            decode_policy=result.backend_datetime_decode_policy,
        )
        if occurred_at is None:
            raise EventIdentityError(
                message=f"Event {event_ref.key!r} produced an invalid occurred_at value",
                expected="a non-null governed timestamp",
                received=repr(row["__occurred_at"]),
                location=f"session.{help_target}.materialized[{event_ref.key!r}].occurred_at",
                repair=_repair(
                    help_target=help_target,
                    action="Inspect the Event occurred_at definition and source values.",
                ),
            )
        for role_name in role_names:
            columns = tuple(
                f"__subject_{role_name}_identity_{index}"
                for index in range(subject_identity_count[role_name])
            )
            subject_identity = tuple(row[column] for column in columns)
            if any(_is_empty_component(component) for component in subject_identity):
                raise EventParticipantCardinalityError(
                    message=(
                        f"Event {event_ref.key!r} participant {role_name!r} "
                        "did not resolve to exactly one subject"
                    ),
                    expected="one non-null participant endpoint identity",
                    received=repr(subject_identity),
                    location=(
                        f"session.{help_target}.materialized[{event_ref.key!r}]"
                        f".participants[{role_name!r}]"
                    ),
                    repair=_repair(
                        help_target=help_target,
                        action="Inspect missing participant join keys and endpoint rows.",
                    ),
                )
            by_role[(event_ref, role_name)].append(
                EventOccurrence(
                    event_ref=event_ref,
                    participant_name=role_name,
                    event_identity=event_identity,
                    subject_identity=subject_identity,
                    occurred_at=occurred_at,
                )
            )
    return {
        key: tuple(
            sorted(
                values,
                key=lambda occurrence: (
                    occurrence.occurred_at,
                    identity_sort_key(occurrence.event_identity),
                ),
            )
        )
        for key, values in by_role.items()
    }


def _parse_iso_bound(value: object, *, report_tz: ZoneInfo) -> pd.Timestamp | None:
    if type(value) is not str or not value.strip():
        return None
    raw = value.strip()
    normalized = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=report_tz)
    return pd.Timestamp(parsed.astimezone(UTC))


def resolve_event_coverage(
    *,
    session: Session,
    inputs: tuple[EventCoverageInput, ...],
    required_through: str,
    required_instant: pd.Timestamp,
    declaration_by_event: dict[Ref[EventKind], CompletenessDeclaration],
) -> tuple[tuple[EventInputCoverage, ...], CoverageBasis]:
    """Resolve authoritative Event coverage once for any typed Event consumer."""

    report_tz = cast("ZoneInfo", session.report_tz)
    items: list[EventInputCoverage] = []
    for item in inputs:
        request = EventWatermarkRequest(
            event_ref=item.event_ref,
            event_fingerprint=item.event_fingerprint,
            source_entity_ref=item.source_entity_ref,
            occurred_at_ref=item.occurred_at_ref,
            required_through=required_through,
        )
        raw_receipt = session._connection_runtime.event_watermark(
            item.datasource_name,
            request,
        )
        receipt: EventWatermarkReceipt | None = None
        valid_receipt: EventWatermarkReceipt | None = None
        if raw_receipt is not None:
            try:
                candidate = EventWatermarkReceipt.model_validate(raw_receipt)
            except (TypeError, ValueError):
                candidate = None
            if candidate is not None:
                receipt_complete = _parse_iso_bound(
                    candidate.complete_through,
                    report_tz=report_tz,
                )
                observed_at = _parse_iso_bound(candidate.observed_at, report_tz=report_tz)
                if receipt_complete is not None and observed_at is not None:
                    valid_receipt = candidate
                    if receipt_complete >= required_instant:
                        receipt = candidate
        if receipt is not None:
            items.append(
                EventInputCoverage(
                    event_ref=RefPayloadV1.from_ref(item.event_ref),
                    basis="observed_watermark",
                    receipt=receipt,
                    observed_complete_through=receipt.complete_through,
                )
            )
            continue
        declaration = declaration_by_event.get(item.event_ref)
        declaration_through = (
            _parse_iso_bound(declaration.through, report_tz=report_tz)
            if declaration is not None
            else None
        )
        if declaration is not None and (
            declaration_through is not None and declaration_through >= required_instant
        ):
            items.append(
                EventInputCoverage(
                    event_ref=RefPayloadV1.from_ref(item.event_ref),
                    basis="declared_complete",
                    receipt=valid_receipt,
                    declaration_fingerprint=declaration.fingerprint,
                    declaration_rationale=declaration.rationale,
                    observed_complete_through=(
                        valid_receipt.complete_through if valid_receipt is not None else None
                    ),
                )
            )
            continue
        items.append(
            EventInputCoverage(
                event_ref=RefPayloadV1.from_ref(item.event_ref),
                basis="unknown",
                receipt=valid_receipt,
                observed_complete_through=(
                    valid_receipt.complete_through if valid_receipt is not None else None
                ),
            )
        )

    bases = {item.basis for item in items}
    if "unknown" in bases:
        aggregate: CoverageBasis = "unknown"
    elif bases == {"observed_watermark"}:
        aggregate = "observed_watermark"
    elif bases == {"declared_complete"}:
        aggregate = "declared_complete"
    else:
        aggregate = "mixed"
    return tuple(items), aggregate


def occurrence_snapshot_fingerprint(
    occurrence_sets: dict[OccurrenceSetKey, tuple[EventOccurrence, ...]],
) -> str:
    """Fingerprint normalized occurrences without backend-specific row shape."""

    payload = [
        {
            "event": event_ref.key,
            "participant": role,
            "event_identity": [
                [canonical_scalar(component) for component in item.event_identity]
                for item in occurrences
            ],
            "subject_identity": [
                [canonical_scalar(component) for component in item.subject_identity]
                for item in occurrences
            ],
            "occurred_at": [item.occurred_at.isoformat() for item in occurrences],
        }
        for (event_ref, role), occurrences in sorted(
            occurrence_sets.items(),
            key=lambda item: (item[0][0].key, item[0][1]),
        )
    ]
    return stable_digest(payload)


__all__ = []
