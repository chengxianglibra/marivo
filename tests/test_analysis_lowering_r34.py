"""Real R1 reads and independent fixed-row oracles for private R3.4 consumers."""

from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

import ibis
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis.core.graph as graph
import marivo.semantic as ms
from marivo.analysis.compiler.graph_lowering import (
    CellColumns,
    CoordinateColumn,
    IntegrityCheck,
    LoweredLocal,
    LoweredRelation,
    RelationLayout,
    SemanticCheck,
    SourceBinding,
    lower,
)
from marivo.analysis.compiler.graph_plan import RouteChoice, plan
from marivo.analysis.core.graph import (
    Edge,
    FixedLeaf,
    SourceDefinition,
    SourceLeaf,
    method_node,
    topology,
)
from marivo.analysis.core.local_laws import (
    FixedMapping,
    FixedStates,
    SumState,
    compose_mapping,
    fuse_selection,
    layered_state,
    restrict_state,
)
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    CoreRuleError,
    CoveragePart,
    Defined,
    DomainSignature,
    Null,
    ObservedQuantity,
    OriginalStatePart,
    Signature,
    Undefined,
    Unknown,
)
from marivo.analysis.core.predicates import ValuePredicate
from marivo.analysis.core.rules import (
    AssociationScore,
    BindProject,
    MapCorrespond,
    PartsTransport,
    RowState,
)
from marivo.analysis.methods.errors import MethodRegistrationError
from marivo.analysis.methods.local import count
from marivo.analysis.methods.physical import (
    DecimalType,
    FixedShape,
    NoTime,
    ScalarType,
    SourceShape,
    TimeShape,
)
from marivo.analysis.methods.registry import REGISTRY, MethodRegistry
from marivo.analysis.refs import ArtifactRef
from marivo.datasource.adapters import SourceSession, provider_for
from marivo.datasource.ir import (
    AiContextIR,
    DatasourceIR,
    DatasourceSourceLocation,
    ParquetSourceIR,
    TableSourceIR,
)
from marivo.refs import RefPayloadV1
from marivo.semantic.ir import TargetDimensionContract


def _leaf(form="table", *, quantity=True):
    binding = Binding("r34", "sales", "customers", "september")
    entity = ms.ref.entity("sales.customer")
    keys = (Coordinate(entity, "tenant", "identity"), Coordinate(entity, "id", "identity"))
    domain = DomainSignature(binding, "entity", keys, keys, "customers")
    observed = ObservedQuantity(
        "revenue",
        ms.ref.metric("sales.revenue"),
        "metric-v1",
        "CNY",
        "september",
        "orders",
        "strict",
        "sum@v1",
    )
    return SourceLeaf(
        SourceDefinition(
            observed.metric_ref if quantity else entity,
            "metric-v1" if quantity else "entity-v1",
            ms.ref.datasource("db"),
            SourceShape("duckdb", form, "native" if form == "table" else "parquet", NoTime()),
        ),
        Signature(domain, observed if quantity else None),
        ScalarType("int64"),
    )


def _rows():
    return pa.table(
        {
            "tenant": pa.array([1, 1, 2, 2, 3], type=pa.int64()),
            "id": pa.array([9007199254740993, 2, 1, 2, 1], type=pa.int64()),
            "amount": pa.array([5, None, None, None, 9], type=pa.int64()),
            "tag": ["defined", "null", "undefined", "unknown", "defined"],
            "reason": pa.array(
                [None, "source_null", "zero_denominator", "unavailable", None], type=pa.string()
            ),
        }
    )


@pytest.fixture(params=["table", "parquet"])
def source_case(request, tmp_path: Path):
    backend = ibis.duckdb.connect()
    data = _rows()
    backend.create_table("facts", data)
    path = tmp_path / "facts.parquet"
    pq.write_table(data, path)
    datasource = DatasourceIR(
        "db", "db", "duckdb", {}, {}, AiContextIR(), "db", DatasourceSourceLocation("source.py", 1)
    )
    with SourceSession(provider_for("duckdb"), datasource, backend) as session:
        yield session, request.param, path


def _bind(case, leaf):
    session, form, path = case
    physical = TableSourceIR("facts") if form == "table" else ParquetSourceIR(str(path))
    bound = session.bind(physical, source_identity=leaf.identity)
    layout = RelationLayout(
        tuple(CoordinateColumn(k, k.field) for k in leaf.signature.domain.instance_key),
        CellColumns("amount", "tag", "reason") if leaf.signature.quantity else None,
    )
    return SourceBinding(leaf, bound, layout)


def _plan(root, route="ibis"):
    return plan(
        root,
        routes=tuple(
            RouteChoice(n.identity, route)
            for n in topology(root)
            if not isinstance(n, (SourceLeaf, FixedLeaf))
        ),
    )


def _read(session, lowered, bindings, expression):
    qualified = tuple(
        session.qualify(b.source, lowered.source_requirement)
        for b in lowered.sources_for(expression)
    )
    handle = session.compile(
        qualified, expression, purpose="r34.test", expected_schema=expression.schema().to_pyarrow()
    )
    batches = session.batches(handle, chunk_size=2)
    try:
        result = pa.Table.from_batches(list(batches), schema=handle.schema).to_pylist()
    finally:
        batches.close()
    assert session.submissions[-1].sql == handle.sql
    return result


def _primary(lowered):
    return next(
        s
        for s in lowered.stages
        if isinstance(s, LoweredRelation) and s.output == lowered.primary_output
    )


def _selection(source, *, threshold=4, unknown="drop"):
    return method_node(
        (Edge("quantity" if source.signature.quantity else "subject", source),),
        PartsTransport(
            "where",
            source.signature.domain,
            (),
            True,
            (ValuePredicate(source.signature.domain.binding, "gt", threshold, unknown),),
        ),
        value_type=source.value_type,
    )


def _count(source, method="count"):
    target = DomainSignature(source.signature.domain.binding, "singleton", (), (), "all")
    return method_node(
        (Edge("quantity", source),),
        RowState(
            method,
            target,
            method,
            "count_all" if method == "count" else "defined_only",
            numeric_check_id="source.cell_policy@v1" if method == "count_defined" else None,
        ),
        value_type=ScalarType("int64"),
    )


def _spearman(left, right):
    target = DomainSignature(left.signature.domain.binding, "singleton", (), (), "all")
    return method_node(
        (Edge("quantity", left), Edge("quantity", right)),
        AssociationScore(
            target,
            "rank-association",
            "source.exact_pairing@v1",
            "source.finite_numeric@v1",
        ),
        value_type=ScalarType("float64"),
    )


