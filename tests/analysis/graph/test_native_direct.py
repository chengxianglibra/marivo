"""Pure-native terminal exchanges and their retained validation boundaries."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Literal, NoReturn

import ibis.expr.operations as ops
import ibis.expr.types as ir
import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.compiler.graph_lowering import LoweredPlan
from marivo.analysis.compiler.graph_plan import CheckRequirement
from marivo.analysis.core.graph import MethodNode, topology
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    DerivedQuantity,
    DomainSignature,
    Signature,
)
from marivo.analysis.materialization import graph_source_execution as native
from marivo.analysis.materialization.cell_arrow import logical_table
from marivo.analysis.materialization.cell_arrow import rows as cell_rows
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import (
    CompletedCheck,
    ExchangeContract,
    from_arrow,
)
from marivo.analysis.methods.semantics import MethodKey
from marivo.analysis.session._lazy_read_model import FailedRun
from marivo.datasource import adapters
from marivo.datasource.adapters import CompiledRead, SourceBatchStream, SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.physical_workloads import Workload, workload
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.graph.source_fixtures import author_source_project
from tests.datasource.source_cases import source_case


def _values(work: Workload) -> mv.LogicalNumericRelation:
    values = work.session.members(ms.ref.entity("cost.facts")).observe(
        ms.ref.metric("cost.facts_total"),
        during=mv.time_scope(start="2026-08-01", end="2026-08-02"),
        by=(ms.ref.entity("cost.facts"),),
    )
    assert isinstance(values, mv.LogicalNumericRelation)
    return values


def _sum(work: Workload) -> mv.LogicalRolledNumericRelation:
    result = _values(work).rollup()
    assert isinstance(result, mv.LogicalRolledNumericRelation)
    return result


def _publications(session: mv.Session) -> tuple[int, ...]:
    counts = []
    with session._runtime.store._connection() as connection:
        for name in ("dataset_artifacts", "dataset_evidence", "findings"):
            row = connection.execute("SELECT COUNT(*) FROM " + name).fetchone()
            assert row is not None and isinstance(row[0], int)
            counts.append(row[0])
    return tuple(counts)


def _scalar_parts(
    result: mv.MaterializedRolledNumericRelation | mv.MaterializedGroupedNumericRelation,
    total: int,
    support: int,
) -> None:
    assert result._dataset is not None
    checked = result._dataset.verified()
    assert checked.primary is not None
    assert checked.primary.schema.field("value").type == pa.int64()
    assert cell_rows(checked.primary) == [
        {"value": total, "cell_tag": "defined", "cell_reason": None}
    ]
    parts = {part.role: part.table for part in checked.parts}
    assert {"original_state", "coverage"} <= set(parts)
    assert parts["original_state"].to_pylist() == [
        {"original_state__sum": total, "original_state__non_null_count": support}
    ]
    assert parts["coverage"].to_pylist() == [{"coverage__complete": True}]


def _forbid_staging(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("Pure-native execution captured or restaged an intermediate exchange")

    monkeypatch.setattr(SourceSession, "stage_derived", forbidden)
    monkeypatch.setattr(SourceSession, "stage_calculated", forbidden)
    monkeypatch.setattr(adapters, "_inline_exchange", forbidden)


@pytest.mark.runtime
def test_native_sum_reads_only_terminal_exchange_with_all_checks_and_fresh_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        logical = _sum(work)
        mandatory = {
            (node.identity, obligation.check_id, obligation.before)
            for node in topology(logical._node.definition)
            if isinstance(node, MethodNode)
            for obligation in node.derivation.obligations
        }
        assert not mandatory
        purposes: list[str] = []
        proofs: list[tuple[CompletedCheck, ...]] = []
        batches = SourceSession.batches
        ordered = native._ordered_checks

        def read(
            source: SourceSession, issued: CompiledRead, *, chunk_size: int
        ) -> SourceBatchStream:
            purposes.append(issued.purpose)
            return batches(source, issued, chunk_size=chunk_size)

        def completed(
            actual: list[CompletedCheck], pending: tuple[CheckRequirement, ...]
        ) -> tuple[CompletedCheck, ...]:
            assert {
                (item.node_id, item.obligation.check_id, item.obligation.before) for item in pending
            } == mandatory
            result = ordered(actual, pending)
            assert {item.requirement for item in result} == set(pending)
            proofs.append(result)
            return result

        monkeypatch.setattr(SourceSession, "batches", read)
        monkeypatch.setattr(native, "_ordered_checks", completed)
        _forbid_staging(monkeypatch)
        results = []
        for _ in range(2):
            before = len(purposes)
            result = logical.execute()
            _scalar_parts(result, 529, 61)
            submitted = purposes[before:]
            assert submitted.count("analysis.graph.stage") == 1
            assert "analysis.graph.check" not in submitted
            assert "analysis.domain.prepare" not in submitted
            results.append(result)
        first, second = results
        assert len(proofs) == 2
        assert first.state.artifact_ref != second.state.artifact_ref
        assert first.state.producing_run_ref != second.state.producing_run_ref
        assert len(work.session.runs().items) == 2
        assert work.session._runtime.store.resources(work.session.id) == ()


@pytest.mark.runtime
def test_native_terminal_arrow_preserves_full_int64_keys_and_null_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        _forbid_staging(monkeypatch)
        result = _values(work).execute()
        assert result._dataset is not None
        checked = result._dataset.verified()
        assert checked.primary is not None
        keys = checked.contract.key_fields
        assert keys == ("key_0", "key_1", "key_2")
        assert [checked.primary.schema.field(key).type for key in keys] == [
            pa.string(),
            pa.int64(),
            pa.int64(),
        ]
        actual = {
            tuple(row[key] for key in keys): (row["value"], row["cell_tag"], row["cell_reason"])
            for row in cell_rows(checked.primary)
        }
        expected = {
            (row["tenant"], row["id"], row["revision"]): (
                row["amount"] if row["amount"] is not None else 0,
                "defined",
                None,
            )
            for row in work.rows
        }
        assert actual == expected and len(actual) == 64
        parts = {part.role: part.table for part in checked.parts}
        support = {
            tuple(row[key] for key in keys): row["original_state__non_null_count"]
            for row in parts["original_state"].to_pylist()
        }
        assert support == {
            (row["tenant"], row["id"], row["revision"]): int(row["amount"] is not None)
            for row in work.rows
        }
        assert all(row["coverage__complete"] is True for row in parts["coverage"].to_pylist())
        assert work.session._runtime.store.resources(work.session.id) == ()


@pytest.mark.runtime
@pytest.mark.parametrize("empty", (False, True))
def test_native_sum_preserves_known_zero_for_null_and_empty_inputs(
    empty: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("sqlite", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        with sqlite3.connect(tmp_path / "r96.sqlite") as connection:
            connection.execute(
                "DELETE FROM r96_facts" if empty else "UPDATE r96_facts SET amount=NULL"
            )
        _forbid_staging(monkeypatch)
        _scalar_parts(_sum(work).execute(), 0, 0)
        assert work.session._runtime.store.resources(work.session.id) == ()


@pytest.mark.runtime
@pytest.mark.parametrize("defect", ("schema", "checks"))
def test_native_late_invalid_exchange_or_missing_check_proof_never_publishes(
    defect: Literal["schema", "checks"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        logical = (
            work.session.members(ms.ref.entity("cost.facts")).read(
                ms.ref.dimension("cost.subjects.sid"), via=ms.ref.relationship("cost.facts_subject")
            )
            if defect == "checks"
            else _sum(work)
        )
        before = _publications(work.session)
        original = native._read
        corrupted: list[str] = []

        def read(
            source: SourceSession,
            lowered: LoweredPlan,
            expression: ir.Table,
            *,
            purpose: str,
            replacements: dict[ops.Node, ops.Node],
            keys: tuple[str, ...] = (),
            validate_cells: bool = True,
            cell_reasons: tuple[tuple[str, tuple[str, ...]], ...] = (),
        ) -> pa.Table:
            table = original(
                source,
                lowered,
                expression,
                purpose=purpose,
                replacements=replacements,
                keys=keys,
                validate_cells=validate_cells,
                cell_reasons=cell_reasons,
            )
            if purpose == "analysis.graph.stage" and defect != "checks":
                corrupted.append("schema")
                return table.drop("value")
            return table

        def missing_proofs(
            actual: list[CompletedCheck], pending: tuple[CheckRequirement, ...]
        ) -> tuple[CompletedCheck, ...]:
            assert actual and pending
            corrupted.append("checks")
            return ()

        monkeypatch.setattr(native, "_read", read)
        if defect == "checks":
            monkeypatch.setattr(native, "_ordered_checks", missing_proofs)
        _forbid_staging(monkeypatch)
        with pytest.raises(MaterializationError):
            logical.execute()
        assert len(corrupted) == 1
        assert _publications(work.session) == before
        runs = work.session.runs().items
        assert len(runs) == 1 and isinstance(runs[0], FailedRun)
        assert work.session._runtime.store.resources(work.session.id) == ()


@pytest.mark.runtime
def test_native_checks_and_computation_may_observe_different_live_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("sqlite", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        logical = work.session.members(ms.ref.entity("cost.facts")).read(
            ms.ref.dimension("cost.subjects.sid"), via=ms.ref.relationship("cost.facts_subject")
        )
        original = SourceSession.batches
        purposes: list[str] = []
        changed: list[bool] = []

        def read(
            source: SourceSession, issued: CompiledRead, *, chunk_size: int
        ) -> SourceBatchStream:
            if issued.purpose == "analysis.graph.stage":
                assert "analysis.graph.check" in purposes
                with sqlite3.connect(tmp_path / "r96.sqlite") as connection:
                    connection.execute(
                        "DELETE FROM r96_subjects WHERE sid=1",
                    )
                changed.append(True)
            purposes.append(issued.purpose)
            return original(source, issued, chunk_size=chunk_size)

        monkeypatch.setattr(SourceSession, "batches", read)
        _forbid_staging(monkeypatch)
        result = logical.execute()
        assert changed == [True]
        assert result._dataset is not None
        assert None in result._dataset.verified().primary["value"].to_pylist()
        assert purposes.count("analysis.graph.stage") == 1
        assert work.session.get_run(result.state.producing_run_ref).lifecycle == "succeeded"
        assert work.session._runtime.store.resources(work.session.id) == ()


@pytest.mark.runtime
def test_source_local_comparison_keeps_its_existing_staged_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    with source_case("duckdb", "table", tmp_path, monkeypatch, reference_data("duckdb")) as case:
        author_source_project("duckdb", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r96-hybrid", report_timezone="UTC")
        values = session.members(ms.ref.entity("sales.facts")).observe(
            ms.ref.metric("sales.total"), by=(ms.ref.entity("sales.facts"),)
        )
        assert isinstance(values, mv.LogicalNumericRelation)
        derived, calculated = SourceSession.stage_derived, SourceSession.stage_calculated
        calls: list[str] = []

        def capture(source: SourceSession, read: CompiledRead) -> tuple[ir.Table, pa.Table]:
            calls.append("derived")
            return derived(source, read)

        def stage(source: SourceSession, read: CompiledRead, table: pa.Table) -> ir.Table:
            calls.append("calculated")
            return calculated(source, read, table)

        monkeypatch.setattr(SourceSession, "stage_derived", capture)
        monkeypatch.setattr(SourceSession, "stage_calculated", stage)
        result = values.ratio(values).execute()
        frame = result.to_pandas().set_index(["member", "coord_0", "coord_1"])
        assert frame.value.dropna().to_dict() == {
            ("a", 9007199254740992, 1): 1,
            ("b", 9007199254740993, 1): 1,
        }
        assert frame.cell_tag.to_dict() == {
            ("a", 9007199254740992, 1): "defined",
            ("b", 9007199254740993, 1): "defined",
            ("a", 9007199254740993, 2): "undefined",
        }
        assert frame.cell_reason.dropna().to_dict() == {
            ("a", 9007199254740993, 2): "zero_denominator"
        }
        assert calls.count("derived") > 0
        assert calls.count("calculated") > calls.count("derived")
        assert session._runtime.store.resources(session.id) == ()


def test_final_exchange_owner_distinguishes_nullable_attribution_axes_from_identity_keys() -> None:
    """Current public Attribution is local; this tests only the existing final key policy."""
    binding = Binding("r96", "native", "nullable", "whole")
    primary = pa.table(
        {
            "key_0": pa.array([None, "a"], type=pa.string()),
            "value": pa.array([2, 3], type=pa.int64()),
            "cell_tag": ["defined", "defined"],
            "cell_reason": pa.array([None, None], type=pa.string()),
        }
    )
    coordinates: tuple[tuple[str, Literal["group", "identity"]], ...] = (
        ("attribution:axis:tenant", "group"),
        ("tenant", "identity"),
    )
    for field, role in coordinates:
        key = (Coordinate(ms.ref.entity("cost.facts"), field, role),)
        domain = DomainSignature(binding, "group" if role == "group" else "entity", key, key, field)
        signature = Signature(
            domain, DerivedQuantity("input", "test@v1", ("raw",), None, "none", "input_owned")
        )
        contract = ExchangeContract(
            signature, MethodKey("parts_transport"), "nullable", primary.schema, ("key_0",)
        )
        if role == "group":
            result = from_arrow(primary, contract)
            assert logical_table(result.primary).equals(primary)
        else:
            with pytest.raises(MaterializationError, match="null or duplicate complete key"):
                from_arrow(primary, contract)
