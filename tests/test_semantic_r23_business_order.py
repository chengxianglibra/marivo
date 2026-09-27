"""Independent R2.3 declaration checks for business order and temporal roles."""

from __future__ import annotations

import textwrap

import pytest

import marivo.semantic as ms
from marivo.semantic.catalog import BusinessOrderEntry, SemanticCatalog
from marivo.semantic.errors import ErrorKind

_DOMAIN = (
    'import marivo.semantic as ms\nms.domain(name="commerce", owner="Analytics", default=True)\n'
)
_OBJECTS = """
import marivo.datasource as md
import marivo.semantic as ms

warehouse = ms.ref.datasource("warehouse")
orders = ms.entity(name="orders", datasource=warehouse, source=md.table("orders"), primary_key=["order_id"])
events = ms.entity(name="events", datasource=warehouse, source=md.table("events"), primary_key=["event_id"])
order_id = ms.dimension_column(name="order_id", entity=orders, column="order_id")
event_id = ms.dimension_column(name="event_id", entity=events, column="event_id")
event_order_id = ms.dimension_column(name="order_id", entity=events, column="order_id")
sequence_number = ms.dimension_column(name="sequence_number", entity=events, column="sequence_number")
event_time = ms.time_dimension_column(
    name="event_time", entity=events, column="event_time", granularity="second",
    parse=ms.timestamp(timezone="UTC"),
)
event_to_order = ms.relationship(
    name="event_to_order", from_entity=events, to_entity=orders,
    keys=[ms.join_on(event_order_id, order_id)],
)

@ms.event(name="activated", identity=(event_id,), occurred_at=event_time,
          participants=(ms.participant(name="order", path=(event_to_order,), cardinality="one"),))
def activated(rows):
    return ms.all_rows()

@ms.event(name="deactivated", identity=(event_id,), occurred_at=event_time,
          participants=(ms.participant(name="order", path=(event_to_order,), cardinality="one"),))
def deactivated(rows):
    return ms.all_rows()

activate_role = ms.participant_role(event=activated, name="order")
deactivate_role = ms.participant_role(event=deactivated, name="order")
order = ms.business_order(
    name="subject_order", subject=orders,
    sequences=(
        ms.event_sequence(activated, sequence_number, order=SEQUENCE_ORDER),
        ms.event_sequence(deactivated, sequence_number, order=SEQUENCE_ORDER),
    ),
    conflicts=(ORDER_CONFLICTS),
    ai_context=ms.ai_context(business_definition="Ledger sequence and close precedence per order."),
)
active = ms.lifecycle_state(name="active", initial=True)
closed = ms.lifecycle_state(name="closed", terminal=True)
model = ms.state_model(
    name="order_model", subject=orders, states=(active, closed),
    transitions=(
        ms.inception(on=activated),
        ms.transition(from_state=active, on=deactivated, to_state=closed),
    ),
    business_order=order,
)
"""


def _source(
    *, order: str = '"integer"', conflicts: str = "ms.precedes(activate_role, deactivate_role),"
) -> str:
    return (
        textwrap.dedent(_OBJECTS)
        .replace("SEQUENCE_ORDER", order)
        .replace("ORDER_CONFLICTS", conflicts)
    )


def _project(semantic_project_factory, source: str, *, load: bool = True):
    return semantic_project_factory(
        {"commerce/_domain.py": _DOMAIN, "commerce/objects.py": source}, load=load
    )


def test_order_loads_with_exact_ref_and_transitive_state_model_identity(
    semantic_project_factory, capsys
) -> None:
    project = _project(semantic_project_factory, _source())
    registry = project._registry
    assert registry is not None
    order_ir = registry.business_orders["commerce.subject_order"]
    assert order_ir.subject == "commerce.orders"
    assert tuple(item.participant_role for item in order_ir.sequences) == ("order", "order")
    assert order_ir.sequences[0].value_ref == "commerce.events.sequence_number"
    assert order_ir.conflicts[0].before_event == "commerce.activated"
    assert registry.state_models["commerce.order_model"].business_order == order_ir.semantic_id

    catalog = ms.load(workspace_dir=project._workspace_dir)
    ref = ms.ref.business_order("commerce.subject_order")
    entry = catalog.require(ref)
    assert isinstance(entry, BusinessOrderEntry)
    assert catalog.business_orders.get(ref) is entry
    assert catalog.domains.get("commerce").business_orders.get(ref) is entry
    assert catalog.entities.get("orders").business_orders.get(ref) is entry
    assert entry.details().definition_fingerprint.startswith("sha256:")
    assert entry.details().source_location.file.endswith("commerce/objects.py")
    entry.details().show()
    assert "sequence_number" in capsys.readouterr().out
    model = catalog.require(ms.ref.state_model("commerce.order_model"))
    assert model.details().business_order == ref
    order_readiness = catalog.readiness([ref])
    assert order_readiness.analysis_ready_inputs == ()
    assert any(item.kind == "business_order_values_unverified" for item in order_readiness.warnings)
    model_readiness = catalog.readiness([model.ref])
    assert ms.ref.state_model("commerce.order_model") in model_readiness.analysis_ready_inputs
    assert any(item.kind == "business_order_values_unverified" for item in model_readiness.warnings)