@pytest.mark.parametrize("route", ["ibis", "ibis_python"])
@pytest.mark.parametrize(
    "values_a,values_b,status,expected,complete",
    [
        ([1, 2, 3, 4], [4, 3, 2, 1], "valid", -1.0, 4),
        ([1, 2, 2, 4], [4, 2, 3, 1], "valid", -0.9486832980505138, 4),
        ([1, 1, 1, 1], [4, 3, 2, 1], "constant_a", None, 4),
        ([1, 2, 3, 4], [4, 3, None, 1], "valid", -1.0, 3),
        ([1, 2, 3, 4], [4, 3, None, 1], "invalid", None, 0),
        ([], [], "insufficient_pairs", None, 0),
    ],
)
def test_r43_source_spearman_exact_pair_state(
    source_case, route, values_a, values_b, status, expected, complete
):
    session, form, path = source_case
    rows = pa.table(
        {
            "tenant": pa.array([1, 1, 2, 2][: len(values_a)], type=pa.int64()),
            "id": pa.array([9007199254740993, 2, 1, 2][: len(values_a)], type=pa.int64()),
            "amount": pa.array(values_a, type=pa.int64()),
            "other": pa.array(values_b, type=pa.int64()),
            "tag": pa.array(["defined"] * len(values_a), type=pa.string()),
            "reason": pa.array([None] * len(values_a), type=pa.string()),
            "tag_b": pa.array(
                [
                    ("unknown" if status == "invalid" else "null") if item is None else "defined"
                    for item in values_b
                ],
                type=pa.string(),
            ),
            "reason_b": pa.array(
                [
                    ("unavailable" if status == "invalid" else "source_null")
                    if item is None
                    else None
                    for item in values_b
                ],
                type=pa.string(),
            ),
        }
    )
    session._backend.create_table("facts", rows, overwrite=True)
    pq.write_table(rows, path)
    left = _leaf(form)
    observed = left.signature.quantity
    assert isinstance(observed, ObservedQuantity)
    right_observed = replace(
        observed,
        definition_id="other",
        metric_ref=ms.ref.metric("sales.other"),
    )
    right = SourceLeaf(
        SourceDefinition(
            right_observed.metric_ref,
            "metric-v1",
            ms.ref.datasource("db"),
            left.definition.shape,
        ),
        replace(left.signature, quantity=right_observed),
        ScalarType("int64"),
    )
    first = _bind(source_case, left)
    second = replace(
        _bind(source_case, right),
        layout=replace(
            _bind(source_case, right).layout, cell=CellColumns("other", "tag_b", "reason_b")
        ),
    )
    root = _spearman(left, right)
    from marivo.analysis.materialization.graph_execution import prepare_graph
    from marivo.analysis.materialization.graph_source_execution import execute_source_graph

    prepared = prepare_graph(
        root,
        session_ref=left.signature.domain.binding.session_id,
        routes=(RouteChoice(root.identity, route),),
    )
    lowered = lower(prepared.admitted, bindings=(first, second))
    if status == "invalid":
        from marivo.analysis.materialization.errors import MaterializationError

        with pytest.raises(MaterializationError, match="violating rows"):
            execute_source_graph(prepared, lowered, session)
        assert len(
            [item for item in session.submissions if item.purpose == "analysis.graph.stage"]
        ) == (0 if route == "ibis" else 2)
        assert not session._staged_relations
        return
    result = execute_source_graph(prepared, lowered, session)
    if expected is None:
        assert result.primary.column("value").to_pylist() == [None]
    else:
        assert result.primary.column("value").to_pylist() == pytest.approx([expected])
    assert result.primary.column("status").to_pylist() == [status]
    assert result.parts[0].role == "pair_counts"
    assert result.parts[0].table.column("pair_counts__complete_pair_count").to_pylist() == [
        complete
    ]


@pytest.mark.parametrize("method,expected", [("count", 5), ("count_defined", 2)])
def test_registered_source_count_preserves_cells_and_component_state(source_case, method, expected):
    leaf = _leaf(source_case[1])
    binding = _bind(source_case, leaf)
    graph = _plan(_count(leaf, method))
    lowered = lower(graph, bindings=(binding,))
    assert source_case[0].submissions == []
    for check in lowered.checks:
        assert _read(source_case[0], lowered, (binding,), check.violations) == []
    result = _read(source_case[0], lowered, (binding,), _primary(lowered).expression)
    assert result == [
        {
            "value": expected,
            "cell_tag": "defined",
            "cell_reason": None,
            f"row_state__{method}": expected,
        }
    ]
    assert graph.checks == lowered.admitted.checks
    assert all(not isinstance(s, LoweredLocal) for s in lowered.stages)


def test_selection_and_l1_use_independent_original_rows(source_case):
    leaf = _leaf(source_case[1])
    binding = _bind(source_case, leaf)
    defined = method_node(
        (Edge("quantity", leaf),),
        PartsTransport(
            "where",
            leaf.signature.domain,
            (),
            True,
            (ValuePredicate(leaf.signature.domain.binding, "is_defined", 0),),
        ),
        value_type=leaf.value_type,
    )
    inner = _selection(defined, threshold=4)
    outer = _selection(inner, threshold=6)
    fused = fuse_selection(outer)
    assert fused.identity != outer.identity
    assert fused.signature == outer.signature
    for root in (outer, fused):
        lowered = lower(_plan(root), bindings=(binding,))
        assert _read(source_case[0], lowered, (binding,), _primary(lowered).expression) == [
            {"key_0": 3, "key_1": 1, "value": 9, "cell_tag": "defined", "cell_reason": None}
        ]
        for check in lowered.checks:
            assert _read(source_case[0], lowered, (binding,), check.violations) == []


def test_unknown_policy_refuses_unsafe_fusion_and_emits_bound_failure(source_case):
    leaf = _leaf(source_case[1])
    binding = _bind(source_case, leaf)
    with pytest.raises(CoreRuleError, match="unknown policy"):
        fuse_selection(_selection(_selection(leaf), unknown="reject"))
    root = _selection(leaf, unknown="reject")
    lowered = lower(_plan(root), bindings=(binding,))
    failures = [
        c for c in lowered.checks if isinstance(c, IntegrityCheck) and "predicate" in c.expected
    ]
    assert len(failures) == 1
    assert len(_read(source_case[0], lowered, (binding,), failures[0].violations)) == 3


