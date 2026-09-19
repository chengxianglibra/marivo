"""Independent no-I/O admission for strptime and hour-prefix time axes.

Every assertion below reads only the backend's own registration decision.  The
positive cases prove that a backend which has qualified its parser admits the
axis; the negative adjacency cases stay refused on every backend, including the
ones whose gate is open, so widening one parser never widens another.
"""

from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Literal

import pytest

from marivo.analysis import grain, time_scope
from marivo.analysis.operators.registry import implementation, source_unsupported_reason
from marivo.analysis.session._lazy_sources import LazySources, make_lazy_sources
from marivo.datasource.ir import TableColumnBindingIR, TableSourceIR
from marivo.refs import ref
from marivo.semantic.ir import (
    CumulativeComposition,
    DateParse,
    HourPrefixParse,
    PeriodCalendarIR,
    StrptimeParse,
    TimestampParse,
)
from marivo.semantic.validator import Registry
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_observation_fixtures import NoIoActionPort
from tests.lazy_scalar_source_fixtures import registry_for

AXIS = "sales.orders.order_time"
HOUR_AXIS = "sales.orders.hour"

WidenedEngine = Literal["sqlite", "postgres", "mysql", "clickhouse", "trino"]
ENGINES: tuple[WidenedEngine, ...] = (
    "sqlite",
    "postgres",
    "mysql",
    "clickhouse",
    "trino",
)

# Backends whose parsing gate is open.  This list is updated in the same commit
# as the matching ``*_support.py`` flag, so an engine that appears here has live
# execution evidence; an engine that does not stays refused below.
#
# Trino stays closed because no reachable service exists to execute against.
# PostgreSQL is open at the same standard as the already live DuckDB path: a
# cell the declared format cannot read surfaces as the driver's own error on
# both engines, and PostgreSQL's leniency toward format-mismatched text remains
# a recorded limitation rather than a guard.
OPEN_ENGINES: tuple[WidenedEngine, ...] = ("sqlite", "mysql", "clickhouse", "postgres")

Mutate = Callable[[Registry], Registry]


def _sources(engine: WidenedEngine, mutate: Mutate) -> LazySources:
    registry, sidecar = registry_for(Path("unused.sqlite"), engine=engine)
    registry = mutate(registry)
    registry.freeze()
    return make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="parsed-time-admission",
        store_id="parsed-time-admission",
    )


def _strptime(fmt: str, timezone: str | None = None) -> Mutate:
    def mutate(registry: Registry) -> Registry:
        dimensions = {**registry.dimensions}
        dimensions[AXIS] = replace(
            dimensions[AXIS],
            parse=StrptimeParse(fmt, timezone=timezone),
            granularity="day" if "%H" not in fmt else "second",
        )
        return replace(registry, dimensions=dimensions)

    return mutate


def _hour_prefix(prefix: str = AXIS) -> Mutate:
    def mutate(registry: Registry) -> Registry:
        dimensions = {**registry.dimensions}
        dimensions[AXIS] = replace(
            dimensions[AXIS], parse=DateParse(), granularity="day", is_default=False
        )
        dimensions[HOUR_AXIS] = replace(
            dimensions[AXIS],
            semantic_id=HOUR_AXIS,
            name="hour",
            is_default=True,
            granularity="hour",
            parse=HourPrefixParse(prefix),
            source_column="channel",
        )
        return replace(registry, dimensions=dimensions)

    return mutate


def _dataset(sources: LazySources, axis: str, unit: str) -> object:
    return (
        sources.observe(
            ref.metric("sales.revenue"),
            time_scope=time_scope(start="2026-07-01", end="2026-07-03"),
        )
        .with_time_axis(ref.time_dimension(axis), grain=grain(unit))
        .aggregate()
    )


def _reason(engine: WidenedEngine, mutate: Mutate, axis: str, unit: str) -> str | None:
    return source_unsupported_reason(_dataset(_sources(engine, mutate), axis, unit), engine)


@pytest.mark.parametrize("engine", OPEN_ENGINES)
@pytest.mark.parametrize(
    "label,mutate,axis,unit",
    [
        ("civil-date", _strptime("%Y-%m-%d"), AXIS, "day"),
        ("time-bearing", _strptime("%Y-%m-%d %H:%M:%S", "UTC"), AXIS, "day"),
        ("composite-hour", _hour_prefix(), HOUR_AXIS, "hour"),
    ],
)
def test_open_backend_admits_parsed_time_axes(
    engine: WidenedEngine, label: str, mutate: Mutate, axis: str, unit: str
) -> None:
    dataset = _dataset(_sources(engine, mutate), axis, unit)
    registration = implementation(dataset).for_backend(engine)  # type: ignore[arg-type]
    assert registration is not None and registration.source, label


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize(
    "label,mutate,axis,unit",
    [
        ("civil-date", _strptime("%Y-%m-%d"), AXIS, "day"),
        ("time-bearing", _strptime("%Y-%m-%d %H:%M:%S", "UTC"), AXIS, "day"),
        ("composite-hour", _hour_prefix(), HOUR_AXIS, "hour"),
    ],
)
def test_gate_is_opt_in_per_backend(
    engine: WidenedEngine, label: str, mutate: Mutate, axis: str, unit: str
) -> None:
    """A backend that has not qualified its parser keeps the exact refusal."""
    if engine in OPEN_ENGINES:
        pytest.skip(f"{engine} has an open parsing gate")
    assert _reason(engine, mutate, axis, unit) == (
        "a Metric dimension requires an unsupported type or parser"
    ), label


