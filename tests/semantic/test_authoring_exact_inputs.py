"""Physical consumers resolve inputs in their complete declaration scope."""

from __future__ import annotations

import copy
from datetime import date
from typing import TypeVar

import ibis.expr.types as ibis_ir
import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo._definition.types import SumAllExcept, SumAxes
from marivo.refs import DimensionKind, EntityKind, Ref, SemanticKindTag, TimeDimensionKind
from marivo.semantic._deferred_semantics import consume_field, consume_row
from marivo.semantic._definition_identity import scoped_definition_fingerprint
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.expressions import Dimension, Measure, TimeField
from marivo.semantic.expressions import _Entity as Entity
from marivo.semantic.loader import LoaderContext, loader_context
from tests.shared_fixtures import captured_catalog, in_memory_datasource_ir
from tests.shared_fixtures import resolve_test_symbol as resolve
from tests.support.refs import as_ref

_T = TypeVar("_T")


def _copy_helper(value: _T, mode: str | None) -> _T:
    return (
        copy.copy(value)
        if mode == "copy"
        else copy.deepcopy(value)
        if mode == "deepcopy"
        else value
    )


_CASES = (
    ("dimension_column", "sales.orders"),
    ("dimension", "sales.orders"),
    ("time_dimension_column", "sales.orders"),
    ("time_dimension", "sales.orders"),
    ("measure_column", "sales.orders"),
    ("relationship_from", "sales.orders"),
    ("relationship_to", "sales.customers"),
    ("join_from", "sales.orders.customer_id"),
    ("join_to", "sales.customers.id"),
    ("status_time", "sales.orders.sample_at"),
    ("additive_over", "sales.orders.region"),
    ("additive_except", "sales.orders.region"),
    ("hour_prefix", "sales.orders.day"),
    ("snapshot", "sales.snapshot_orders.day"),
    ("validity_from", "sales.validity_orders.start"),
    ("validity_to", "sales.validity_orders.end"),
    ("calendar_date", "sales.calendar.day"),
    ("calendar_level", "sales.calendar.week"),
    ("temporal_id", "sales.orders.id"),
    ("temporal_start", "sales.orders.start"),
    ("temporal_end", "sales.orders.end"),
    ("temporal_category", "sales.orders.region"),
)


def _context() -> LoaderContext:
    return LoaderContext(
        default_domain="sales", datasource_irs=(in_memory_datasource_ir("warehouse"),)
    )


def _dependencies(*, changed: str | None = None, skip_entity: str | None = None) -> None:
    """One small physical slice, with optional one-definition replacement."""
    for name in ("orders", "customers", "calendar", "snapshot_orders", "validity_orders"):
        if name == skip_entity:
            continue
        source = "replacement" if changed == f"sales.{name}" else name
        versioning = (
            ms.snapshot(
                partition_field=ms.ref.time_dimension("sales.snapshot_orders.day"), timezone="UTC"
            )
            if name == "snapshot_orders"
            else ms.validity(
                valid_from=ms.ref.time_dimension("sales.validity_orders.start"),
                valid_to=ms.ref.time_dimension("sales.validity_orders.end"),
                interval="closed_open",
                open_end=(None,),
                timezone="UTC",
            )
            if name == "validity_orders"
            else None
        )
        ms.entity(
            name=name,
            datasource=ms.ref.datasource("warehouse"),
            source=md.table(source),
            primary_key=["id"],
            versioning=versioning,
        )
    for owner, name in (
        ("orders", "id"),
        ("orders", "customer_id"),
        ("orders", "region"),
        ("customers", "id"),
        ("calendar", "week"),
        ("calendar", "previous_week"),
        ("calendar", "working"),
    ):
        path = f"sales.{owner}.{name}"
        ms.dimension_column(
            name=name,
            entity=ms.ref.entity(f"sales.{owner}"),
            column="replacement" if changed == path else name,
        )
    for owner, name in (
        ("orders", "day"),
        ("orders", "start"),
        ("orders", "end"),
        ("orders", "sample_at"),
        ("calendar", "day"),
        ("snapshot_orders", "day"),
        ("validity_orders", "start"),
        ("validity_orders", "end"),
    ):
        path = f"sales.{owner}.{name}"
        sampled = name == "sample_at"
        ms.time_dimension_column(
            name=name,
            entity=ms.ref.entity(f"sales.{owner}"),
            column="replacement" if changed == path else name,
            sample_interval=(1, "hour") if sampled else None,
            parse=ms.timestamp(timezone="UTC") if sampled else ms.strptime("%Y%m%d"),
        )