def test_direct_field_binding_filters_actual_subjects(source_case):
    leaf = _leaf(source_case[1], quantity=False)
    binding = _bind(source_case, leaf)
    field_ref = ms.ref.dimension("sales.customer.amount")
    field = TargetDimensionContract(
        RefPayloadV1.from_ref(field_ref),
        RefPayloadV1.from_ref(leaf.definition.ref),
        "amount",
        "int64",
        True,
        False,
        None,
        False,
        None,
    )
    root = method_node(
        (Edge("subject", leaf),),
        BindProject(field_ref, leaf.definition.ref, field, None, (), ()),
        sources=(leaf,),
        value_type=ScalarType("int64"),
    )
    defined = method_node(
        (Edge("quantity" if root.signature.quantity else "subject", root),),
        PartsTransport(
            "where",
            root.signature.domain,
            (),
            True,
            (ValuePredicate(root.signature.domain.binding, "is_defined", 0),),
        ),
        value_type=root.value_type,
    )
    selected = _selection(defined, threshold=6)
    lowered = lower(_plan(selected), bindings=(binding,))
    for check in lowered.checks:
        assert _read(source_case[0], lowered, (binding,), check.violations) == []
    assert _read(source_case[0], lowered, (binding,), _primary(lowered).expression) == [
        {"key_0": 3, "key_1": 1, "value": 9, "cell_tag": "defined", "cell_reason": None}
    ]


@pytest.mark.parametrize("mode", ["exact_keys", "one_to_one", "union_keys"])
def test_correspondence_uses_full_tuples_not_cartesian_axes(source_case, mode):
    left, right = _leaf(source_case[1], quantity=False), _leaf(source_case[1], quantity=False)
    bindings = (_bind(source_case, left), _bind(source_case, right))
    output = replace(left.signature.domain, definition_id="paired")
    root = method_node(
        (Edge("subject", left), Edge("subject", right)),
        MapCorrespond(mode, output, "source.exact_pairing@v1"),
        value_type=left.value_type,
    )
    lowered = lower(_plan(root), bindings=bindings)
    # Ordered fact inputs that share the same symbolic domain are ambiguous without identities.
    assert len(_read(source_case[0], lowered, bindings, _primary(lowered).expression)) == 5
    for check in lowered.checks:
        assert _read(source_case[0], lowered, bindings, check.violations) == []


def test_layout_identity_type_and_plan_tampering_refuse_before_submission(source_case):
    leaf = _leaf(source_case[1])
    binding = _bind(source_case, leaf)
    graph = _plan(_count(leaf))
    for candidate in (
        (),
        (binding, binding),
        (replace(binding, layout=replace(binding.layout, keys=binding.layout.keys[:1])),),
    ):
        with pytest.raises(CoreRuleError):
            lower(graph, bindings=candidate)
    with pytest.raises(CoreRuleError, match="unchanged admitted plan"):
        lower(replace(graph, checks=(), primary_output="forged"), bindings=(binding,))
    with pytest.raises(CoreRuleError, match="exact leaf identity"):
        lower(
            graph,
            bindings=(
                replace(
                    binding,
                    source=replace(
                        binding.source, facts=replace(binding.source.facts, source_identity="wrong")
                    ),
                ),
            ),
        )
    assert source_case[0].submissions == []


def test_unqualified_parameters_and_shapes_fail_at_plan():
    leaf = _leaf()
    with pytest.raises(MethodRegistrationError, match="explicit predicates"):
        _plan(
            method_node(
                (Edge("quantity", leaf),),
                PartsTransport("where", leaf.signature.domain, (), True),
                value_type=leaf.value_type,
            )
        )
    wrong = replace(
        leaf,
        definition=replace(
            leaf.definition, shape=SourceShape("sqlite", "table", "native", NoTime())
        ),
    )
    with pytest.raises(MethodRegistrationError, match="qualified exact key"):
        _plan(_count(wrong))
    with pytest.raises(CoreRuleError, match="exact input scope"):
        method_node(
            (Edge("quantity", leaf),),
            PartsTransport(
                "where",
                leaf.signature.domain,
                (),
                True,
                (
                    ValuePredicate(
                        replace(leaf.signature.domain.binding, scope_id="other"), "gt", 1
                    ),
                ),
            ),
            value_type=leaf.value_type,
        )


@pytest.mark.parametrize("method, expected", [("count", 4), ("count_defined", 1)])
def test_fixed_count_handoff_and_registered_local_consumer(method, expected):
    from marivo.analysis.methods.local import count_defined

    reducer = count if method == "count" else count_defined
    source = _leaf()
    fixed = FixedLeaf(
        ArtifactRef("old/revenue"),
        source.fingerprint,
        source.signature,
        source.value_type,
        FixedShape(NoTime()),
    )
    lowered = lower(_plan(_count(fixed, method), "artifact_python"), bindings=())
    stage = next(s for s in lowered.stages if isinstance(s, LoweredLocal))
    assert (
        reducer(
            stage.stage, (Defined(1), Null("empty"), Undefined("zero"), Unknown("missing"))
        ).count
        == expected
    )
    assert reducer(stage.stage, ()).cell == Defined(0)
    if method == "count":
        with pytest.raises(CoreRuleError, match="100000"):
            reducer(stage.stage, (Defined(1),) * 100001)


def _states():
    source = _leaf().signature
    binding, quantity = source.domain.binding, source.quantity
    state = OriginalStatePart(
        binding,
        quantity.definition_id,
        "sum@v1",
        quantity.contribution_id,
        ("sum", "non_null_count"),
        "v1",
    )
    coverage = CoveragePart(binding, quantity.definition_id, binding.scope_id, "v1")
    signature = replace(source, parts=(state, coverage))
    return FixedStates(
        signature,
        (
            ((1, 1), SumState(5, 1, ("a",))),
            ((1, 2), SumState(7, 1, ("b",))),
            ((2, 1), SumState(0, 0, ("c",))),
        ),
    )


def test_l7_composition_preserves_roles_scope_and_noninjectivity():
    binding = _leaf().signature.domain.binding
    first = FixedMapping(
        binding, "anchors", "subjects", ("participant",), (((1,), (10,)), ((2,), (10,)))
    )
    second = FixedMapping(binding, "subjects", "groups", ("region",), (((10,), (100,)),))
    result = compose_mapping(first, second)
    assert result.rows == (((1,), (100,)), ((2,), (100,)))
    assert result.roles == ("participant", "region") and not result.injective
    with pytest.raises(CoreRuleError, match="same fixed input"):
        compose_mapping(first, replace(second, binding=replace(binding, scope_id="other")))
    with pytest.raises(CoreRuleError, match="total mapping"):
        compose_mapping(first, replace(second, rows=()))


