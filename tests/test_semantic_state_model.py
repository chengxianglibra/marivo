"""StateModel authoring, canonical trigger, catalog, and readiness contracts."""

from __future__ import annotations

import textwrap

import ibis
import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.refs import Ref, SemanticKind
from marivo.semantic.catalog import SemanticCatalog, StateModelEntry
from marivo.semantic.errors import (
    ErrorKind,
    SemanticDecoratorError,
    SemanticRuntimeError,
)
from marivo.semantic.preview_checks import preview_dependency_entities
from marivo.semantic.state_model import _resolve_model_state

_DOMAIN = """\
import marivo.semantic as ms
ms.domain(name="commerce", owner="Analytics", default=True)
"""

_OBJECTS = """\
import marivo.datasource as md
import marivo.semantic as ms

warehouse = ms.ref.datasource("warehouse")
orders = ms.entity(
    name="orders", datasource=warehouse, source=md.table("orders"),
    primary_key=["order_id"],
    ai_context=ms.ai_context(business_definition="One row per order."),
)
event_log = ms.entity(
    name="event_log", datasource=warehouse, source=md.table("event_log"),
    primary_key=["event_id"],
    ai_context=ms.ai_context(business_definition="One row per event."),
)
order_id = ms.dimension_column(
    name="order_id", entity=orders, column="order_id",
    ai_context=ms.ai_context(business_definition="Order identity."),
)
event_id = ms.dimension_column(
    name="event_id", entity=event_log, column="event_id",
    ai_context=ms.ai_context(business_definition="Event identity."),
)
event_order_id = ms.dimension_column(
    name="order_id", entity=event_log, column="order_id",
    ai_context=ms.ai_context(business_definition="Event order identity."),
)
event_type = ms.dimension_column(
    name="event_type", entity=event_log, column="event_type",
    ai_context=ms.ai_context(business_definition="Event type."),
)
event_time = ms.time_dimension_column(
    name="event_time", entity=event_log, column="event_time",
    granularity="second", parse=ms.timestamp(timezone="UTC"), is_default=True,
    ai_context=ms.ai_context(business_definition="Occurrence time."),
)
event_to_order = ms.relationship(
    name="event_to_order", from_entity=event_log, to_entity=orders,
    keys=[ms.join_on(event_order_id, order_id)],
)

@ms.event(
    name="order_created", identity=(event_id,), occurred_at=event_time,
    participants=(
        ms.participant(name="order", path=(event_to_order,), cardinality="one"),
    ),
    ai_context=ms.ai_context(business_definition="An order was created."),
)
def order_created(rows):
    return ms.bind(event_type, rows) == "created"

@ms.event(
    name="payment_captured", identity=(event_id,), occurred_at=event_time,
    participants=(
        ms.participant(name="order", path=(event_to_order,), cardinality="one"),
    ),
    ai_context=ms.ai_context(business_definition="Payment was captured."),
)
def payment_captured(rows):
    return ms.bind(event_type, rows) == "paid"

created = ms.lifecycle_state(name="created", initial=True)
paid = ms.lifecycle_state(name="paid", terminal=True)
order_lifecycle = ms.state_model(
    name="order_lifecycle",
    subject=orders,
    states=(created, paid),
    transitions=(
        ms.inception(on=order_created),
        ms.transition(from_state=created, on=payment_captured, to_state=paid),
    ),
    ai_context=ms.ai_context(
        business_definition="Commercial order lifecycle.",
        guardrails=("Use only governed order Events.",),
    ),
)
"""


def _project(semantic_project_factory, *, objects: str = _OBJECTS):
    return semantic_project_factory(
        {
            "commerce/_domain.py": textwrap.dedent(_DOMAIN),
            "commerce/objects.py": textwrap.dedent(objects),
        }
    )


def test_state_model_compiles_canonical_roles_and_catalog_entry(
    semantic_project_factory,
) -> None:
    project = _project(semantic_project_factory)
    registry = project._registry
    assert registry is not None
    model = registry.state_models["commerce.order_lifecycle"]
    assert model.subject == "commerce.orders"
    assert tuple(state.name for state in model.states) == ("created", "paid")
    assert model.inceptions[0].trigger.participant_role == "order"
    assert model.transitions[0].trigger.event_ref == "commerce.payment_captured"

    catalog = SemanticCatalog(project)
    entry = catalog.state_models.get("order_lifecycle")
    assert type(entry) is StateModelEntry
    assert type(entry.ref) is Ref
    assert entry.ref.kind is SemanticKind.STATE_MODEL
    assert catalog.require(ms.ref.state_model("commerce.order_lifecycle")) is entry
    assert catalog.domains.get("commerce").state_models.get("order_lifecycle") is entry
    assert catalog.entities.get("orders").state_models.get("order_lifecycle") is entry
    assert entry.details().states == (
        ("created", True, False),
        ("paid", False, True),
    )
    assert entry.details().definition_fingerprint.startswith("sha256:")
    assert entry.details().inceptions[0][2] == (ms.ref.relationship("commerce.event_to_order"),)
    assert "state.created" in entry.render()
    assert "payment_captured" in entry.details().render()
    assert catalog.require(entry.ref) is entry
    readiness = catalog.readiness(refs=(entry,))
    assert entry.ref in readiness.analysis_ready_refs
    assert preview_dependency_entities(
        entry.ref.path,
        registry=registry,
    ) == ("commerce.event_log", "commerce.orders")
    resolved, fingerprint = _resolve_model_state(
        ms.model_state(model=entry.ref, name="paid"),
        registry=registry,
        sidecar=catalog._state.sidecar,
    )
    assert resolved is model
    assert fingerprint == entry.details().definition_fingerprint
    with pytest.raises(SemanticRuntimeError) as exc_info:
        _resolve_model_state(
            ms.model_state(model=entry.ref, name="missing"),
            registry=registry,
            sidecar=catalog._state.sidecar,
        )
    assert exc_info.value.kind == ErrorKind.MODEL_STATE_MISMATCH
    assert exc_info.value.repair is not None
    assert len(exc_info.value.repair.candidates) == 2


