"""Explicit Frame key definitions retain live inputs outside semantic capture."""

import gc
import json
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
    canonical_recipe,
    recipe_fingerprint,
    resolver_of,
    semantic_input,
)
from marivo._definition.expressions import ContributionFilter
from marivo._definition.graph import (
    _encode_fields,
    canonical_bytes,
    computation_fingerprint,
    direct_refs,
    walk,
)
from marivo._definition.relations import FrameKeyAxis, FrameKeys, PopulationProgram
from marivo._definition.types import AxisBinding, ExactRef
from marivo.analysis.compiler.populations import frame_key_columns
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
from marivo.analysis.core.frame_audit import EvaluationRecord, FrameAudit, FrameResultMetadata
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    ExpressionFrameBacking,
    SourceFrameBinding,
)
from marivo.analysis.core.frame_key_inputs import (
    FrameKeyInput,
    collect_frame_key_inputs,
    merge_frame_key_inputs,
)
from marivo.analysis.core.frame_source_facts import source_scope
from marivo.analysis.core.frame_structure import PendingDataSchema
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.dsl import frame_sources
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.frame_population import population_from
from marivo.analysis.dsl.population import Population, in_population, product
from marivo.analysis.materialization.contracts import FileEntry, LocalReceipt
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.refs import ref
from marivo.semantic._deferred_semantics import consume_field, consume_metric
from marivo.semantic.reader import SemanticProject


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
                f"md.duckdb(name='warehouse', path={str(tmp_path / 'unopened.duckdb')!r})\n"
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
    return mv.session.create("frame-population-construction", timezone="UTC")


def _metadata(result_id: str = "fixed-selection") -> FrameResultMetadata:
    return FrameResultMetadata(
        result_id,
        FrameAudit((), EvaluationRecord(datetime(2026, 10, 19, tzinfo=timezone.utc), (), ())),
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
        session.catalog.entities.get("sales.facts")
        if identity
        else session.catalog.fields.get("sales.facts.region")
    )
    return population_from(
        session._runtime.local, session.catalog._state, frame, on=((frame.axes[0], target),)
    )


def _ids(population: Population) -> tuple[str, ...]:
    return tuple(item.input_id for item in population._frame_inputs)


def test_factory_is_metadata_only_and_definition_is_inert(
    session: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    frame = _fields(session)
    region = session.catalog.fields.get("sales.facts.region")
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
    assert source_scope(population) == source_scope(Population(population._definition))
    assert "FrameKeys" in source_scope(population._definition)
    population.show()
    assert "FrameKeys" in capsys.readouterr().out


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
    region = session.catalog.fields.get("sales.facts.region")
    channel = session.catalog.fields.get("sales.facts.channel")
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
    customer = session.catalog.entities.get("sales.customers")
    targets = population_from(
        session._runtime.local,
        session.catalog._state,
        customer_frame,
        on=((customer_frame.axes[0], customer),),
    )
    relationship = session.catalog.relationships.get("sales.customer")
    assert _ids(selected.classify(by=(session.catalog.fields.get("sales.facts.region"),))) == _ids(
        selected
    )
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
    assert condition._frame_inputs == expected
    amount = session.catalog.fields.get("sales.facts.amount")
    metric = -(amount.sum().where(condition) + amount.sum().where(in_population(left)))
    assert collect_frame_key_inputs(metric, left, condition) == expected
    # Sequence literals are also visited without resolving their contents.
    sequence = Deferred(Sequence((Just(left), Just(condition))))
    assert collect_frame_key_inputs(sequence) == expected
    consumed = consume_metric(amount.sum().where(condition))
    assert isinstance(consumed.node, ContributionFilter)
    assert consumed.node.predicate == condition._condition
    tokens = canonical_recipe(resolver_of(metric))
    assert tokens and "FrameKeyInput" not in repr(tokens)
    assert source_scope(condition) == source_scope(type(condition)(condition._condition))


def test_private_population_capture_fields_preserve_deferred_fingerprint() -> None:
    population = mv.coordinates(by=(), keys=((),))
    predicate = mv.in_population(population)
    expression = Deferred(Sequence((Just(population), Just(predicate))))
    assert not hasattr(population, "definition")
    assert not hasattr(predicate, "condition")
    assert (
        recipe_fingerprint(resolver_of(expression))
        == "7568133cc15b29e7c5b9b0a7973f7bfef6de7cfdcd0a051103c2e567aab34db5"
    )


def test_factory_rejects_wrong_handles_roles_duplicates_and_identity_width(
    session: Session,
) -> None:
    frame, foreign = _fields(session), _fields(session)
    region = session.catalog.fields.get("sales.facts.region")
    entity = session.catalog.entities.get("sales.facts")
    local, state = session._runtime.local, session.catalog._state
    with pytest.raises(CoreRuleError, match="exact Frame input"):
        population_from(local, state, frame, on=((foreign.axes[0], region),))
    with pytest.raises(CoreRuleError, match="each selected Frame axis once"):
        population_from(local, state, frame, on=((frame.axes[0], region), (frame.axes[0], region)))
    with pytest.raises(CoreRuleError, match="each target coordinate once"):
        population_from(local, state, frame, on=((frame.axes[0], region), (frame.axes[1], region)))
    with pytest.raises(CoreRuleError, match="ordinary field axis"):
        population_from(local, state, frame, on=((frame.axes[0], entity),))
    identity = _identities(session)
    with pytest.raises(CoreRuleError, match="ordinary field axis"):
        population_from(local, state, identity, on=((identity.axes[0], region),))
    incomplete = _identities(session, composite=False)
    with pytest.raises(CoreRuleError, match="all components"):
        population_from(local, state, incomplete, on=((incomplete.axes[0], entity),))


@pytest.mark.parametrize("role", ("calendar", "paired"))
def test_factory_rejects_unsupported_pending_roles(session: Session, role: str) -> None:
    region = session.catalog.fields.get("sales.facts.region")
    pending = frame_sources.observe(
        session._runtime.local,
        session.catalog._state,
        session._runtime.connection_config,
        {"amount": session.catalog.fields.get("sales.facts.amount").sum()},
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
    with pytest.raises(CoreRuleError, match="ordinary field axis"):
        _bridge(session, frame)


def test_current_catalog_rebinds_versions_and_validates_field_owner(session: Session) -> None:
    frame = _fields(session)
    region = session.catalog.fields.get("sales.facts.region")
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
                facts.field_read, entity=session.catalog.entities.get("sales.customers").ref
            ),
        )
    )
    with pytest.raises(CoreRuleError, match="declared Entity"):
        population_from(
            session._runtime.local, session.catalog._state, frame, on=((frame.axes[0], bad),)
        )
    missing = replace(
        session.catalog.entities.get("sales.facts"), ref=ExactRef(ref.entity("sales.absent"), "v")
    )
    identity = _identities(session)
    with pytest.raises(CoreRuleError, match="declared in the receiving Catalog"):
        population_from(
            session._runtime.local,
            session.catalog._state,
            identity,
            on=((identity.axes[0], missing),),
        )