def test_l8_l9_keep_complete_original_components_and_empty_targets():
    states = _states()
    binding = states.signature.domain.binding
    first = FixedMapping(
        binding,
        "customers",
        "regions",
        ("region",),
        (((1, 1), (1,)), ((1, 2), (1,)), ((2, 1), (2,))),
    )
    second = FixedMapping(
        binding, "regions", "all", ("all",), (((1,), (0,)), ((2,), (0,)), ((3,), (0,)))
    )
    layered = layered_state(states, first, second, ((1,), (2,), (3,)), ((0,), (9,)))
    expected = (((0,), SumState(12, 2, ("a", "b", "c"))), ((9,), SumState(0, 0, ())))
    assert layered.left == layered.right == expected
    assert layered.comparison == "state" and layered.input_signature == states.signature
    restricted = restrict_state(states, first, ((1,), (2,), (3,)), ((3,), (1,)))
    assert (
        restricted.left
        == restricted.right
        == (((3,), SumState(0, 0, ())), ((1,), SumState(12, 2, ("a", "b"))))
    )
    assert restrict_state(states, first, ((1,), (2,), (3,)), ()).left == ()
    with pytest.raises(CoreRuleError, match="fixed scope"):
        restrict_state(
            states,
            replace(first, binding=replace(binding, scope_id="changed")),
            ((1,), (2,)),
            ((1,),),
        )
    with pytest.raises(CoreRuleError, match="original_state"):
        replace(states, signature=replace(states.signature, parts=states.signature.parts[1:]))
    with pytest.raises(CoreRuleError, match="disjoint"):
        replace(states, rows=(((1, 1), SumState(5, 1, ("a",))), ((1, 2), SumState(5, 1, ("a",)))))


def test_lowering_never_opens_compiles_reads_or_allocates(source_case, monkeypatch):
    leaf = _leaf(source_case[1])
    binding = _bind(source_case, leaf)

    def forbidden(*args, **kwargs):
        raise AssertionError("pure lowering attempted execution")

    for name in ("bind", "qualify", "compile", "batches"):
        monkeypatch.setattr(SourceSession, name, forbidden)
    monkeypatch.setattr(ibis.duckdb, "connect", forbidden)
    graph = _plan(_count(_selection(leaf)))
    lowered = lower(graph, bindings=(binding,))
    assert lowered.admitted is graph
    assert len(lowered.stages) == 3


def test_duplicate_identity_and_malformed_cell_remain_visible(source_case):
    from marivo.analysis.core.model import Fact, Obligation

    leaf = _leaf(source_case[1])
    obligation = Obligation(
        Fact(
            "unique_key", leaf.signature.domain.binding, leaf.signature.domain.definition_id, "v1"
        ),
        "source.unique_key@v1",
        "consume",
    )
    leaf = replace(leaf, signature=replace(leaf.signature, obligations=(obligation,)))
    binding = _bind(source_case, leaf)
    source = binding.source
    table = source.relation
    duplicate = table.union(table, distinct=False)
    binding = replace(binding, source=replace(source, relation=duplicate))
    lowered = lower(_plan(_count(leaf)), bindings=(binding,))
    pending = [c for c in lowered.checks if isinstance(c, SemanticCheck)]
    assert pending and pending[0].requirement.obligation == obligation
    assert len(_read(source_case[0], lowered, (binding,), pending[0].violations)) == 5
    assert lowered.admitted.root.signature.obligations == (obligation,)
    malformed = table.mutate(tag=ibis.literal("null"))
    binding = replace(binding, source=replace(source, relation=malformed))
    lowered = lower(_plan(_count(leaf)), bindings=(binding,))
    check = next(
        c for c in lowered.checks if isinstance(c, IntegrityCheck) and "Cell encoding" in c.expected
    )
    assert len(_read(source_case[0], lowered, (binding,), check.violations)) == 2


def test_empty_source_count_keeps_singleton(source_case):
    leaf = _leaf(source_case[1])
    binding = _bind(source_case, leaf)
    empty = binding.source.relation.filter(ibis.literal(False))
    binding = replace(binding, source=replace(binding.source, relation=empty))
    lowered = lower(_plan(_count(leaf)), bindings=(binding,))
    assert _read(source_case[0], lowered, (binding,), _primary(lowered).expression) == [
        {"value": 0, "cell_tag": "defined", "cell_reason": None, "row_state__count": 0}
    ]


def test_exact_pairing_rejects_different_realizations_of_same_definition(source_case):
    left, right = _leaf(source_case[1], quantity=False), _leaf(source_case[1], quantity=False)
    a, b = _bind(source_case, left), _bind(source_case, right)
    b = replace(
        b,
        source=replace(b.source, relation=b.source.relation.filter(b.source.relation.tenant == 1)),
    )
    root = method_node(
        (Edge("subject", left), Edge("subject", right)),
        MapCorrespond(
            "exact_keys",
            replace(left.signature.domain, definition_id="paired"),
            "source.exact_pairing@v1",
        ),
        value_type=left.value_type,
    )
    lowered = lower(_plan(root), bindings=(a, b))
    check = next(c for c in lowered.checks if isinstance(c, SemanticCheck))
    assert len(_read(source_case[0], lowered, (a, b), check.violations)) == 3