def _consume(
    family: str,
    selected: str,
    old: SemanticCatalog,
    *,
    lookup: bool,
    helper_copy: str | None = None,
) -> Ref[SemanticKindTag]:
    def entity(path: str) -> Entity | Ref[EntityKind]:
        ref = ms.ref.entity(path)
        return resolve(old, ref) if path == selected and not lookup else ref

    def dimension(path: str) -> Dimension | Ref[DimensionKind]:
        ref = ms.ref.dimension(path)
        return resolve(old, ref) if path == selected and not lookup else ref

    def time(path: str) -> TimeField | Ref[TimeDimensionKind]:
        ref = ms.ref.time_dimension(path)
        return resolve(old, ref) if path == selected and not lookup else ref

    orders = entity("sales.orders")
    if family == "dimension_column":
        return ms.dimension_column(name="probe", entity=orders, column="probe")
    if family == "dimension":

        @ms.dimension(entity=orders, name="probe")
        def calculated(rows: ibis_ir.Table) -> ibis_ir.Value:
            return rows.region.upper()

        return calculated
    if family == "time_dimension_column":
        return ms.time_dimension_column(name="probe", entity=orders, column="probe")
    if family == "time_dimension":

        @ms.time_dimension(entity=orders, name="probe")
        def calculated_time(rows: ibis_ir.Table) -> ibis_ir.Value:
            return rows.day

        return calculated_time
    if family.startswith("relationship_") or family.startswith("join_"):
        key = ms.join_on(dimension("sales.orders.customer_id"), dimension("sales.customers.id"))
        return ms.relationship(
            name="probe",
            from_entity=orders,
            to_entity=entity("sales.customers"),
            keys=[_copy_helper(key, helper_copy)],
        )
    if family in {"measure_column", "status_time", "additive_over", "additive_except"}:
        status = time("sales.orders.sample_at") if family == "status_time" else None
        policy = (
            ms.additive_over(dimension("sales.orders.region"))
            if family == "additive_over"
            else ms.additive_except(dimension("sales.orders.region"))
            if family == "additive_except"
            else ms.additive_except(status)
            if status is not None
            else ms.additive_all()
        )
        return ms.measure_column(
            name="probe",
            entity=orders,
            column="probe",
            additivity=_copy_helper(policy, helper_copy),
            status_time_dimension=status,
        )
    if family == "hour_prefix":
        parse = ms.hour_prefix(time("sales.orders.day"))
        return ms.time_dimension_column(
            name="probe",
            entity=orders,
            column="hour",
            parse=_copy_helper(parse, helper_copy),
        )
    if family == "snapshot":
        versioning = ms.snapshot(partition_field=time("sales.snapshot_orders.day"), timezone="UTC")
        return ms.entity(
            name="snapshot_orders",
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("snapshot_orders"),
            primary_key=["id"],
            versioning=_copy_helper(versioning, helper_copy),
        )
    if family.startswith("validity_"):
        validity_versioning = ms.validity(
            valid_from=time("sales.validity_orders.start"),
            valid_to=time("sales.validity_orders.end"),
            interval="closed_open",
            open_end=(None,),
            timezone="UTC",
        )
        return ms.entity(
            name="validity_orders",
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("validity_orders"),
            primary_key=["id"],
            versioning=_copy_helper(validity_versioning, helper_copy),
        )
    coverage = (date(2026, 1, 1), date(2027, 1, 1))
    if family.startswith("calendar_"):
        return ms.period_calendar(
            name="probe",
            date=time("sales.calendar.day"),
            boundary_timezone="UTC",
            coverage=coverage,
            levels={"week": dimension("sales.calendar.week")},
        )
    if family.startswith("temporal_"):
        return ms.temporal_set(
            name="probe",
            occurrence_id=dimension("sales.orders.id"),
            start=time("sales.orders.start"),
            end=time("sales.orders.end"),
            category=dimension("sales.orders.region"),
            boundary_timezone="UTC",
            coverage=coverage,
        )
    raise AssertionError(f"unknown input family: {family}")


def _version(state: SemanticCatalog, ref: Ref[SemanticKindTag]) -> str:
    return scoped_definition_fingerprint(
        root=as_ref(ref),
        definitions=state._state.definitions,
        dependencies=state._state.dependencies,
        sidecar=state._state.sidecar,
    )


