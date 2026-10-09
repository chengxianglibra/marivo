"""Public fixed selection, atomic failure and independent source-free recovery."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import asdict, replace
from typing import Literal, NoReturn

import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.compiler.graph_lowering import LoweredLocal
from marivo.analysis.core.domain_captures import DomainPreparationError
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.core.predicates import ValuePredicate
from marivo.analysis.materialization import execute_deadline
from marivo.analysis.materialization import graph_local_execution as local
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import ExchangeResult
from marivo.analysis.methods.predicates import evaluate_leaf
from marivo.datasource.adapters import SourceSession
from tests.shared_fixtures import DslCase, DslCaseFactory, run_ids
from tests.support.paths import PROJECT_ROOT


def _saved(case: DslCase) -> mv.MaterializedNumericRelation:
    n = case.names
    result = (
        case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
        .observe(
            ms.ref.metric(f"{n.domain}.{n.revenue}"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship(f"{n.domain}.{n.buyer}"),
            by=(mv.member(),),
        )
        .execute()
    )

    assert isinstance(result, mv.MaterializedNumericRelation)
    return result


def _chain(saved: mv.MaterializedNumericRelation) -> mv.LogicalSelectedNumericRelation:
    inner = saved.where(saved.value.gt(0))
    return inner.where(inner.value.lt(100))


def _forbid_source(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("fixed selection accessed a source")

    monkeypatch.setattr(SourceSession, "batches", forbidden)


def _publications(session: mv.Session) -> tuple[int, ...]:
    counts: list[int] = []
    with session._runtime.store._connection() as connection:
        for table in ("dataset_artifacts", "dataset_evidence", "findings"):
            row = connection.execute("SELECT COUNT(*) FROM " + table).fetchone()
            assert row is not None and isinstance(row[0], int)
            counts.append(row[0])
    return tuple(counts)


@pytest.mark.runtime
def test_public_selection_parts_contract_members_summary_rollup_and_cache(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j2")
    saved = _saved(case)
    logical = _chain(saved)
    _forbid_source(monkeypatch)
    executed: list[int] = []
    execute = local._selection_group_result

    def grouped(
        group: tuple[LoweredLocal, ...], source: ExchangeResult, binding: str
    ) -> ExchangeResult:
        executed.append(len(group))
        return execute(group, source, binding)

    monkeypatch.setattr(local, "_selection_group_result", grouped)
    result = logical.execute()
    assert executed == [2]
    frame = result.to_pandas()
    assert frame.set_index("member")["value"].to_dict() == {"A": 60}
    assert frame.cell_tag.tolist() == ["defined"]
    assert result._dataset is not None
    exchange = result._dataset.verified()
    parts = {part.role: part.table for part in exchange.parts}
    assert {"subject", "original_state", "coverage"} <= set(parts)
    assert parts["original_state"]["original_state__sum"].to_pylist() == [60]
    assert parts["original_state"]["original_state__non_null_count"].to_pylist() == [1]
    assert parts["coverage"]["coverage__complete"].to_pylist() == [True]
    assert result.members().execute().to_pandas().member.tolist() == ["A"]
    assert result.summarize(mv.sum()).execute().to_pandas().value.tolist() == [60]
    assert result.rollup().execute().to_pandas().value.tolist() == [60]
    disclosure = asdict(result.contract())
    restored = case.session.artifact(result.state.artifact_ref)
    assert isinstance(
        restored, (mv.MaterializedNumericRelation, mv.MaterializedSelectedNumericRelation)
    )
    assert asdict(restored.contract()) == disclosure
    before = run_ids(case.session)
    assert logical.execute().state.artifact_ref == result.state.artifact_ref
    assert run_ids(case.session) == before and executed == [2]
    assert case.session._runtime.store.resources(case.session.id) == ()


@pytest.mark.runtime
@pytest.mark.parametrize("failure", ["predicate", "duplicate", "timeout", "cancel"])
def test_fused_failure_never_retries_or_publishes(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    failure: Literal["predicate", "duplicate", "timeout", "cancel"],
) -> None:
    case = analysis_dsl_case_factory("j2")
    saved = _saved(case)
    logical = _chain(saved)
    _forbid_source(monkeypatch)
    before = _publications(case.session)
    runs = run_ids(case.session)
    frame = saved.to_pandas()
    attempts: list[int] = []
    execute = local._selection_group_result

    def grouped(
        group: tuple[LoweredLocal, ...], source: ExchangeResult, binding: str
    ) -> ExchangeResult:
        attempts.append(len(group))
        if failure == "duplicate":
            source = replace(
                source, primary=pa.concat_tables((source.primary, source.primary.slice(0, 1)))
            )
        return execute(group, source, binding)

    monkeypatch.setattr(local, "_selection_group_result", grouped)
    evaluate = evaluate_leaf

    def broken(
        predicate: ValuePredicate, left: dict[str, object], right: dict[str, object] | None
    ) -> bool | None:
        if predicate.operator == "lt":
            if failure == "predicate":
                return evaluate(predicate, {**left, "cell_tag": "null", "value": None}, right)
            if failure == "cancel":
                raise KeyboardInterrupt()
            if failure == "timeout":
                deadline = execute_deadline.CURRENT.get()
                assert deadline is not None
                object.__setattr__(deadline, "start", deadline.start - 601)
        return evaluate(predicate, left, right)

    monkeypatch.setattr(local, "evaluate_leaf", broken)
    expected = {
        "predicate": CoreRuleError,
        "duplicate": MaterializationError,
        "timeout": DomainPreparationError,
        "cancel": KeyboardInterrupt,
    }[failure]
    with pytest.raises(expected) as error:
        logical.execute()
    if failure == "predicate":
        assert isinstance(error.value, CoreRuleError)
        assert error.value.location == f"analysis.predicate.{logical._node.root.identity}"
    assert attempts == [2]
    assert _publications(case.session) == before
    assert len(run_ids(case.session) - runs) == 1
    assert case.session._runtime.store.resources(case.session.id) == ()
    assert saved.to_pandas().equals(frame)


@pytest.mark.runtime
def test_cold_selection_reads_result_and_computes_new_chain_without_source(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    saved = _saved(case)
    selected = _chain(saved).execute()
    case.database_path.unlink()
    shutil.rmtree(case.root / "models")
    script = """