def test_parts_transport_preserves_components_and_prunes_k(source_case):
    from marivo.analysis.compiler.graph_lowering import ComponentColumn, PartColumns
    from marivo.analysis.methods.registry import REGISTRY

    leaf = _leaf(source_case[1])
    original = _states().signature.parts
    leaf = replace(leaf, signature=replace(leaf.signature, parts=original))
    binding = _bind(source_case, leaf)
    raw = binding.source.relation
    raw = raw.mutate(
        state_sum=raw.amount.fill_null(0),
        support=raw.amount.notnull().cast("int64"),
        covered=ibis.literal(True),
    )
    physical = replace(
        binding.source,
        relation=raw,
        facts=replace(binding.source.facts, schema=raw.schema().to_pyarrow()),
    )
    part_columns = (
        PartColumns(
            original[0],
            (ComponentColumn("sum", "state_sum"), ComponentColumn("non_null_count", "support")),
        ),
        PartColumns(original[1], (ComponentColumn("complete", "covered"),)),
    )
    binding = replace(binding, source=physical, layout=replace(binding.layout, parts=part_columns))
    retained = method_node(
        (Edge("quantity", leaf),),
        PartsTransport("projection", leaf.signature.domain, ("coverage", "original_state"), True),
        value_type=leaf.value_type,
    )
    lowered = lower(_plan(retained), bindings=(binding,))
    rows = _read(source_case[0], lowered, (binding,), _primary(lowered).expression)
    assert sorted(
        (
            r["key_0"],
            r["key_1"],
            r["original_state__sum"],
            r["original_state__non_null_count"],
            r["coverage__complete"],
        )
        for r in rows
    ) == [
        (1, 2, 0, 0, True),
        (1, 9007199254740993, 5, 1, True),
        (2, 1, 0, 0, True),
        (2, 2, 0, 0, True),
        (3, 1, 9, 1, True),
    ]
    dropped = method_node(
        (Edge("quantity", retained),),
        PartsTransport("projection", leaf.signature.domain, (), True),
        value_type=leaf.value_type,
    )
    assert "state_rollup" in {c.method.name for c in REGISTRY.continuations(retained.signature)}
    assert "state_rollup" not in {c.method.name for c in REGISTRY.continuations(dropped.signature)}
    assert lower(_plan(dropped), bindings=(binding,)).admitted.root.signature.parts == ()
    with pytest.raises(CoreRuleError, match="complete ordered part"):
        lower(
            _plan(retained),
            bindings=(
                replace(
                    binding,
                    layout=replace(
                        binding.layout,
                        parts=(
                            replace(part_columns[0], columns=part_columns[0].columns[:1]),
                            part_columns[1],
                        ),
                    ),
                ),
            ),
        )


@pytest.mark.parametrize("injective", [False, True])
def test_subject_set_image_and_injective_failure(source_case, injective):
    from marivo.analysis.compiler.graph_lowering import ComponentColumn, PartColumns
    from marivo.analysis.core.model import SubjectPart

    leaf = _leaf(source_case[1], quantity=False)
    key = (leaf.signature.domain.instance_key[0],)
    part = SubjectPart(
        leaf.signature.domain.binding,
        key[0].entity_ref,
        leaf.signature.domain.instance_key,
        key,
        injective,
        True,
        "v1",
    )
    leaf = replace(leaf, signature=replace(leaf.signature, parts=(part,)))
    binding = _bind(source_case, leaf)
    raw = binding.source.relation.mutate(subject_tenant=binding.source.relation.tenant)
    binding = replace(
        binding,
        source=replace(
            binding.source,
            relation=raw,
            facts=replace(binding.source.facts, schema=raw.schema().to_pyarrow()),
        ),
        layout=replace(
            binding.layout,
            parts=(PartColumns(part, (ComponentColumn("key_0", "subject_tenant"),)),),
        ),
    )
    target = replace(
        leaf.signature.domain, instance_key=key, target_key=key, definition_id="subjects"
    )
    root = method_node(
        (Edge("subject", leaf),), MapCorrespond("subjects", target), value_type=leaf.value_type
    )
    lowered = lower(_plan(root), bindings=(binding,))
    if injective:
        check = next(
            c for c in lowered.checks if isinstance(c, IntegrityCheck) and "injective" in c.expected
        )
        assert len(_read(source_case[0], lowered, (binding,), check.violations)) == 2
    else:
        rows = _read(source_case[0], lowered, (binding,), _primary(lowered).expression)
        assert sorted(rows, key=lambda r: r["key_0"]) == [
            {"key_0": 1, "subject__key_0": 1},
            {"key_0": 2, "subject__key_0": 2},
            {"key_0": 3, "subject__key_0": 3},
        ]


def test_l8_does_not_accept_row_statistic_or_incomplete_mapping():
    from marivo.analysis.core.model import RowStatisticQuantity

    states = _states()
    with pytest.raises(CoreRuleError, match="original sum"):
        replace(
            states,
            signature=replace(
                states.signature,
                quantity=RowStatisticQuantity(
                    "revenue",
                    "row.mean@v1",
                    "revenue",
                    "customers",
                    "CNY",
                    "september",
                    "strict",
                    "equal_weight",
                ),
            ),
        )
    binding = states.signature.domain.binding
    first = FixedMapping(
        binding,
        "customers",
        "region",
        ("region",),
        (((1, 1), (1,)), ((1, 2), (1,)), ((2, 1), (2,))),
    )
    second = FixedMapping(binding, "region", "all", ("all",), (((1,), (0,)), ((2,), (0,))))
    with pytest.raises(CoreRuleError, match="total assignment"):
        layered_state(states, first, second, ((1,), (2,), (3,)), ((0,),))


def test_l1_refuses_unqualified_value_types():
    source = replace(_leaf(), value_type=ScalarType("float64"))
    with pytest.raises(CoreRuleError, match="int64 selection law"):
        fuse_selection(_selection(_selection(source)))


@pytest.mark.parametrize("mode", ["exact_keys", "one_to_one", "union_keys"])
def test_same_table_leaves_keep_expression_source_identity(source_case, mode):
    left = _leaf(source_case[1], quantity=False)
    right = _leaf(source_case[1], quantity=False)
    a, b = _bind(source_case, left), _bind(source_case, right)
    if source_case[1] == "table":
        assert a.source.relation.op() == b.source.relation.op()
    root = method_node(
        (Edge("subject", left), Edge("subject", right)),
        MapCorrespond(
            mode, replace(left.signature.domain, definition_id="paired"), "source.exact_pairing@v1"
        ),
        value_type=left.value_type,
    )
    lowered = lower(_plan(root), bindings=(a, b))
    expression = _primary(lowered).expression
    expected = (left.identity, right.identity) if mode == "union_keys" else (left.identity,)
    selected = lowered.sources_for(expression)
    assert tuple(binding.leaf.identity for binding in selected) == expected
    session = source_case[0]
    handle = session.compile(
        tuple(session.qualify(binding.source, lowered.source_requirement) for binding in selected),
        expression,
        purpose="r34.identity",
        expected_schema=expression.schema().to_pyarrow(),
    )
    assert handle.source_identity == "|".join(sorted(expected))
    for node, binding in ((left, a), (right, b)):
        relation = next(
            s for s in lowered.stages if isinstance(s, LoweredRelation) and s.node is node
        )
        assert lowered.sources_for(relation.expression) == (binding,)
        checks = [
            c
            for c in lowered.checks
            if isinstance(c, IntegrityCheck) and c.stage_output == relation.output
        ]
        assert checks
        for check in checks:
            assert lowered.sources_for(check.violations) == (binding,)
    if mode != "union_keys":
        checks = [
            c
            for c in lowered.checks
            if isinstance(c, SemanticCheck)
            or (isinstance(c, IntegrityCheck) and c.expected == "equal complete key sets")
        ]
        assert checks
        for check in checks:
            assert tuple(
                binding.leaf.identity for binding in lowered.sources_for(check.violations)
            ) == (left.identity, right.identity)


