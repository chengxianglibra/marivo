"""Public original-state L8 execution, atomic failure and source-free cold reuse."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import asdict, replace
from typing import Literal, NoReturn, TypeAlias

import duckdb
import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.compiler.graph_lowering import LoweredLocal
from marivo.analysis.core.domain_captures import DomainPreparationError
from marivo.analysis.materialization import execute_deadline
from marivo.analysis.materialization import graph_local_execution as local
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import ExchangePart, ExchangeResult
from marivo.datasource.adapters import SourceSession
from tests.shared_fixtures import DslCase, DslCaseFactory, run_ids
from tests.support.paths import PROJECT_ROOT

Method: TypeAlias = Literal["sum", "count", "mean", "ratio", "weighted_mean", "linear"]
Saved: TypeAlias = mv.MaterializedNumericRelation | mv.MaterializedRatioRelation


def _saved(case: DslCase, method: Method = "sum") -> Saved:
    with duckdb.connect(str(case.database_path)) as db:
        db.execute('DELETE FROM "order"')
        db.execute('ALTER TABLE "order" ADD COLUMN weight DOUBLE')
        db.execute("""INSERT INTO "order" VALUES
            ('a1','A','web','paid','2026-08-10',10,1),
            ('a2','A','web','paid','2026-08-11',30,3),
            ('b1','B','web','paid','2026-08-10',90,2),
            ('c1','C','web','paid','2026-08-10',0,0)""")
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace("granularity='second',", "granularity='second', is_default=True,")
        + "\nweight = ms.measure_column(name='weight', entity=orders, column='weight', additivity=ms.additive_all())\n"
        + "orders_count = ms.count(name='orders_count', entity=orders, time=ordered_at)\n"
    )
    ms.load(workspace_dir=case.root)
    amount = ms.ref.measure("sales.order.amount")
    base = ms.ref.metric("sales.revenue")
    metric = (
        ms.ref.metric("sales.orders_count")
        if method == "count"
        else mv.runtime_metric.aggregate(amount, agg="mean", label="mean")
        if method == "mean"
        else mv.runtime_metric.ratio(base, base, label="ratio")
        if method == "ratio"
        else mv.runtime_metric.weighted_mean(
            amount, ms.ref.measure("sales.order.weight"), label="weighted"
        )
        if method == "weighted_mean"
        else mv.runtime_metric.linear(add=[base, base], subtract=[base], label="linear")
        if method == "linear"
        else base
    )
    fixed = (
        case.session.members(ms.ref.entity("sales.customer"))
        .observe(
            metric,
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=(mv.member(),),
        )
        .execute()
    )
    assert isinstance(fixed, (mv.MaterializedNumericRelation, mv.MaterializedRatioRelation))
    return fixed


def _forbid_source(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("fixed original reduction accessed a source")

    monkeypatch.setattr(SourceSession, "batches", forbidden)


def _publications(case: DslCase) -> tuple[int, ...]:
    counts: list[int] = []
    with case.session._runtime.store._connection() as connection:
        for table in ("dataset_artifacts", "dataset_evidence", "findings"):
            row = connection.execute("SELECT COUNT(*) FROM " + table).fetchone()
            assert row is not None and isinstance(row[0], int)
            counts.append(row[0])
    return tuple(counts)


@pytest.mark.runtime
@pytest.mark.parametrize("method", ["sum", "count", "mean", "ratio", "weighted_mean", "linear"])
def test_public_original_chain_has_independent_oracle_and_exact_cache(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch, method: Method
) -> None:
    case = analysis_dsl_case_factory("j1")
    saved = _saved(case, method)
    chain = saved.group_by(ms.ref.entity("sales.customer")).rollup().rollup()
    _forbid_source(monkeypatch)
    groups: list[int] = []
    execute = local._reduction_group_result

    def grouped(
        group: tuple[LoweredLocal, ...], source: ExchangeResult, binding: str
    ) -> ExchangeResult:
        groups.append(len(group))
        return execute(group, source, binding)

    monkeypatch.setattr(local, "_reduction_group_result", grouped)
    result = chain.execute()
    expected = (
        4
        if method == "count"
        else 130 / 4
        if method == "mean"
        else 1
        if method == "ratio"
        else 280 / 6
        if method == "weighted_mean"
        else 130
    )
    assert result.to_pandas().value.tolist() == [expected]
    assert groups == [2]
    direct = saved.rollup().execute()
    assert direct.to_pandas().equals(result.to_pandas())
    assert direct._dataset is not None and result._dataset is not None
    assert [(p.role, p.table.to_pylist()) for p in direct._dataset.verified().parts] == [
        (p.role, p.table.to_pylist()) for p in result._dataset.verified().parts
    ]
    restored = case.session.artifact(result.state.artifact_ref)
    assert isinstance(
        restored, (mv.MaterializedRolledNumericRelation, mv.MaterializedRolledRatioRelation)
    )
    assert asdict(restored.contract()) == asdict(result.contract())
    before = run_ids(case.session)
    assert chain.execute().state.artifact_ref == result.state.artifact_ref
    assert run_ids(case.session) == before and groups == [2]
    assert case.session._runtime.store.resources(case.session.id) == ()


@pytest.mark.runtime
@pytest.mark.parametrize("failure", ["missing", "duplicate", "overflow", "timeout", "cancel"])
def test_group_failure_has_no_retry_or_partial_publication(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    case = analysis_dsl_case_factory("j1")
    saved = _saved(case)
    chain = saved.group_by(ms.ref.entity("sales.customer")).rollup().rollup()
    _forbid_source(monkeypatch)
    before = _publications(case)
    attempts: list[int] = []
    execute = local._reduction_group_result

    def grouped(
        group: tuple[LoweredLocal, ...], source: ExchangeResult, binding: str
    ) -> ExchangeResult:
        attempts.append(len(group))
        if failure == "cancel":
            raise KeyboardInterrupt()
        if failure == "timeout":
            deadline = execute_deadline.CURRENT.get()
            assert deadline is not None
            object.__setattr__(deadline, "start", deadline.start - 601)
        parts = list(source.parts)
        if failure == "missing":
            parts = [p for p in parts if p.role != "original_state"]
        elif failure == "duplicate":
            index = next(i for i, p in enumerate(parts) if p.role == "original_state")
            table = parts[index].table
            parts[index] = ExchangePart(
                "original_state", pa.concat_tables((table, table.slice(0, 1)))
            )
        elif failure == "overflow":
            index = next(i for i, p in enumerate(parts) if p.role == "original_state")
            table = parts[index].table
            column = table.schema.get_field_index("original_state__sum")
            table = table.set_column(
                column, "original_state__sum", pa.array([2**63 - 1, 90, 0, 0], type=pa.int64())
            )
            parts[index] = ExchangePart("original_state", table)
            source = replace(
                source,
                primary=source.primary.set_column(
                    1, "value", pa.array([2**63 - 1, 90, 0, None], type=pa.int64())
                ),
            )
        return execute(group, replace(source, parts=tuple(parts)), binding)

    monkeypatch.setattr(local, "_reduction_group_result", grouped)
    error = (
        KeyboardInterrupt
        if failure == "cancel"
        else DomainPreparationError
        if failure == "timeout"
        else MaterializationError
    )
    with pytest.raises(error):
        chain.execute()
    assert attempts == [2]
    assert _publications(case) == before
    assert case.session._runtime.store.resources(case.session.id) == ()


@pytest.mark.runtime
def test_materialized_intermediate_ends_l8(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    saved = _saved(case, "mean")
    intermediate = saved.group_by(ms.ref.entity("sales.customer")).rollup().execute()
    _forbid_source(monkeypatch)

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("L8 crossed an explicit Artifact boundary")

    monkeypatch.setattr(local, "_reduction_group_result", forbidden)
    assert intermediate.rollup().execute().to_pandas().value.tolist() == [32.5]


@pytest.mark.runtime
def test_cold_reduction_reads_saved_primary_and_continues_without_sources(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    saved = _saved(case, "mean")
    result = saved.group_by(ms.ref.entity("sales.customer")).rollup().rollup().execute()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    shutil.rmtree(case.root / "models")
    script = """