import sys
import ibis
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import graph_local_execution as local
from marivo.datasource.runtime import DatasourceConnectionService
def forbidden(*args, **kwargs):
    raise AssertionError('fixed cold execution opened source or Semantic')
ibis.duckdb.connect = forbidden
ms.load = forbidden
DatasourceConnectionService.use_backend = forbidden
session = mv.session.resume(sys.argv[1], by='id')
selected = session.artifact(sys.argv[3])
assert selected.to_pandas().set_index('member').value.to_dict() == {'A': 60}
selected.contract()
saved = session.artifact(sys.argv[2])
visits = []
original = local._selection_group_result
def grouped(group, source, binding):
    visits.append(len(group))
    return original(group, source, binding)
local._selection_group_result = grouped
first = saved.where(saved.value.gt(20))
chain = first.where(first.value.lt(100))
result = chain.execute()
assert visits == [2]
assert result.to_pandas().set_index('member').value.to_dict() == {'A': 60}
assert result.rollup().execute().to_pandas().value.tolist() == [60]
assert result.members().execute().to_pandas().member.tolist() == ['A']
assert chain.execute().state.artifact_ref == result.state.artifact_ref
assert visits == [2]
assert session._runtime.store.resources(session.id) == ()
"""
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            case.session.id,
            saved.state.artifact_ref.ref,
            selected.state.artifact_ref.ref,
        ],
        cwd=case.root,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert process.returncode == 0, process.stdout + process.stderr


@pytest.mark.runtime
def test_fixed_difference_endpoints_follow_selected_complete_keys(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j2")
    n = case.names
    members = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
    current = members.observe(
        ms.ref.metric(f"{n.domain}.{n.revenue}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{n.domain}.{n.buyer}"),
        by=(mv.member(),),
    ).execute()
    baseline = members.observe(
        ms.ref.metric(f"{n.domain}.{n.revenue}"),
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=ms.ref.relationship(f"{n.domain}.{n.buyer}"),
        by=(mv.member(),),
    ).execute()
    difference = current.compare(baseline).execute()
    _forbid_source(monkeypatch)
    first = difference.where(difference.value.gt(-45))
    chain = first.where(first.value.lt(10))
    groups: list[int] = []
    execute = local._selection_group_result

    def grouped(
        group: tuple[LoweredLocal, ...], source: ExchangeResult, binding: str
    ) -> ExchangeResult:
        groups.append(len(group))
        return execute(group, source, binding)

    monkeypatch.setattr(local, "_selection_group_result", grouped)
    result = chain.execute()
    assert groups == [2]
    assert result.to_pandas().set_index("member").value.to_dict() == {"A": -40, "D": 0}
    assert result._dataset is not None
    exchange = result._dataset.verified()
    assert {part.role for part in exchange.parts} == {
        "subject",
        "current_endpoint",
        "baseline_endpoint",
        "correspondence",
    }
    for part in exchange.parts:
        assert part.table["key_0"].to_pylist() == ["A", "D"]


@pytest.mark.runtime
def test_display_and_fixed_reference_selection_keep_the_original_scope(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j2")
    saved = _saved(case)
    ranking = saved.rank(order="descending", ties="min").execute()
    reference = saved.rollup().execute()
    shares = saved.share_of(reference).execute()
    _forbid_source(monkeypatch)

    def forbidden(*args: object) -> NoReturn:
        pytest.fail("specialized retained scope entered ordinary selection fusion")

    monkeypatch.setattr(local, "_selection_group_result", forbidden)
    first = ranking.where(ranking.ranks.value.gt(0))
    result = first.where(first.ranks.value.lt(3)).execute()
    assert result.values.to_pandas().set_index("member").value.to_dict() == {"A": 60, "B": 120}
    first_share = shares.where(shares.value.gt(0.1))
    selected_share = first_share.where(first_share.value.lt(0.5)).execute()
    assert selected_share.to_pandas().set_index("member").value.to_dict() == {
        "A": pytest.approx(1 / 3)
    }
    assert selected_share._dataset is not None and shares._dataset is not None
    before = {part.role: part.table for part in shares._dataset.verified().parts}
    after = {part.role: part.table for part in selected_share._dataset.verified().parts}
    for role in ("fixed_reference", "reference_proof", "stratum_values"):
        assert after[role].equals(before[role])
