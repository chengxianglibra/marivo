"""Explicit Frame key definitions retain live inputs outside semantic capture."""

import gc
import weakref
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest
from ibis.common.deferred import Deferred, Just, Sequence

import marivo.analysis as mv
from marivo._definition.deferred import (
    DeclarationRecord,
    DeclarationResolver,
    canonical_recipe,
    recipe_fingerprint,
    resolver_of,
    semantic_input,
)
from marivo._definition.expressions import ContributionFilter, PredicateExpression, RowExpression
from marivo._definition.graph import (
    _encode_fields,
    canonical_bytes,
    computation_fingerprint,
    direct_refs,
    walk,
)
from marivo._definition.relations import FrameKeyAxis, FrameKeys, PopulationProgram
from marivo._definition.types import AxisBinding, ExactRef
from marivo.analysis.core.artifact_data import (
    ArtifactData,
    BuiltinCalendarData,
    CalendarDataAxis,
    DataAxis,
    DataColumn,
    DataSchema,
    FrozenCalendarData,
    PairedDataAxis,
    ParquetHandle,
)
from marivo.analysis.core.data_types import ScalarDataType
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    ExpressionFrameBacking,
    SourceFrameBinding,
)
from marivo.analysis.core.frame_metadata import EvaluationRecord, FrameResultMetadata
from marivo.analysis.core.frame_structure import PendingDataSchema
from marivo.analysis.core.lineage import empty as empty_lineage
from marivo.analysis.dsl import frame_sources
from marivo.analysis.dsl._frame_inputs import (
    FrameKeyInput,
    collect_frame_key_inputs,
    merge_frame_key_inputs,
)
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.frame_population import population_from
from marivo.analysis.dsl.population import Population, in_population, product
from marivo.analysis.materialization.contracts import FileEntry, LocalReceipt
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization.terminal_store import describe
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.refs import ref
from marivo.semantic._deferred_semantics import consume_field, consume_metric, consume_predicate
from marivo.semantic._expression_values import RowExpression as RowValue
from marivo.semantic.expressions import _Resolver
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_get, catalog_value


@pytest.fixture
def session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(wall_clock_timezone='UTC', name='warehouse', path={str(tmp_path / 'unopened.duckdb')!r})\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Analytics', default=True)\n"
            ),
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
warehouse = ms.ref.datasource('warehouse')
facts = ms.entity(name='facts', datasource=warehouse, source=md.table('facts'), primary_key=['tenant', 'id'])
tenant = ms.dimension_column(name='tenant', entity=facts, column='tenant')
id = ms.dimension_column(name='id', entity=facts, column='id')
customer_id = ms.dimension_column(name='customer_id', entity=facts, column='customer_id')
region = ms.dimension_column(name='region', entity=facts, column='region')
channel = ms.dimension_column(name='channel', entity=facts, column='channel')
amount = ms.measure_column(name='amount', entity=facts, column='amount', unit='USD', additivity=ms.additive_all())
customers = ms.entity(name='customers', datasource=warehouse, source=md.table('customers'), primary_key=['id'])
cid = ms.dimension_column(name='id', entity=customers, column='id')
segment = ms.dimension_column(name='segment', entity=customers, column='segment')
customer = ms.relationship(name='customer', from_entity=facts, to_entity=customers, keys=[ms.join_on(customer_id, cid)])
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("frame-population-construction", timezone="UTC")


def _metadata(result_id: str = "fixed-selection") -> FrameResultMetadata:
    return FrameResultMetadata(
        result_id,
        EvaluationRecord(datetime(2026, 10, 19, tzinfo=timezone.utc), (), ()),
    )


def _memory(
    session: Session,
    axes: tuple[DataAxis, ...],
    rows: tuple[tuple[str | int | None, ...], ...],
) -> AnalysisFrame:
    schema = DataSchema(axes, ())
    table = pd.DataFrame(rows, columns=[column.slot_id for column in schema.columns])
    result = CompletedFrameResult(CompletedFrameData(schema, table), _metadata())
    return AnalysisFrame._from_memory(session._runtime.local, result)