@pytest.mark.parametrize("engine", ENGINES)
def test_parsed_time_axes_do_not_open_epoch_representations(engine: WidenedEngine) -> None:
    """Integer epoch parsing is outside this stage's contract.

    ``%s`` cannot even be authored, because ``normalize_strptime`` probes the
    format through ``time.strftime``/``time.strptime``.  An integer column with
    no declared parser has no temporal parse at all, so it fails at declaration
    normalization and never reaches backend admission.
    """
    from marivo.semantic.errors import SemanticError

    with pytest.raises(ValueError):
        StrptimeParse("%s")

    def mutate(registry: Registry) -> Registry:
        entities = {**registry.entities}
        entity = entities["sales.orders"]
        assert isinstance(entity.source, TableSourceIR)
        entities["sales.orders"] = replace(
            entity,
            source=replace(
                entity.source,
                columns=tuple(
                    (name, TableColumnBindingIR(name, "int64") if name == "day" else binding)
                    for name, binding in entity.source.columns
                ),
            ),
        )
        dimensions = {**registry.dimensions}
        dimensions[AXIS] = replace(dimensions[AXIS], parse=None, granularity="second")
        return replace(registry, entities=entities, dimensions=dimensions)

    with pytest.raises(SemanticError) as refusal:
        _dataset(_sources(engine, mutate), AXIS, "day")
    assert refusal.value.received == "a non-temporal source type without parse"


@pytest.mark.parametrize("format", ["%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S%Z"])
def test_offset_and_zone_formats_stay_unconstructable(format: str) -> None:
    """``%z``/``%Z`` never reach admission: authoring rejects them first."""
    with pytest.raises(ValueError):
        StrptimeParse(format, timezone="UTC")


@pytest.mark.parametrize("engine", ENGINES)
def test_parsed_time_axes_do_not_open_cumulative_state(engine: WidenedEngine) -> None:
    def mutate(registry: Registry) -> Registry:
        metrics = {**registry.metrics}
        metrics["sales.running"] = replace(
            registry.metrics["sales.conversion_rate"],
            semantic_id="sales.running",
            name="running",
            composition=CumulativeComposition("sales.revenue", AXIS),
        )
        return _strptime("%Y-%m-%d")(replace(registry, metrics=metrics))

    sources = _sources(engine, mutate)
    observed = sources.observe(
        ref.metric("sales.running"),
        time_scope=time_scope(start="2026-07-01", end="2026-07-03"),
    ).with_time_axis(ref.time_dimension(AXIS), grain=grain("day"))
    reason = source_unsupported_reason(observed.aggregate(), engine)
    assert reason is not None
    if engine in OPEN_ENGINES:
        # The parser is admitted here, so the surviving refusal is the payload's own.
        assert "cumulative" in reason