@pytest.mark.parametrize(("family", "selected"), _CASES)
def test_constructed_inputs_validate_at_the_complete_consumer(
    family: str, selected: str, helper_copy: str | None = None
) -> None:
    with loader_context(_context()):
        _dependencies()
        old = captured_catalog()
    for mode in ("same", "updated", "lookup_forward"):
        ctx = _context()
        with loader_context(ctx):
            target = _consume(
                family, selected, old, lookup=mode == "lookup_forward", helper_copy=helper_copy
            )
            # All referenced declarations arrive after the consumer, including helper-record inputs.
            skip = (
                "snapshot_orders"
                if family == "snapshot"
                else "validity_orders"
                if family.startswith("validity_")
                else None
            )
            _dependencies(changed=selected if mode != "same" else None, skip_entity=skip)
            state = captured_catalog()
        assert as_ref(target) in state._state.definitions
        # A matching constructed input and explicit lookup produce the same definition identity.
        with loader_context(_context()):
            lookup_target = _consume(family, selected, old, lookup=True)
            _dependencies(changed=selected if mode != "same" else None, skip_entity=skip)
            lookup_state = captured_catalog()
        assert _version(state, target) == _version(lookup_state, lookup_target)
        selected_ref = next(ref for ref in state._state.definitions if ref.path == selected)
        assert (_version(state, selected_ref) == _version(old, selected_ref)) is (mode == "same")
        assert (
            state._state.definitions[selected_ref] == lookup_state._state.definitions[selected_ref]
        )


def test_capture_helpers_are_frozen_computational_records() -> None:
    with loader_context(_context()):
        _dependencies()
        state = captured_catalog()
    region = resolve(state, ms.ref.dimension("sales.orders.region"))
    policy = ms.additive_over(region)
    assert policy.coordinates == ("sales.orders.region",)
    assert policy.version == 1
    with pytest.raises((AttributeError, TypeError)):
        policy.coordinates = ("replacement",)  # type: ignore[misc]  # intentional frozen-value mutation


@pytest.mark.parametrize("policy_kind", ("over", "except"))
@pytest.mark.parametrize("mode", ("same", "updated", "lookup"))
def test_pure_measure_additivity_uses_its_frozen_row_scope(policy_kind: str, mode: str) -> None:
    with loader_context(_context()):
        _dependencies()
        old = resolve(captured_catalog(), ms.ref.dimension("sales.orders.region"))
    with loader_context(_context()):
        _dependencies(changed="sales.orders.region" if mode != "same" else None)
        amount_ref = ms.measure_column(
            name="amount",
            entity=ms.ref.entity("sales.orders"),
            column="amount",
            additivity=ms.non_additive(),
            unit="CNY",
        )
        state = captured_catalog()
        amount = resolve(state, amount_ref)
        region = resolve(state, ms.ref.dimension("sales.orders.region"))
    axis = ms.ref.dimension("sales.orders.region") if mode == "lookup" else old
    for helper_copy in (None, "copy", "deepcopy"):
        policy = ms.additive_over(axis) if policy_kind == "over" else ms.additive_except(axis)
        policy = _copy_helper(policy, helper_copy)

        @ms.measure(additivity=policy)
        def granted() -> Measure:
            return amount

        assert isinstance(
            consume_row(granted).permission, SumAxes if policy_kind == "over" else SumAllExcept
        )
        assert consume_row(granted).permission.axes == (consume_field(region).reference,)
        assert (consume_field(region).reference == consume_field(old).reference) is (mode == "same")
        assert (
            consume_row(granted).root == consume_row(amount).root
            and consume_row(granted).default_time == consume_row(amount).default_time
        )


@pytest.mark.parametrize(
    ("family", "selected"),
    (
        ("snapshot", "sales.snapshot_orders.day"),
        ("validity_from", "sales.validity_orders.start"),
        ("hour_prefix", "sales.orders.day"),
    ),
)
def test_exact_versions_include_physical_helper_dependencies(family: str, selected: str) -> None:
    with loader_context(_context()):
        _dependencies()
        old = captured_catalog()
    versions: list[str] = []
    for changed in (None, selected):
        with loader_context(_context()):
            target = _consume(family, selected, old, lookup=True)
            skip = (
                "snapshot_orders"
                if family == "snapshot"
                else "validity_orders"
                if family.startswith("validity_")
                else None
            )
            _dependencies(changed=changed, skip_entity=skip)
            versions.append(_version(captured_catalog(), target))
    assert versions[0] != versions[1]