def test_source_identity_propagates_through_projection_and_rejects_untracked_expression(
    source_case,
):
    left = _leaf(source_case[1], quantity=False)
    right = _leaf(source_case[1], quantity=False)
    a, b = _bind(source_case, left), _bind(source_case, right)
    paired = method_node(
        (Edge("subject", left), Edge("subject", right)),
        MapCorrespond(
            "exact_keys",
            replace(left.signature.domain, definition_id="paired"),
            "source.exact_pairing@v1",
        ),
        value_type=left.value_type,
    )
    projected = method_node(
        (Edge("subject", paired),),
        PartsTransport("projection", paired.signature.domain, (), False),
        value_type=left.value_type,
    )
    lowered = lower(_plan(projected), bindings=(b, a))
    primary = _primary(lowered)
    assert primary.source_ids == (left.identity,)
    assert lowered.sources_for(primary.expression) == (a,)
    for check in lowered.checks:
        if isinstance(check, SemanticCheck):
            assert check.source_ids == (left.identity, right.identity)
        elif check.stage_output == primary.output:
            assert check.source_ids == (left.identity,)
    copied = primary.expression.op().to_expr()
    assert copied is not primary.expression and copied.equals(primary.expression)
    with pytest.raises(CoreRuleError, match="emitted expression"):
        lowered.sources_for(copied)
    with pytest.raises(CoreRuleError, match="emitted expression"):
        lowered.sources_for(primary.expression.limit(1))


def test_repeated_reference_deduplicates_only_the_same_leaf(source_case):
    source = _leaf(source_case[1], quantity=False)
    binding = _bind(source_case, source)
    root = method_node(
        (Edge("subject", source), Edge("subject", source)),
        MapCorrespond(
            "exact_keys",
            replace(source.signature.domain, definition_id="paired"),
            "source.exact_pairing@v1",
        ),
        value_type=source.value_type,
    )
    lowered = lower(_plan(root), bindings=(binding,))
    for check in lowered.checks:
        assert check.source_ids == (source.identity,)
        assert lowered.sources_for(check.violations) == (binding,)


@pytest.mark.parametrize("shared", [True, False])
def test_inherited_pairing_keeps_origin_groups_across_union(source_case, shared):
    leaves = tuple(_leaf(source_case[1], quantity=False) for _ in range(2 if shared else 4))
    bindings = tuple(_bind(source_case, leaf) for leaf in leaves)

    def pair(left, right):
        return method_node(
            (Edge("subject", left), Edge("subject", right)),
            MapCorrespond(
                "exact_keys",
                replace(left.signature.domain, definition_id="paired"),
                "source.exact_pairing@v1",
            ),
            value_type=left.value_type,
        )

    first = pair(*leaves[:2])
    second = first if shared else pair(*leaves[2:])
    root = method_node(
        (Edge("subject", first), Edge("subject", second)),
        MapCorrespond("union_keys", replace(leaves[0].signature.domain, definition_id="union")),
        value_type=leaves[0].value_type,
    )
    lowered = lower(_plan(root), bindings=bindings)
    inherited = [
        check
        for check in lowered.checks
        if isinstance(check, SemanticCheck) and check.requirement.node_id == root.identity
    ]
    assert [check.source_ids for check in inherited] == [
        tuple(leaf.identity for leaf in leaves[index : index + 2])
        for index in range(0, len(leaves), 2)
    ]
    assert all(_read(source_case[0], lowered, bindings, c.violations) == [] for c in lowered.checks)
    assert {
        (row["key_0"], row["key_1"])
        for row in _read(source_case[0], lowered, bindings, _primary(lowered).expression)
    } == {(1, 9007199254740993), (1, 2), (2, 1), (2, 2), (3, 1)}
    if not shared:
        last = bindings[-1]
        bindings = (
            *bindings[:-1],
            replace(
                last,
                source=replace(
                    last.source,
                    relation=last.source.relation.filter(last.source.relation.tenant == 1),
                ),
            ),
        )
        lowered = lower(_plan(root), bindings=bindings)
        inherited = [
            check
            for check in lowered.checks
            if isinstance(check, SemanticCheck) and check.requirement.node_id == root.identity
        ]
        assert [len(_read(source_case[0], lowered, bindings, c.violations)) for c in inherited] == [
            0,
            3,
        ]


def test_r43_private_source_exchange_executes_lowered_count(source_case):
    from marivo.analysis.materialization.graph_execution import PreparedGraph
    from marivo.analysis.materialization.graph_source_execution import execute_source_graph

    leaf = _leaf(source_case[1])
    binding = _bind(source_case, leaf)
    admitted = _plan(_count(leaf))
    lowered = lower(admitted, bindings=(binding,))
    result = execute_source_graph(PreparedGraph(admitted), lowered, source_case[0])
    assert result.primary.to_pylist() == [{"value": 5, "cell_tag": "defined", "cell_reason": None}]
    assert tuple(part.role for part in result.parts) == ("row_state",)
    assert result.parts[0].table["row_state__count"].to_pylist() == [5]
    assert all(submission.state == "succeeded" for submission in source_case[0].submissions)


def test_r43_transport_carries_exact_source_cell_reason_policy(source_case):
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.analysis.materialization.graph_execution import PreparedGraph
    from marivo.analysis.materialization.graph_source_execution import execute_source_graph

    leaf = _leaf(source_case[1])
    view = method_node(
        (Edge("quantity", leaf),),
        PartsTransport("view", leaf.signature.domain, (), True),
        value_type=ScalarType("int64"),
    )
    admitted = _plan(view)
    binding = _bind(source_case, leaf)
    with pytest.raises(MaterializationError, match="invalid non-Defined Cell"):
        execute_source_graph(
            PreparedGraph(admitted), lower(admitted, bindings=(binding,)), source_case[0]
        )
    assert not source_case[0]._staged_relations
    declared = replace(
        binding,
        cell_reasons=(
            ("null", ("source_null",)),
            ("undefined", ("zero_denominator",)),
            ("unknown", ("unavailable",)),
        ),
    )
    result = execute_source_graph(
        PreparedGraph(admitted), lower(admitted, bindings=(declared,)), source_case[0]
    )
    assert result.primary["cell_tag"].to_pylist() == [
        "defined",
        "null",
        "undefined",
        "unknown",
        "defined",
    ]
    assert result.primary["value"].to_pylist()[0] == 5