def test_entity_and_state_model_previews_report_their_exact_kinds(
    semantic_project_factory,
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "warehouse.duckdb"
    backend = ibis.duckdb.connect(str(database_path))
    backend.raw_sql("CREATE TABLE orders (order_id VARCHAR)")
    backend.raw_sql(
        "CREATE TABLE event_log ("
        "event_id VARCHAR, order_id VARCHAR, event_type VARCHAR, event_time TIMESTAMP)"
    )
    backend.raw_sql("INSERT INTO orders VALUES ('o1')")
    backend.raw_sql(
        "INSERT INTO event_log VALUES "
        "('e1', 'o1', 'created', '2026-07-01 00:00:00'), "
        "('e2', 'o1', 'paid', '2026-07-02 00:00:00')"
    )
    backend.disconnect()
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(database_path)!r})\n"
            ),
            "commerce/_domain.py": textwrap.dedent(_DOMAIN),
            "commerce/objects.py": textwrap.dedent(_OBJECTS),
        }
    )
    monkeypatch.chdir(tmp_path)
    catalog = SemanticCatalog(project)
    datasource = ms.ref.datasource("warehouse")
    orders_snapshot = md.inspect(datasource, md.table("orders")).sample(
        scope=md.unpruned(max_rows=2, timeout_seconds=30),
        columns=("order_id",),
    )
    events_snapshot = md.inspect(datasource, md.table("event_log")).sample(
        scope=md.unpruned(max_rows=2, timeout_seconds=30),
        columns=("event_id", "order_id", "event_type", "event_time"),
    )
    orders = catalog.require(ms.ref.entity("commerce.orders")).ref
    events = catalog.require(ms.ref.entity("commerce.event_log")).ref

    entity_preview = catalog.preview(orders, using=orders_snapshot)
    model_preview = catalog.preview(
        catalog.require(ms.ref.state_model("commerce.order_lifecycle")).ref,
        using={
            orders: orders_snapshot,
            events: events_snapshot,
        },
    )

    assert entity_preview.kind == "semantic_dataset"
    assert model_preview.kind == "semantic_state_model"


def test_state_values_and_model_state_handle_are_immutable() -> None:
    created = ms.lifecycle_state(name="created", initial=True)
    assert created.initial is True
    with pytest.raises(AttributeError):
        created.name = "changed"  # type: ignore[misc]
    handle = ms.model_state(
        model=ms.ref.state_model("commerce.order_lifecycle"),
        name="paid",
    )
    assert handle.key == "state_model:commerce.order_lifecycle#state:paid"
    with pytest.raises(AttributeError):
        handle.name = "created"  # type: ignore[misc]


def test_state_model_fingerprint_tracks_event_definition_without_global_handle_state(
    semantic_project_factory,
    tmp_path,
) -> None:
    first = SemanticCatalog(
        _project(
            semantic_project_factory,
            objects=_OBJECTS,
        )
    )
    changed_source = _OBJECTS.replace(
        'return ms.bind(event_type, rows) == "paid"',
        'return ms.bind(event_type, rows) == "captured"',
    )
    second_workspace = tmp_path / "other"
    second_workspace.mkdir()
    second = SemanticCatalog(
        semantic_project_factory(
            {
                "commerce/_domain.py": textwrap.dedent(_DOMAIN),
                "commerce/objects.py": textwrap.dedent(changed_source),
            },
            workspace_dir=second_workspace,
        )
    )
    first_model = first.state_models.get("order_lifecycle")
    second_model = second.state_models.get("order_lifecycle")
    assert (
        first_model.details().definition_fingerprint
        != second_model.details().definition_fingerprint
    )
    assert ms.model_state(model=first_model.ref, name="paid") == ms.model_state(
        model=second_model.ref,
        name="paid",
    )