def _fields(session: Session) -> AnalysisFrame:
    return _memory(
        session,
        (
            DataAxis(
                "a", (DataColumn("k0", "first", ScalarDataType("string"), True),), name="first"
            ),
            DataAxis(
                "b", (DataColumn("k1", "second", ScalarDataType("string"), False),), name="second"
            ),
        ),
        (("east", "A"), ("west", "B")),
    )


def _identities(session: Session, *, composite: bool = True) -> AnalysisFrame:
    columns = (
        (DataColumn("tenant", "tenant", ScalarDataType("string"), False),) if composite else ()
    ) + (DataColumn("id", "id", ScalarDataType("int64"), False),)
    return _memory(
        session,
        (DataAxis("members", columns, name="selected", kind="member"),),
        (("t1", 1),) if composite else ((1,),),
    )


def _bridge(session: Session, frame: AnalysisFrame, *, identity: bool = False) -> Population:
    target = (
        catalog_get(session.catalog, "entity:sales.facts", kind="entity")
        if identity
        else catalog_value(session.catalog, "sales.facts.region", kind="dimension")
    )
    return population_from(
        session._runtime.local, session.catalog._state, frame, on=((frame.axes[0], target),)
    )


def _ids(population: Population | Deferred) -> tuple[str, ...]:
    assert isinstance(population, Population)
    return tuple(item.input_id for item in population._frame_inputs)