@pytest.mark.parametrize("form", ["table", "parquet"])
@pytest.mark.parametrize("method,expected", [("sum", 23), ("mean", 4.6)])
def test_r43_registered_row_arithmetic_uses_one_semantic_policy(
    tmp_path: Path, form: str, method: str, expected: int | float
) -> None:
    from marivo.analysis.materialization.graph_execution import PreparedGraph
    from marivo.analysis.materialization.graph_source_execution import execute_source_graph

    data = _rows()
    data = data.set_column(2, "amount", pa.array([5, 2, 3, 4, 9], type=pa.int64()))
    data = data.set_column(3, "tag", pa.array(["defined"] * 5))
    data = data.set_column(4, "reason", pa.array([None] * 5, type=pa.string()))
    backend = ibis.duckdb.connect()
    backend.create_table("facts", data)
    path = tmp_path / "facts.parquet"
    pq.write_table(data, path)
    datasource = DatasourceIR(
        "db", "db", "duckdb", {}, {}, AiContextIR(), "db", DatasourceSourceLocation("source.py", 1)
    )
    with SourceSession(provider_for("duckdb"), datasource, backend) as source:
        leaf = _leaf(form)
        binding = _bind((source, form, path), leaf)
        target = DomainSignature(leaf.signature.domain.binding, "singleton", (), (), "all")
        node = method_node(
            (Edge("quantity", leaf),),
            RowState(method, target, method, "strict", numeric_check_id="source.finite_numeric@v1"),
            value_type=ScalarType("float64" if method == "mean" else "int64"),
        )
        admitted = _plan(node)
        lowered = lower(admitted, bindings=(binding,))
        result = execute_source_graph(PreparedGraph(admitted), lowered, source)
        assert result.primary["value"].to_pylist() == [expected]
        assert result.primary["cell_tag"].to_pylist() == ["defined"]
        assert len(result.completed_checks) == 1
        assert result.completed_checks[0].requirement.obligation.check_id == (
            "source.finite_numeric@v1"
        )
        assert result.parts[0].table["row_state__sum"].to_pylist() == [23]
        if method == "mean":
            assert result.parts[0].table["row_state__count"].to_pylist() == [5]


def test_r43_strict_row_method_rejects_non_defined_input_before_result(source_case):
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.analysis.materialization.graph_execution import PreparedGraph
    from marivo.analysis.materialization.graph_source_execution import execute_source_graph

    leaf = _leaf(source_case[1])
    binding = _bind(source_case, leaf)
    target = DomainSignature(leaf.signature.domain.binding, "singleton", (), (), "all")
    node = method_node(
        (Edge("quantity", leaf),),
        RowState("sum", target, "sum", "strict", numeric_check_id="source.finite_numeric@v1"),
        value_type=ScalarType("int64"),
    )
    admitted = _plan(node)
    lowered = lower(admitted, bindings=(binding,))
    with pytest.raises(MaterializationError, match="violating rows"):
        execute_source_graph(PreparedGraph(admitted), lowered, source_case[0])
    assert all(submission.state == "succeeded" for submission in source_case[0].submissions)
    assert source_case[0]._staged_relations == {}


@pytest.mark.parametrize("failure", [ValueError, KeyboardInterrupt])
def test_r43_source_iteration_failure_closes_terminal_stream_without_temporary_stages(
    source_case, monkeypatch: pytest.MonkeyPatch, failure
):
    from marivo.analysis.materialization.graph_execution import PreparedGraph
    from marivo.analysis.materialization.graph_source_execution import execute_source_graph

    leaf = _leaf(source_case[1])
    admitted = _plan(_count(leaf))
    lowered = lower(admitted, bindings=(_bind(source_case, leaf),))
    session = source_case[0]
    original = session.batches
    stage_reads = 0
    closed = []

    class FailedStream:
        def __init__(self, schema: pa.Schema) -> None:
            self.schema = schema

        def __iter__(self):
            raise failure("selected stream stopped")
            yield

        def close(self):
            closed.append(True)

    def failing_batches(read, *, chunk_size):
        nonlocal stage_reads
        if read.purpose == "analysis.graph.stage":
            stage_reads += 1
            return FailedStream(read.schema)
        return original(read, chunk_size=chunk_size)

    monkeypatch.setattr(session, "batches", failing_batches)
    with pytest.raises(failure, match="selected stream stopped"):
        execute_source_graph(PreparedGraph(admitted), lowered, session)
    assert stage_reads == 1
    assert closed == [True]
    assert not session._staged_relations


def test_r43_duplicate_source_identity_rejects_and_releases_temporary_stage(source_case):
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.analysis.materialization.graph_execution import PreparedGraph
    from marivo.analysis.materialization.graph_source_execution import execute_source_graph

    session, form, path = source_case
    rows = _rows().set_column(1, "id", pa.array([2, 2, 1, 2, 1], type=pa.int64()))
    session._backend.create_table("facts", rows, overwrite=True)
    pq.write_table(rows, path)
    leaf = _leaf(form)
    admitted = _plan(_count(leaf))
    lowered = lower(admitted, bindings=(_bind(source_case, leaf),))
    with pytest.raises(MaterializationError, match="violating rows"):
        execute_source_graph(PreparedGraph(admitted), lowered, session)
    assert not session._staged_relations


def test_r56_decimal_count_admits_but_unqualified_time_rejects_before_business_read(source_case):
    source = source_case[0]
    base = _leaf(source_case[1])
    _plan(_count(replace(base, value_type=DecimalType(18, 2))))
    changed = (
        replace(
            base,
            definition=replace(
                base.definition,
                shape=SourceShape(
                    "duckdb",
                    source_case[1],
                    "native" if source_case[1] == "table" else "parquet",
                    TimeShape("instant", "ms", "UTC")
                    if source_case[1] == "table"
                    else TimeShape("instant", "us", "Asia/Tokyo"),
                ),
            ),
        ),
    )
    for leaf in changed:
        with pytest.raises(MethodRegistrationError, match="qualified exact key"):
            _plan(_count(leaf))
    assert source.submissions == []


