"""Public integer-sum consumers and exact route-registration regressions."""

from pathlib import Path
from typing import Literal

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.graph_protocol import descriptor_plan
from marivo.analysis.methods.builtin import implementations
from marivo.analysis.methods.physical import Qualified, ScalarType, SourceShape, TimeShape
from marivo.analysis.methods.semantics import MethodKey
from tests.analysis.graph.physical_workloads import (
    workload,
)


@pytest.mark.runtime
def test_duckdb_cost_routes_keep_the_original_exact_sum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        assert [row["amount"] for row in work.rows[:6]] == [None, 2, 3, 4, 5, 6]
        assert [row["id"] for row in work.rows[:2]] == [9007199254740992, 9007199254740993]
        references: list[object] = []
        for route in ("ibis", "ibis_python"):
            result = work.source(route)
            assert result.to_pandas().value.tolist() == [529]
            assert work.validate(result)["passed"] is True
            identity = work.identity(result)
            assert identity["root_route"] == route
            references.append(identity["artifact_ref"])
            fixed = work.fixed(result)
            first = fixed.execute()
            assert first.to_pandas().value.tolist() == [529]
            assert work.validate(first)["passed"] is True
            count = len(work.session.runs().items)
            hit = fixed.execute()
            assert hit.state.artifact_ref == first.state.artifact_ref
            assert len(work.session.runs().items) == count
            assert work.session._runtime.store.resources(work.session.id) == ()
        assert references[0] != references[1]


@pytest.mark.runtime
def test_duckdb_local_original_sum_consumes_group_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        independent_totals = {"started": 0, "finished": 0}
        for row in work.rows:
            amount = row["amount"]
            if amount is not None:
                assert isinstance(amount, int)
                independent_totals[str(row["kind"])] += amount
        assert independent_totals == {"started": 262, "finished": 267}
        axis = ms.ref.dimension("cost.facts.kind")
        raw = work.session.members(ms.ref.entity("cost.facts")).observe(
            ms.ref.metric("cost.facts_total"),
            during=mv.time_scope(start="2026-08-01", end="2026-08-02"),
            by=(
                mv.member(),
                axis,
            ),
        )
        assert isinstance(raw, mv.LogicalNumericRelation)
        grouped = raw.group_by(axis).rollup()
        assert isinstance(grouped, mv.LogicalRolledNumericRelation)
        assert grouped._node.root.signature.domain.kind == "group"
        result = grouped.deviation(method="zscore").observed.rollup().execute()
        assert result.to_pandas().value.tolist() == [sum(independent_totals.values())]
        assert result.to_pandas().cell_tag.tolist() == ["defined"]
        assert work.validate(result)["passed"] is True
        assert result._dataset is not None
        plan = descriptor_plan(result._dataset.artifact.descriptor, result._node.definition)
        root = next(
            item
            for item in plan.physical_requirements
            if item.node_id == result._node.definition.identity
        )
        assert root.key.method == MethodKey("state_rollup.sum_zero")
        assert root.key.input_types == (ScalarType("int64"),)
        assert root.key.input_domains == ("group",)
        assert root.key.shape == SourceShape(
            "duckdb", "table", "native", TimeShape("instant", "us", "UTC")
        )
        assert root.key.route == "ibis_python"
        assert isinstance(root.implementation.qualification, Qualified)
        assert root.implementation.qualification.implementation_id.startswith(
            "r93.c09.local.duckdb."
        )
        assert work.session._runtime.store.resources(work.session.id) == ()


@pytest.mark.parametrize("name", ("cell.difference", "attribution.additive_difference"))
def test_cost_sum_increment_does_not_grant_other_duckdb_local_consumers(
    name: Literal["cell.difference", "attribution.additive_difference"],
) -> None:
    assert not any(
        isinstance(item.key.shape, SourceShape)
        and item.key.shape.backend == "duckdb"
        and item.key.shape.time == TimeShape("instant", "us", "UTC")
        and item.key.route == "ibis_python"
        and isinstance(item.qualification, Qualified)
        and item.qualification.implementation_id.startswith("r93.c09.local.duckdb.")
        for item in implementations(MethodKey(name))
    )