def test_order_value_contract_changes_model_fingerprint(semantic_project_factory, tmp_path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = SemanticCatalog(
        semantic_project_factory(
            {
                "commerce/_domain.py": _DOMAIN,
                "commerce/objects.py": _source(order='("activate", "deactivate")'),
            },
            workspace_dir=first_dir,
        )
    )
    second = SemanticCatalog(
        semantic_project_factory(
            {
                "commerce/_domain.py": _DOMAIN,
                "commerce/objects.py": _source(order='("deactivate", "activate")'),
            },
            workspace_dir=second_dir,
        )
    )
    order_ref = ms.ref.business_order("commerce.subject_order")
    model_ref = ms.ref.state_model("commerce.order_model")
    assert (
        first.require(order_ref).details().definition_fingerprint
        != second.require(order_ref).details().definition_fingerprint
    )
    assert (
        first.require(model_ref).details().definition_fingerprint
        != second.require(model_ref).details().definition_fingerprint
    )


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        (
            _source().replace(
                "ms.event_sequence(activated, sequence_number",
                "ms.event_sequence(activated, event_id",
            ),
            ErrorKind.INVALID_BUSINESS_ORDER,
        ),
        (
            _source().replace(
                'name="order", path=(event_to_order,), cardinality="one"',
                'name="order", path=(event_to_order,), cardinality="optional_one"',
            ),
            ErrorKind.INVALID_BUSINESS_ORDER,
        ),
        (
            _source(
                conflicts="ms.precedes(activate_role, deactivate_role), ms.precedes(activate_role, deactivate_role),"
            ),
            ErrorKind.INVALID_BUSINESS_ORDER,
        ),
        (
            _source(
                conflicts="ms.precedes(activate_role, deactivate_role), ms.precedes(deactivate_role, activate_role),"
            ),
            ErrorKind.INVALID_BUSINESS_ORDER,
        ),
        (_source(order='("activate", "activate")'), ErrorKind.INVALID_BUSINESS_ORDER),
        (
            _source().replace(
                'ms.event_sequence(deactivated, sequence_number, order="integer")',
                'ms.event_sequence(deactivated, sequence_number, order=("activate", "deactivate"))',
            ),
            ErrorKind.INVALID_BUSINESS_ORDER,
        ),
        (
            _source().replace(
                'name="subject_order", subject=orders,', 'name="subject_order", subject=events,'
            ),
            ErrorKind.INVALID_BUSINESS_ORDER,
        ),
    ],
)
def test_invalid_order_authority_fails_with_repair(
    semantic_project_factory, source: str, kind: ErrorKind
) -> None:
    result = _project(semantic_project_factory, source, load=False).load()
    assert result.status == "errored"
    errors = [error for error in result.errors if error.kind == kind]
    assert errors
    assert errors[0].expected
    assert errors[0].received
    assert errors[0].repair is not None


def test_simultaneous_opposite_transitions_have_no_implicit_occurrence_id_order(
    semantic_project_factory,
) -> None:
    catalog = SemanticCatalog(_project(semantic_project_factory, _source(conflicts="")))
    order = catalog.business_orders.get("subject_order").details()
    assert order.conflicts == ()
    assert all(
        value != ms.ref.dimension("commerce.events.event_id") for _, value, _, _ in order.sequences
    )
    assert catalog.state_models.get("order_model").details().business_order == order.ref


def test_state_model_order_must_cover_each_trigger(semantic_project_factory) -> None:
    source = _source(conflicts="").replace(
        'ms.event_sequence(deactivated, sequence_number, order="integer"),', ""
    )
    result = _project(semantic_project_factory, source, load=False).load()
    assert result.status == "errored"
    assert any(
        error.kind == ErrorKind.INVALID_STATE_MODEL and "deactivated" in (error.received or "")
        for error in result.errors
    )


def test_calendar_rejects_declared_timestamp_date_axis(semantic_project_factory) -> None:
    source = """
import datetime
import marivo.datasource as md
import marivo.semantic as ms
calendar = ms.entity(name="calendar", datasource=ms.ref.datasource("warehouse"), source=md.table("calendar"))
calendar_date = ms.time_dimension_column(
    name="calendar_date", entity=calendar, column="calendar_date", granularity="day",
    parse=ms.timestamp(timezone="UTC"),
)
week = ms.dimension_column(name="week", entity=calendar, column="week")
ms.period_calendar(name="fiscal", date=calendar_date, boundary_timezone="UTC",
    coverage=(datetime.date(2026, 1, 1), datetime.date(2026, 1, 3)), levels={"week": week})
"""
    result = _project(semantic_project_factory, textwrap.dedent(source), load=False).load()
    assert result.status == "errored"
    assert any("civil-date TimeDimension" in (error.expected or "") for error in result.errors)
