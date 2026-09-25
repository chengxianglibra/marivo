"""Private J4 source preparation and Association numerical admission."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import duckdb
import ibis
import ibis.expr.types as ir
import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.analysis.materialization.dsl_j4_source as j4_source
import marivo.semantic as ms
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.materialization.dsl_j4_source import run_j4_source
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.ibis_batches import IbisBatchStream
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.observation.dsl_j1 import J1Context, J4Association
from marivo.analysis.operators.association_values import reduce_entity_spearman
from marivo.analysis.operators.errors import CorrelationError
from tests.shared_fixtures import DslCase, DslCaseFactory, DslScenario


def _association(case: DslCase) -> J4Association:
    store = SessionStore(case.root)
    store.create_session("j4-source", session_ref="session")
    state = case.catalog._state
    context = J1Context(state.registry, state.sidecar, "session", store.store_id)
    domain = case.names.domain
    members = context.members(ms.ref.entity(f"{domain}.customer"))
    during = mv.time_scope(start="2026-08-01", end="2026-09-01")
    via = ms.ref.relationship(f"{domain}.order_buyer")
    revenue = members.observe(ms.ref.metric(f"{domain}.revenue"), during=during, via=via)
    count = members.observe(ms.ref.metric(f"{domain}.order_count"), during=during, via=via)
    return revenue.correlate(count, method="spearman")


@contextmanager
def _source(case: DslCase) -> Iterator[tuple[ibis.BaseBackend, dict[str, ir.Table]]]:
    backend = ibis.duckdb.connect(str(case.database_path))
    try:
        domain = case.names.domain
        yield (
            backend,
            {
                f"{domain}.customer": backend.table("customer"),
                f"{domain}.order": backend.table("order"),
            },
        )
    finally:
        backend.disconnect()


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [("j4", -0.4), ("j4_ties", 7 / 9)],
)
def test_j4_spearman_uses_real_source_and_python_kernel(
    analysis_dsl_case_factory: DslCaseFactory,
    scenario: DslScenario,
    expected: float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory(scenario)
    association = _association(case)
    assert tuple(item.role for item in association.root.inputs) == ("left", "right")
    assert association.left.root.inputs[0].root is association.right.root.inputs[0].root
    steps: list[str] = []
    original = j4_source.run_j1_source

    def counted(*args: object, **kwargs: object) -> object:
        root = args[1]
        assert isinstance(root, LogicalRootHandle)
        steps.append(root.operator_id)
        return original(*args, **kwargs)

    monkeypatch.setattr(j4_source, "run_j1_source", counted)
    with _source(case) as (backend, tables):
        result = run_j4_source(association, backend, tables)
    assert steps == ["dsl.j1.members", "dsl.j1.observe", "dsl.j1.observe"]
    assert result.coefficient == pytest.approx(expected)
    assert result.status == "valid"
    assert (result.input_observation_count, result.matched_observation_count) == (4, 4)
    assert (result.null_pair_count, result.complete_pair_count) == (0, 4)
    assert result.metric_key_a == f"metric:{case.names.domain}.revenue"
    assert result.metric_key_b == f"metric:{case.names.domain}.order_count"
    assert (result.method, result.method_version) == ("spearman", 1)


def test_j4_rejects_independent_member_roots(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j4")
    association = _association(case)
    state = case.catalog._state
    other = J1Context(state.registry, state.sidecar, "session", association.context.store_id)
    members = other.members(ms.ref.entity(f"{case.names.domain}.customer"))
    count = members.observe(
        ms.ref.metric(f"{case.names.domain}.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{case.names.domain}.order_buyer"),
    )
    from marivo.analysis.datasets.errors import DatasetConstructionError

    with pytest.raises(DatasetConstructionError, match="one explicit Entity member"):
        association.left.correlate(count)


def test_j4_construction_does_not_connect_to_business_source(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j4")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("business source opened during construction")

    monkeypatch.setattr(duckdb, "connect", forbidden)
    association = _association(case)
    assert association.root.operator_id == "dsl.j1.correlate"


def test_j4_source_retains_empty_count_and_excludes_null_revenue_pair(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j4")
    connection = duckdb.connect(str(case.database_path))
    try:
        connection.execute('DELETE FROM "order" WHERE customer_id = ?', ["D"])
    finally:
        connection.close()
    association = _association(case)
    with _source(case) as (backend, tables):
        result = run_j4_source(association, backend, tables)
    assert (result.input_observation_count, result.matched_observation_count) == (4, 4)
    assert (result.null_pair_count, result.complete_pair_count) == (1, 3)
    assert result.coefficient == pytest.approx(-0.5)


def _observed(
    values: list[float | None],
    *,
    members: list[str] | None = None,
    tags: list[str] | None = None,
) -> pa.Table:
    keys = ["A", "B", "C", "D"] if members is None else members
    chosen_tags = (
        ["defined" if value is not None else "null" for value in values] if tags is None else tags
    )
    return pa.table(
        {
            "member": pa.array(keys, type=pa.string()),
            "value": pa.array(values, type=pa.float64()),
            "cell_tag": pa.array(chosen_tags, type=pa.string()),
            "cell_reason": pa.array(
                [None if tag == "defined" else "empty_contribution" for tag in chosen_tags],
                type=pa.string(),
            ),
            "state_sum": pa.array(values, type=pa.float64()),
            "non_null_count": pa.array(
                [int(value is not None) for value in values], type=pa.int64()
            ),
            "row_count": pa.array([int(value is not None) for value in values], type=pa.int64()),
        }
    )


def test_j4_null_pairs_are_counted_after_exact_domain_pairing() -> None:
    left = _observed([1.0, 2.0, 4.0, None])
    right = _observed([4.0, 1.0, 3.0, 2.0])
    result = reduce_entity_spearman(left, right, ("metric:a", "metric:b"))
    assert (
        result.matched_observation_count,
        result.null_pair_count,
        result.complete_pair_count,
    ) == (4, 1, 3)
    assert result.coefficient == pytest.approx(-0.5)


def test_j4_pairs_by_identity_after_right_input_reordering() -> None:
    left = _observed([1.0, 2.0, 4.0, 8.0])
    right = _observed([2.0, 3.0, 1.0, 4.0], members=["D", "C", "B", "A"])
    result = reduce_entity_spearman(left, right, ("metric:a", "metric:b"))
    assert result.coefficient == pytest.approx(-0.4)


def test_j4_ibis_pair_preflight_rejects_missing_key_and_closes_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = ibis.duckdb.connect()
    closed = 0
    original = IbisBatchStream.close

    def counted_close(self: IbisBatchStream) -> None:
        nonlocal closed
        closed += 1
        original(self)

    monkeypatch.setattr(IbisBatchStream, "close", counted_close)
    try:
        left = ibis.memtable({"member": ["A", "B"], "value": [1.0, 2.0]})
        right = ibis.memtable({"member": ["A"], "value": [4.0]})
        with pytest.raises(MaterializationError, match="keys differ or repeat"):
            j4_source._require_complete_keys(backend, left, right)
        assert closed == 1
    finally:
        backend.disconnect()


@pytest.mark.parametrize(
    "right",
    [
        _observed([4.0, 1.0, 3.0], members=["A", "B", "C"]),
        _observed([4.0, 1.0, 3.0, 2.0], members=["A", "A", "C", "D"]),
        _observed([4.0, 1.0, 3.0, 2.0], tags=["defined", "unknown", "defined", "defined"]),
        _observed([4.0, 1.0, float("inf"), 2.0]),
        _observed([1.0, 1.0, 1.0, 1.0]),
    ],
    ids=["missing-key", "duplicate-key", "unknown-cell", "nonfinite", "constant"],
)
def test_j4_rejects_invalid_or_unusable_pairs(right: pa.Table) -> None:
    with pytest.raises(CorrelationError):
        reduce_entity_spearman(_observed([1.0, 2.0, 4.0, 8.0]), right, ("metric:a", "metric:b"))


def test_j4_rejects_insufficient_complete_pairs() -> None:
    left = _observed([1.0, None, None, None])
    right = _observed([4.0, 3.0, 2.0, 1.0])
    with pytest.raises(CorrelationError, match="insufficient_pairs"):
        reduce_entity_spearman(left, right, ("metric:a", "metric:b"))


def test_j4_rejects_decimal_input() -> None:
    left = _observed([1.0, 2.0, 3.0, 4.0])
    left = left.set_column(
        left.schema.get_field_index("value"),
        "value",
        pa.array([1, 2, 3, 4], type=pa.decimal128(10, 0)),
    )
    with pytest.raises(CorrelationError, match="int64 or float64"):
        reduce_entity_spearman(left, _observed([4.0, 3.0, 2.0, 1.0]), ("metric:a", "metric:b"))