def test_transition_requires_exact_member_identity(semantic_project_factory) -> None:
    source = _OBJECTS.replace(
        "ms.transition(from_state=created, on=payment_captured, to_state=paid)",
        (
            "ms.transition("
            "from_state=ms.lifecycle_state(name='created', initial=True), "
            "on=payment_captured, to_state=paid)"
        ),
    )
    project = semantic_project_factory(
        {
            "commerce/_domain.py": textwrap.dedent(_DOMAIN),
            "commerce/objects.py": textwrap.dedent(source),
        },
        load=False,
    )
    result = project.load()
    assert result.status == "errored"
    assert ErrorKind.INVALID_STATE_MODEL in {error.kind for error in result.errors}


def test_event_role_ambiguity_requires_explicit_handle(semantic_project_factory) -> None:
    ambiguous = _OBJECTS.replace(
        'ms.participant(name="order", path=(event_to_order,), cardinality="one"),',
        (
            'ms.participant(name="buyer_order", path=(event_to_order,), cardinality="one"),\n'
            '        ms.participant(name="seller_order", path=(event_to_order,), cardinality="one"),'
        ),
    )
    project = semantic_project_factory(
        {
            "commerce/_domain.py": textwrap.dedent(_DOMAIN),
            "commerce/objects.py": textwrap.dedent(ambiguous),
        },
        load=False,
    )
    result = project.load()
    assert result.status == "errored"
    errors = [
        error for error in result.errors if error.kind == ErrorKind.AMBIGUOUS_PARTICIPANT_ROLE
    ]
    assert errors
    candidates = tuple(errors[0].details["candidates"])
    assert len(candidates) == 2
    assert all("ms.participant_role" in item for item in candidates)
    assert errors[0].repair is not None
    assert errors[0].repair.candidates == candidates


def test_explicit_role_resolves_an_ambiguous_event(
    semantic_project_factory,
) -> None:
    explicit = (
        _OBJECTS.replace(
            'ms.participant(name="order", path=(event_to_order,), cardinality="one"),',
            (
                'ms.participant(name="buyer_order", path=(event_to_order,), cardinality="one"),\n'
                '        ms.participant(name="seller_order", path=(event_to_order,), cardinality="one"),'
            ),
        )
        .replace(
            "ms.inception(on=order_created)",
            ("ms.inception(on=ms.participant_role(event=order_created, name='buyer_order'))"),
        )
        .replace(
            "on=payment_captured, to_state=paid",
            ("on=ms.participant_role(event=payment_captured, name='seller_order'), to_state=paid"),
        )
    )
    registry = _project(semantic_project_factory, objects=explicit)._registry
    assert registry is not None
    model = registry.state_models["commerce.order_lifecycle"]
    assert model.inceptions[0].trigger.participant_role == "buyer_order"
    assert model.transitions[0].trigger.participant_role == "seller_order"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            'created = ms.lifecycle_state(name="created", initial=True)',
            'created = ms.lifecycle_state(name="created")',
        ),
        (
            'paid = ms.lifecycle_state(name="paid", terminal=True)',
            'paid = ms.lifecycle_state(name="paid", initial=True, terminal=True)',
        ),
        (
            "states=(created, paid),",
            "states=(created, created),",
        ),
        (
            "transitions=(\n        ms.inception(on=order_created),",
            (
                "transitions=(\n"
                "        ms.inception(on=order_created),\n"
                "        ms.inception(on=order_created),"
            ),
        ),
        (
            "ms.transition(from_state=created, on=payment_captured, to_state=paid),",
            (
                "ms.transition(from_state=created, on=payment_captured, to_state=paid),\n"
                "        ms.transition(from_state=paid, on=payment_captured, to_state=created),"
            ),
        ),
        (
            "ms.transition(from_state=created, on=payment_captured, to_state=paid),",
            (
                "ms.transition(from_state=created, on=payment_captured, to_state=paid),\n"
                "        ms.transition("
                "from_state=created, on=payment_captured, to_state=created),"
            ),
        ),
    ],
)
def test_invalid_model_closure_fails(
    semantic_project_factory,
    old: str,
    new: str,
) -> None:
    source = _OBJECTS.replace(old, new)
    project = semantic_project_factory(
        {
            "commerce/_domain.py": textwrap.dedent(_DOMAIN),
            "commerce/objects.py": textwrap.dedent(source),
        },
        load=False,
    )
    result = project.load()
    assert result.status == "errored"
    assert ErrorKind.INVALID_STATE_MODEL in {error.kind for error in result.errors}


def test_seedless_model_loads_but_is_not_replay_ready(
    semantic_project_factory,
) -> None:
    seedless = _OBJECTS.replace(
        "ms.inception(on=order_created),",
        "",
    )
    catalog = SemanticCatalog(_project(semantic_project_factory, objects=seedless))
    model = catalog.state_models.get("order_lifecycle")
    assert catalog.require(model.ref) is model
    readiness = catalog.readiness(refs=(model,))
    assert model.ref not in readiness.analysis_ready_refs
    assert any(issue.kind == "state_model_seed_missing" for issue in readiness.blockers)


def test_invalid_state_names_fail_eagerly() -> None:
    with pytest.raises(SemanticDecoratorError) as exc_info:
        ms.lifecycle_state(name="Paid")
    assert exc_info.value.kind == ErrorKind.INVALID_STATE_MODEL