@pytest.mark.parametrize("engine", ENGINES)
def test_parsed_time_axes_do_not_open_timestamp_validity(engine: WidenedEngine) -> None:
    """A time-bearing strptime axis is not a usable validity endpoint."""
    from marivo.semantic.validator import normalize_target_dimension

    def mutate(registry: Registry) -> Registry:
        entities = {**registry.entities}
        entity = entities["sales.validity"]
        assert isinstance(entity.source, TableSourceIR)
        entities["sales.validity"] = replace(
            entity,
            source=replace(
                entity.source,
                columns=tuple(
                    (
                        name,
                        replace(binding, data_type="timestamp(6)")
                        if name in {"start", "end"}
                        else binding,
                    )
                    for name, binding in entity.source.columns
                ),
            ),
        )
        dimensions = {**registry.dimensions}
        for name in ("valid_from", "valid_to"):
            path = "sales.validity." + name
            dimensions[path] = replace(
                dimensions[path],
                parse=StrptimeParse("%Y-%m-%d %H:%M:%S", timezone="UTC"),
                granularity="second",
            )
        return replace(registry, entities=entities, dimensions=dimensions)

    registry, sidecar = registry_for(Path("unused.sqlite"), engine=engine)
    registry = mutate(registry)
    registry.freeze()
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="parsed-time-admission",
        store_id="parsed-time-admission",
    )
    population = sources.population(
        ref.entity("sales.validity"),
        time_scope=time_scope(start="2026-07-01", end="2026-07-02"),
    )
    reason = source_unsupported_reason(population, engine)
    assert reason is not None and "civil-date axes" in reason
    assert (
        normalize_target_dimension(registry, "sales.validity.valid_from").logical_type
        == "timestamp"
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_parsed_time_axes_do_not_open_semantic_calendars(engine: WidenedEngine) -> None:
    """A certified calendar grain over a strptime axis stays refused."""
    from marivo._temporal import certify_period_calendar, semantic_grain

    calendar = ref.period_calendar("sales.fiscal")
    snapshot = certify_period_calendar(
        calendar_ref=calendar,
        boundary_timezone="UTC",
        coverage=(date(2026, 7, 1), date(2026, 7, 3)),
        rows=(
            {"date": date(2026, 7, 1), "period": "a"},
            {"date": date(2026, 7, 2), "period": "b"},
        ),
        levels={"reporting_period": "period"},
    )
    registry, sidecar = registry_for(Path("unused.sqlite"), engine=engine)
    metric = registry.metrics["sales.revenue"]
    registry = _strptime("%Y-%m-%d")(registry)
    registry = replace(
        registry,
        period_calendars={
            **registry.period_calendars,
            calendar.path: PeriodCalendarIR(
                calendar.path,
                "sales",
                "fiscal",
                AXIS,
                "UTC",
                ("2026-07-01", "2026-07-03"),
                (("reporting_period", "sales.orders.channel"),),
                metric.ai_context,
                "fiscal",
                metric.location,
            ),
        },
    )
    registry.freeze()
    sources = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="parsed-time-admission",
        store_id="parsed-time-admission",
        period_calendar_snapshots=(snapshot,),
    )
    dataset = (
        sources.observe(
            ref.metric("sales.revenue"),
            time_scope=time_scope(start="2026-07-01T00:00:00", end="2026-07-03T00:00:00"),
        )
        .with_time_axis(
            ref.time_dimension(AXIS),
            grain=semantic_grain(calendar=calendar, level="reporting_period"),
        )
        .aggregate()
    )
    assert source_unsupported_reason(dataset, engine) is not None


@pytest.mark.parametrize("engine", ENGINES)
def test_hour_prefix_never_admits_a_timestamp_prefix(engine: WidenedEngine) -> None:
    def mutate(registry: Registry) -> Registry:
        dimensions = {**registry.dimensions}
        dimensions[AXIS] = replace(
            dimensions[AXIS],
            parse=TimestampParse(timezone="UTC"),
            granularity="second",
            is_default=False,
        )
        dimensions[HOUR_AXIS] = replace(
            dimensions[AXIS],
            semantic_id=HOUR_AXIS,
            name="hour",
            is_default=True,
            granularity="hour",
            parse=HourPrefixParse(AXIS),
            source_column="channel",
        )
        return replace(registry, dimensions=dimensions)

    assert _reason(engine, mutate, HOUR_AXIS, "hour") is not None


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("prefix", ["sales.snapshots.snapshot_at", "sales.orders.channel"])
def test_hour_prefix_never_admits_a_cross_entity_or_non_temporal_prefix(
    engine: WidenedEngine, prefix: str
) -> None:
    """``sales.snapshots.snapshot_at`` fails the same-Entity half of the rule and
    ``sales.orders.channel`` fails the civil-date half."""
    assert _reason(engine, _hour_prefix(prefix), HOUR_AXIS, "hour") is not None


@pytest.mark.parametrize("engine", ENGINES)
def test_unresolvable_hour_prefix_is_refused(engine: WidenedEngine) -> None:
    """A prefix that is not a loadable Dimension must not survive admission.

    The shared normalization layer resolves this reference before the backend
    decision, so the refusal is the same ``SemanticLoadError`` for every engine.
    """
    from marivo.semantic.errors import SemanticError

    with pytest.raises(SemanticError) as refusal:
        _reason(engine, _hour_prefix("sales.orders.not_declared"), HOUR_AXIS, "hour")
    assert refusal.value.received == "not loaded"


def test_default_flags_keep_parsed_axes_refused() -> None:
    """The opt-in flag defaults to False, so an unconditioned caller is unchanged."""
    import inspect

    from marivo.analysis.operators.scalar_support import unsupported_reason

    assert inspect.signature(unsupported_reason).parameters["parsed_time_axes"].default is False
    registry, sidecar = make_execution_registry(Path("unused.duckdb"))
    registry = _strptime("%Y-%m-%d")(registry)
    registry.freeze()
    observed = (
        make_lazy_sources(
            semantic_registry=registry,
            sidecar=sidecar,
            action_port=NoIoActionPort(),
            session_id="parsed-time-admission",
            store_id="parsed-time-admission",
        )
        .observe(
            ref.metric("sales.revenue"),
            time_scope=time_scope(start="2026-07-01", end="2026-07-03"),
        )
        .with_time_axis(ref.time_dimension(AXIS), grain=grain("day"))
        .aggregate()
    )
    assert (
        unsupported_reason(observed, lambda kind: True, date_buckets=True, timestamp_buckets=True)
        == "a Metric dimension requires an unsupported type or parser"
    )