import sys
import ibis
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import graph_local_execution as local
from marivo.datasource.adapters import SourceSession

def forbidden(*args, **kwargs):
    raise AssertionError('cold fixed reduction opened a source or Semantic')
SourceSession.batches = forbidden
ibis.duckdb.connect = forbidden
ms.load = forbidden
session = mv.session.resume(sys.argv[1], by='id')
saved = session.artifact(mv.ArtifactRef(ref=sys.argv[2]))
result = session.artifact(mv.ArtifactRef(ref=sys.argv[3]))
assert result.to_pandas().value.tolist() == [32.5]
visits = []
execute = local._reduction_group_result
def grouped(group, source, binding):
    visits.append(len(group))
    return execute(group, source, binding)
local._reduction_group_result = grouped
# A different two-stage definition requires a new cold local computation.
chain = saved.group_by(ms.ref.entity('sales.customer')).rollup().group_by(ms.ref.entity('sales.customer')).rollup().rollup()
new = chain.execute()
assert new.to_pandas().value.tolist() == [32.5]
assert visits == [3]
assert chain.execute().state.artifact_ref == new.state.artifact_ref
assert visits == [3]
assert session._runtime.store.resources(session.id) == ()
"""
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            case.session.id,
            saved.state.artifact_ref.ref,
            result.state.artifact_ref.ref,
        ],
        cwd=case.root,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert process.returncode == 0, process.stdout + process.stderr


@pytest.mark.runtime
@pytest.mark.parametrize("boundary", ["time_mapping", "fold"])
def test_time_coarsening_and_ordered_fold_keep_ordinary_execution(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    case = analysis_dsl_case_factory("j1")
    if boundary == "fold":
        model = case.root / "models/semantic/sales/models.py"
        model.write_text(
            model.read_text()
            + "\nstatus_amount = ms.measure_column(name='status_amount', entity=orders, column='amount', additivity=ms.additive_all(except_=(ordered_at,)), status_time_dimension=ordered_at, status_time_fold='mean')\nfolded = ms.aggregate(name='folded', measure=status_amount, agg='sum')\n"
        )
    _saved(case)
    customer = ms.ref.entity("sales.customer")
    members = case.session.members(customer)
    if boundary == "time_mapping":
        grid = mv.time_grid(
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"), grain=mv.grain("day")
        )
        saved = members.observe(
            ms.ref.metric("sales.revenue"),
            during=grid,
            via=ms.ref.relationship("sales.order_buyer"),
            by=(mv.member(),),
        ).execute()
        chain = saved.group_by(mv.grain("month")).rollup().rollup()
    else:
        with duckdb.connect(str(case.database_path)) as db:
            db.execute("DELETE FROM customer WHERE customer_id IN ('C','D')")
            db.execute("DELETE FROM \"order\" WHERE order_id = 'c1'")
            db.execute(
                """INSERT INTO "order" VALUES
                ('b2','B','web','paid','2026-08-11',0,0)"""
            )
        saved = members.observe(
            ms.ref.metric("sales.folded"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=(mv.member(),),
        ).execute()
        chain = saved.group_by(customer).rollup().rollup()
    _forbid_source(monkeypatch)

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("L8 crossed a time mapping or ordered-fold boundary")

    monkeypatch.setattr(local, "_reduction_group_result", forbidden)
    assert chain.execute().to_pandas().value.tolist() == [130 if boundary == "time_mapping" else 65]