def test_r43_explicit_shared_source_stage_keeps_logical_sharing_with_one_terminal_read(source_case):
    from marivo.analysis.materialization.graph_execution import PreparedGraph
    from marivo.analysis.materialization.graph_source_execution import execute_source_graph

    left, right = (_leaf(source_case[1], quantity=False) for _ in range(2))
    bindings = (_bind(source_case, left), _bind(source_case, right))
    first = method_node(
        (Edge("subject", left), Edge("subject", right)),
        MapCorrespond(
            "exact_keys",
            replace(left.signature.domain, definition_id="paired"),
            "source.exact_pairing@v1",
        ),
        value_type=ScalarType("int64"),
    )
    shared = method_node(
        (Edge("subject", first), Edge("subject", first)),
        MapCorrespond("union_keys", first.signature.domain),
        value_type=ScalarType("int64"),
    )
    admitted = _plan(shared)
    lowered = lower(admitted, bindings=bindings)
    assert (
        sum(isinstance(stage, LoweredRelation) and stage.node is first for stage in lowered.stages)
        == 1
    )
    result = execute_source_graph(PreparedGraph(admitted), lowered, source_case[0])
    assert result.primary.num_rows == 5
    assert result.contract.key_fields == ("key_0", "key_1")
    assert result.primary.schema.field("key_1").type == pa.int64()
    assert {(row["key_0"], row["key_1"]) for row in result.primary.to_pylist()} == {
        (row["tenant"], row["id"]) for row in _rows().to_pylist()
    }
    assert source_case[0]._staged_relations == {}
    terminal = [
        submission
        for submission in source_case[0].submissions
        if submission.purpose == "analysis.graph.stage"
    ]
    assert len(terminal) == 1
    assert terminal[0].source_identity == left.identity
    assert {
        identity
        for submission in source_case[0].submissions
        for identity in submission.source_identity.split("|")
    } == {left.identity, right.identity}
    physical = "facts" if source_case[1] == "table" else "ibis_read_parquet_"
    assert physical in terminal[0].sql


def test_l1_cannot_fuse_tag_selection_with_numeric_consumption(source_case):
    leaf = _leaf(source_case[1])
    defined = method_node(
        (Edge("quantity", leaf),),
        PartsTransport(
            "where",
            leaf.signature.domain,
            (),
            True,
            (ValuePredicate(leaf.signature.domain.binding, "is_defined", 0),),
        ),
        value_type=leaf.value_type,
    )
    with pytest.raises(CoreRuleError, match="consumption domain"):
        fuse_selection(_selection(defined, unknown="reject"))


@pytest.mark.parametrize("route", ["ibis", "artifact_python"])
def test_o1_lowering_reuses_static_admission(source_case, route: str) -> None:
    leaf = _leaf(source_case[1])
    if route == "artifact_python":
        fixed = FixedLeaf(
            ArtifactRef("o1-fixed"),
            leaf.fingerprint,
            leaf.signature,
            leaf.value_type,
            FixedShape(NoTime()),
        )
        admitted = _plan(_count(fixed), route)
        bindings = ()
    else:
        admitted = _plan(_count(leaf))
        bindings = (_bind(source_case, leaf),)
    with (
        patch.object(graph, "_validate_method", side_effect=AssertionError("revalidated graph")),
        patch.object(MethodRegistry, "derive", side_effect=AssertionError("rederived semantics")),
        patch.object(MethodRegistry, "select", side_effect=AssertionError("reselected method")),
        patch.object(
            MethodRegistry, "_select_derived", side_effect=AssertionError("reselected method")
        ),
    ):
        lowered = lower(admitted, bindings=bindings)
    assert lowered.admitted is admitted
    assert source_case[0].submissions == []


@pytest.mark.parametrize(
    "change",
    ["stages", "checks", "physical", "output", "node", "literal_type", "registry", "capture"],
)
def test_o1_in_place_handoff_tampering_rejects_before_submission(source_case, change: str) -> None:
    leaf = _leaf(source_case[1])
    selected = _selection(leaf, unknown="reject")
    admitted = _plan(_count(selected, "count_defined"))
    assert admitted.checks
    binding = _bind(source_case, leaf)
    if change == "stages":
        target, field, altered = admitted, "stages", admitted.stages[:-1]
    elif change == "checks":
        target, field, altered = admitted, "checks", ()
    elif change == "physical":
        target, field, altered = admitted, "physical_requirements", ()
    elif change == "output":
        target, field, altered = admitted, "primary_output", "forged"
    elif change == "node":
        target, field, altered = leaf.signature.domain, "definition_id", "forged"
    elif change == "literal_type":
        predicate = selected.parameters.predicates[0]
        target, field, altered = predicate, "value", float(predicate.value)
        assert altered == predicate.value
    elif change == "registry":
        registration = REGISTRY.lookup(admitted.physical_requirements[0].key.method)
        target, field, altered = registration, "implementations", ()
    else:
        assert admitted._handoff is not None
        target, field, altered = admitted._handoff.captured, "index", MappingProxyType({})
    original = getattr(target, field)
    object.__setattr__(target, field, altered)
    try:
        with pytest.raises(CoreRuleError, match="unchanged admitted plan"):
            lower(admitted, bindings=(binding,))
    finally:
        object.__setattr__(target, field, original)
    assert source_case[0].submissions == []


def test_o1_handoff_is_bound_to_registry_instance(source_case) -> None:
    leaf = _leaf(source_case[1])
    admitted = _plan(_count(leaf))
    other = MethodRegistry(REGISTRY.registrations)
    with pytest.raises(CoreRuleError, match="unchanged admitted plan"):
        lower(admitted, bindings=(_bind(source_case, leaf),), registry=other)
    assert source_case[0].submissions == []


def test_o1_shadowed_registration_rejects_handoff(source_case) -> None:
    leaf = _leaf(source_case[1])
    root = _count(leaf)
    selected = REGISTRY.lookup(root.method)
    shadow = replace(
        next(item for item in REGISTRY.registrations if item.semantics.key != root.method)
    )
    registry = MethodRegistry((shadow, selected))
    admitted = plan(root, routes=(RouteChoice(root.identity, "ibis"),), registry=registry)
    binding = _bind(source_case, leaf)
    assert lower(admitted, bindings=(binding,), registry=registry).admitted is admitted

    object.__setattr__(shadow, "semantics", selected.semantics)
    object.__setattr__(shadow, "implementations", ())
    assert registry.lookup(root.method) is shadow
    with pytest.raises(CoreRuleError, match="unchanged admitted plan"):
        lower(admitted, bindings=(binding,), registry=registry)
    assert source_case[0].submissions == []