def test_explicit_field_path_is_retained_and_rebound(session: Session) -> None:
    frame = _fields(session)
    relationship = session.catalog.relationships.get("sales.customer")
    segment = session.catalog.fields.get("sales.customers.segment").via((relationship,))
    population = population_from(
        session._runtime.local, session.catalog._state, frame, on=((frame.axes[0], segment),)
    )
    assert isinstance(population._definition.axes[0], AxisBinding)
    assert population._definition.axes[0].path == (relationship.ref,)
    assert population._definition.axes[0].entity == relationship.to_entity


def test_known_axis_unit_must_match_the_current_target_contract(session: Session) -> None:
    frame = _memory(
        session,
        (
            DataAxis(
                "amount",
                (DataColumn("amount", "amount", ScalarDataType("int64"), False, "EUR"),),
                name="amount",
            ),
        ),
        ((1,),),
    )
    with pytest.raises(CoreRuleError, match="compatible declared unit"):
        population_from(
            session._runtime.local,
            session.catalog._state,
            frame,
            on=((frame.axes[0], session.catalog.fields.get("sales.facts.amount")),),
        )


def test_prepared_keys_validate_deferred_time_role_without_source_reads(session: Session) -> None:
    frame = _fields(session)
    assert isinstance(frame._schema, DataSchema)
    target = AxisBinding(
        session.catalog.entities.get("sales.facts").ref,
        ExactRef(ref.time_dimension("sales.facts.day"), "current"),
        (),
        "field",
    )
    definition = FrameKeys(frame._input_id, (FrameKeyAxis("a", ("first",), target),))
    with pytest.raises(CoreRuleError, match="date or timestamp"):
        frame_key_columns(definition, frame._schema)


def _stored(session: Session, schema: DataSchema, *, digest: str = "same") -> AnalysisFrame:
    receipt = LocalReceipt("never-read", (FileEntry("part.parquet", 1),), 2, 1)
    return AnalysisFrame._from_stored(
        session._runtime.local,
        ArtifactData(schema, ParquetHandle(receipt, digest)),
        artifact_id="committed-selection",
        metadata=_metadata("stored-result"),
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
    region = session.catalog.fields.get("sales.facts.region")
    accepted = population_from(
        session._runtime.local, session.catalog._state, left, on=((right.axes[0], region),)
    )
    assert accepted._frame_inputs[0].frame is left
    conflicting = _stored(session, memory._schema, digest="contradictory")
    with pytest.raises(CoreRuleError, match="compatible exact descriptor"):
        merge_frame_key_inputs(
            (FrameKeyInput(left._input_id, left),),
            (FrameKeyInput(conflicting._input_id, conflicting),),
        )
    with pytest.raises(CoreRuleError, match="compatible exact descriptor"):
        population_from(
            session._runtime.local,
            session.catalog._state,
            left,
            on=((conflicting.axes[0], region),),
        )


def test_cross_session_inputs_reject_and_selected_frame_stays_alive(session: Session) -> None:
    other = mv.session.create("other-frame-owner", timezone="UTC")
    foreign = _fields(other)
    with pytest.raises(CoreRuleError, match="receiving Store and Session"):
        population_from(session._runtime.local, session.catalog._state, foreign, on=())
    local_population, other_population = _bridge(session, _fields(session)), _bridge(other, foreign)
    with pytest.raises(CoreRuleError, match="one Store and Session"):
        in_population(local_population) | in_population(other_population)

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
        {"amount": session.catalog.fields.get("sales.facts.amount").sum()},
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
    assert json.loads(source_scope(retained.frame_key_inputs[0].input_id))[1] == live._input_id