def test_factory_is_metadata_only_and_definition_is_inert(
    session: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    frame = _fields(session)
    region = catalog_value(session.catalog, "sales.facts.region", kind="dimension")
    state = session.catalog._state

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("construction cannot read rows, schemas or sources")

    monkeypatch.setattr(CompletedFrameData, "copy_rows", forbidden)
    monkeypatch.setattr(CompletedFrameData, "preview_rows", forbidden)
    monkeypatch.setattr(LocalFrameServices, "read_complete", forbidden)
    monkeypatch.setattr(SourceSession, "bind", forbidden)
    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    population = population_from(
        session._runtime.local, state, frame, on=((frame.axes[0], region),)
    )
    assert isinstance(population._definition, PopulationProgram)
    leaf = population._definition.root
    assert isinstance(leaf, FrameKeys)
    assert leaf.input_id == frame._input_id
    assert leaf.bindings[0].key_names == ("first",)
    assert leaf.bindings[0].target.field == consume_field(region).reference
    assert population._frame_inputs[0].frame is frame
    closure = walk((population._definition,), resolve=lambda _: None)
    assert closure.order == (leaf, population._definition)
    assert consume_field(region).reference in direct_refs(population._definition)
    encoded = canonical_bytes(_encode_fields(leaf, {})).decode()
    assert frame._input_id in encoded and "FrameKeys" in encoded
    assert all(word not in encoded for word in ("AnalysisFrame", "Session", "_rows", "connection"))
    population.show()


@pytest.mark.parametrize("empty", (False, True))
def test_explicit_empty_mapping_keeps_the_input_dependency(session: Session, empty: bool) -> None:
    source = _fields(session)
    if empty:
        assert isinstance(source._schema, DataSchema)
        source = _memory(session, source._schema.axes, ())
    population = population_from(session._runtime.local, session.catalog._state, source, on=())
    assert isinstance(population._definition, PopulationProgram)
    assert population._definition.root == FrameKeys(source._input_id, ())
    assert population._definition.axes == () and _ids(population) == (source._input_id,)
    # Construction cannot decide whether the projected empty tuple exists.
    assert population._frame_inputs[0].frame is source


def test_mapping_order_and_fingerprints_capture_exact_input_and_targets(session: Session) -> None:
    frame = _fields(session)
    region = catalog_value(session.catalog, "sales.facts.region", kind="dimension")
    channel = catalog_value(session.catalog, "sales.facts.channel", kind="dimension")
    first = population_from(
        session._runtime.local,
        session.catalog._state,
        frame,
        on=((frame.axes[1], channel), (frame.axes[0], region)),
    )
    assert isinstance(first._definition, PopulationProgram)
    assert isinstance(first._definition.root, FrameKeys)
    assert tuple(binding.token for binding in first._definition.root.bindings) == ("b", "a")
    other = _fields(session)
    assert computation_fingerprint(_bridge(session, frame)._definition) != computation_fingerprint(
        _bridge(session, other)._definition
    )
    assert hash(first) == hash(Population(first._definition))
    assert first == Population(first._definition)


@pytest.mark.parametrize("operation", ("project", "union", "intersection", "difference", "where"))
def test_population_algebra_retains_every_required_input(session: Session, operation: str) -> None:
    left, right = _bridge(session, _fields(session)), _bridge(session, _fields(session))
    if operation == "project":
        result = left.project(by=())
        expected = _ids(left)
    elif operation == "union":
        result, expected = left.union(right), (*_ids(left), *_ids(right))
    elif operation == "intersection":
        result, expected = left.intersection(right), (*_ids(left), *_ids(right))
    elif operation == "difference":
        result, expected = left.difference(right), (*_ids(left), *_ids(right))
    else:
        result = left.where(in_population(left) | ~in_population(right))
        expected = (*_ids(left), *_ids(right))
    assert _ids(result) == expected
    assert len(set(_ids(result))) == len(_ids(result))


def test_classify_image_preimage_and_product_retain_carriers(session: Session) -> None:
    selected = _bridge(session, _identities(session), identity=True)
    customer_frame = _identities(session, composite=False)
    customer = catalog_get(session.catalog, "entity:sales.customers", kind="entity")
    targets = population_from(
        session._runtime.local,
        session.catalog._state,
        customer_frame,
        on=((customer_frame.axes[0], customer),),
    )
    relationship = catalog_get(session.catalog, "relationship:sales.customer", kind="relationship")
    assert _ids(
        selected.classify(
            by=(catalog_value(session.catalog, "sales.facts.region", kind="dimension"),)
        )
    ) == _ids(selected)
    assert _ids(selected.image(via=(relationship,))) == _ids(selected)
    assert _ids(selected.preimage(targets, via=(relationship,))) == (
        *_ids(selected),
        *_ids(targets),
    )
    assert _ids(product(selected, targets)) == (*_ids(selected), *_ids(targets))


def test_predicates_and_deferred_recipes_preserve_inputs_before_consumption(
    session: Session,
) -> None:
    left, right = _bridge(session, _fields(session)), _bridge(session, _fields(session))
    condition = in_population(left) & ~(in_population(left) | in_population(right))
    expected = (*left._frame_inputs, *right._frame_inputs)
    assert collect_frame_key_inputs(condition) == expected
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    metric = -(amount.sum().where(condition) + amount.sum().where(in_population(left)))
    assert collect_frame_key_inputs(metric, left, condition) == expected
    # Sequence literals are also visited without resolving their contents.
    sequence = Deferred(Sequence((Just(left), condition._recipe)))
    assert collect_frame_key_inputs(sequence) == expected
    consumed = consume_metric(amount.sum().where(condition))
    assert isinstance(consumed.node, ContributionFilter)
    assert consumed.node.predicate == consume_predicate(condition).node
    tokens = canonical_recipe(metric._recipe)
    assert tokens


def test_private_population_capture_fields_preserve_deferred_fingerprint() -> None:
    population = mv.coordinates(by=(), keys=((),))
    predicate = mv.in_population(population)
    expression = Deferred(Sequence((Just(population), predicate._recipe)))
    assert isinstance(predicate, RowValue)
    assert recipe_fingerprint(resolver_of(expression)) == recipe_fingerprint(
        resolver_of(Deferred(Sequence((Just(population), mv.in_population(population)._recipe))))
    )


@pytest.mark.parametrize("role", ("calendar", "paired"))
def test_factory_rejects_unsupported_pending_roles(session: Session, role: str) -> None:
    region = catalog_value(session.catalog, "sales.facts.region", kind="dimension")
    pending = frame_sources.observe(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        {"amount": catalog_value(session.catalog, "sales.facts.amount", kind="measure").sum()},
        by=(region,),
    )
    assert isinstance(pending._schema, PendingDataSchema)
    # A metadata-only input isolates role admission from all execution behavior.
    key = DataColumn("key.0", "calendar", ScalarDataType("int64"), False)
    left = CalendarDataAxis(
        "calendar.left",
        (key,),
        "UTC",
        name="calendar",
        frozen=FrozenCalendarData(BuiltinCalendarData("day", 1), "left.directory"),
    )
    axis = (
        left
        if role == "calendar"
        else PairedDataAxis(
            "paired",
            (key,),
            left,
            replace(
                left,
                token="calendar.right",
                frozen=FrozenCalendarData(BuiltinCalendarData("day", 1), "right.directory"),
            ),
            "paired.mapping",
            name="paired",
        )
    )
    frame = AnalysisFrame._bind(
        pending._local, replace(pending._schema, axes=(axis,)), pending._backing, pending._input_id
    )


def test_current_catalog_rebinds_versions_and_validates_field_owner(session: Session) -> None:
    frame = _fields(session)
    region = catalog_value(session.catalog, "sales.facts.region", kind="dimension")
    facts = consume_field(region)
    old = semantic_input(replace(facts, reference=ExactRef(facts.reference.ref, "old-version")))
    mapped = population_from(
        session._runtime.local, session.catalog._state, frame, on=((frame.axes[0], old),)
    )
    assert isinstance(mapped._definition.axes[0], AxisBinding)
    assert mapped._definition.axes[0].field == facts.reference
    assert facts.field_read is not None
    bad = semantic_input(
        replace(
            facts,
            field_read=replace(
                facts.field_read,
                entity=_Resolver(session.catalog._state).entity_ref("sales.customers"),
            ),
        )
    )
    missing = replace(
        _Resolver(session.catalog._state).entity("sales.facts"),
        _exact_ref=ExactRef(ref.entity("sales.absent"), "v"),
    )
    identity = _identities(session)


def test_explicit_field_path_is_retained_and_rebound(session: Session) -> None:
    frame = _fields(session)
    relationship = catalog_get(session.catalog, "relationship:sales.customer", kind="relationship")
    segment = catalog_value(session.catalog, "sales.customers.segment", kind="dimension").via(
        (relationship,)
    )
    population = population_from(
        session._runtime.local, session.catalog._state, frame, on=((frame.axes[0], segment),)
    )
    assert isinstance(population._definition.axes[0], AxisBinding)
    assert population._definition.axes[0].path == (relationship._parameter.ref,)
    assert population._definition.axes[0].entity == relationship._parameter.to_entity


def test_prepared_keys_validate_deferred_time_role_without_source_reads(session: Session) -> None:
    frame = _fields(session)
    assert isinstance(frame._schema, DataSchema)
    target = AxisBinding(
        _Resolver(session.catalog._state).entity_ref("sales.facts"),
        ExactRef(ref.time_dimension("sales.facts.day"), "current"),
        (),
        "field",
    )
    definition = FrameKeys(frame._input_id, (FrameKeyAxis("a", ("first",), target),))


def _stored(session: Session, schema: DataSchema, *, digest: str = "same") -> AnalysisFrame:
    receipt = LocalReceipt("never-read", (FileEntry("part.parquet", 1),), 2, 1)
    return AnalysisFrame._from_stored(
        session._runtime.local,
        ArtifactData(schema, ParquetHandle(receipt, digest)),
        artifact_id="committed-selection",
        metadata=describe(replace(_metadata("stored-result"), lineage=empty_lineage(schema))),
    )


def test_independent_stored_handles_share_exact_descriptor_without_reading(
    session: Session,
) -> None:
    memory = _fields(session)
    assert isinstance(memory._schema, DataSchema)
    left, right = _stored(session, memory._schema), _stored(session, memory._schema)
    assert left is not right and left._input_id == right._input_id
    assert merge_frame_key_inputs(
        (FrameKeyInput(left._input_id, left),), (FrameKeyInput(right._input_id, right),)
    ) == (FrameKeyInput(left._input_id, left),)
    region = catalog_value(session.catalog, "sales.facts.region", kind="dimension")
    accepted = population_from(
        session._runtime.local, session.catalog._state, left, on=((right.axes[0], region),)
    )
    assert accepted._frame_inputs[0].frame is left
    conflicting = _stored(session, memory._schema, digest="contradictory")


def test_cross_session_inputs_reject_and_selected_frame_stays_alive(session: Session) -> None:
    other = mv.session.get_or_create("other-frame-owner", timezone="UTC")
    foreign = _fields(other)
    local_population, other_population = _bridge(session, _fields(session)), _bridge(other, foreign)

    frame = _fields(session)
    reference = weakref.ref(frame)
    population = _bridge(session, frame)
    del frame
    gc.collect()
    assert reference() is population._frame_inputs[0].frame
    pending = frame_sources.observe(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        {"amount": catalog_value(session.catalog, "sales.facts.amount", kind="measure").sum()},
    )
    assert isinstance(pending._backing, ExpressionFrameBacking)
    binding = pending._backing.root
    assert isinstance(binding, SourceFrameBinding)
    retained = replace(
        binding, frame_key_inputs=collect_frame_key_inputs(in_population(population))
    )
    del population
    gc.collect()
    assert reference() is retained.frame_key_inputs[0].frame
    live = reference()
    assert live is not None
    assert retained.frame_key_inputs[0].input_id == live._input_id


def test_mixed_native_predicate_and_declared_metric_retain_all_frame_inputs(
    session: Session,
) -> None:
    left, right = _bridge(session, _fields(session)), _bridge(session, _fields(session))
    region = catalog_value(session.catalog, "sales.facts.region", kind="dimension")
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    condition = ((region == "east") | in_population(left)) & ~(in_population(right) | False)
    expected = (*left._frame_inputs, *right._frame_inputs)
    facts = consume_predicate(condition)
    assert isinstance(facts.node, PredicateExpression)
    assert isinstance(facts.node.inputs[0], RowExpression)
    assert facts.roots == (consume_field(region).root,)
    assert collect_frame_key_inputs(condition) == expected
    metric = amount.sum().where(condition)
    declared = Deferred(
        DeclarationResolver(metric._recipe, DeclarationRecord("metric", "selected"))
    )
    assert collect_frame_key_inputs(declared) == expected
    graph = frame_sources.observe(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        {"amount": declared},
    )
    assert isinstance(graph._backing, ExpressionFrameBacking)
    assert isinstance(graph._backing.root, SourceFrameBinding)
    assert graph._backing.root.frame_key_inputs == expected


@pytest.mark.parametrize("operation", ["add", "compare", "cast", "ifelse", "sum"])
def test_membership_methods_reject_at_shared_intake(session: Session, operation: str) -> None:
    from marivo.semantic.errors import SemanticExpressionError

    member = in_population(_bridge(session, _fields(session)))
    from marivo.semantic._expression_values import _ExpressionCarrier

    operations: dict[str, Callable[[], _ExpressionCarrier]] = {
        "add": lambda: member + 1,  # type: ignore[dict-item,operator,return-value]  # Invalid boolean arithmetic.
        "compare": lambda: member == True,  # noqa: E712 - intentionally invalid native syntax
        "cast": lambda: member.cast("boolean"),  # type: ignore[misc]  # Invalid boolean cast.
        "ifelse": lambda: member.ifelse(True, False),
        "sum": lambda: member.sum(),  # type: ignore[misc]  # Invalid boolean aggregation.
    }
    with pytest.raises(SemanticExpressionError) as error:
        invalid = operations[operation]()
        consume_predicate(invalid)
    assert error.value.expected
    assert error.value.repair


def test_predicate_consumer_limits_and_hidden_field_roots(session: Session) -> None:

    population = _bridge(session, _fields(session))
    member = in_population(population)
    region = catalog_value(session.catalog, "sales.facts.region", kind="dimension")
    segment = catalog_value(session.catalog, "sales.customers.segment", kind="dimension")
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    for condition in ((region == "east") | member, member | (region == "east")):
        assert consume_predicate(condition).roots == (consume_field(region).root,)
