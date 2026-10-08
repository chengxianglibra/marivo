"""Declaration trust, exact invocation premises and actual check scheduling."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.compiler.graph_lowering import SemanticCheck
from marivo.analysis.core.model import available_facts
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import graph_source_execution as native
from marivo.analysis.materialization.errors import MaterializationError
from marivo.datasource.adapters import CompiledRead, SourceBatchStream, SourceSession
from tests.analysis.graph.physical_workloads import workload


def _reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    purposes: list[str] = []
    original = SourceSession.batches

    def read(source: SourceSession, issued: CompiledRead, *, chunk_size: int) -> SourceBatchStream:
        purposes.append(issued.purpose)
        return original(source, issued, chunk_size=chunk_size)

    monkeypatch.setattr(SourceSession, "batches", read)
    return purposes


@pytest.mark.runtime
@pytest.mark.parametrize("operation", ["read", "rollup"])
def test_declared_native_inputs_need_only_one_terminal_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: Literal["read", "rollup"]
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        members = work.session.members(ms.ref.entity("cost.facts"))
        logical = (
            members.read(ms.ref.measure("cost.facts.amount"))
            if operation == "read"
            else members.observe(
                ms.ref.metric("cost.facts_total"),
                during=mv.time_scope(start="2026-08-01", end="2026-08-02"),
                by=(ms.ref.entity("cost.facts"),),
            ).rollup()
        )
        purposes = _reads(monkeypatch)
        from marivo.analysis.materialization import graph_exchange

        def forbidden_audit(*args: object, **kwargs: object) -> None:
            pytest.fail("an owned producer result was subjected to a repeated business audit")

        monkeypatch.setattr(graph_exchange, "collect", forbidden_audit)
        result = logical.execute()
        assert purposes == ["analysis.graph.stage"]
        assert result._dataset is not None
        exchange = result._dataset.verified()
        rows = exchange.primary.to_pylist()
        if operation == "read":
            actual = {
                tuple(row[key] for key in exchange.contract.key_fields): row["value"]
                for row in rows
            }
            expected = {
                (row["tenant"], row["id"], row["revision"]): row["amount"] for row in work.rows
            }
            assert actual == expected
        else:
            assert rows == [
                {
                    "value": sum(
                        row["amount"] for row in work.rows if isinstance(row["amount"], int)
                    ),
                    "cell_tag": "defined",
                    "cell_reason": None,
                }
            ]
        assert exchange.completed_checks == ()


@pytest.mark.runtime
def test_owner_matching_is_checked_or_assumed_without_rechecking_cardinality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("sqlite", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        members = work.session.members(ms.ref.entity("cost.facts"))
        field = ms.ref.dimension("cost.subjects.sid")
        via = ms.ref.relationship("cost.facts_subject")
        purposes = _reads(monkeypatch)
        checked = members.read(field, via=via).execute()
        assert purposes.count("analysis.graph.check") == 1
        assert checked._dataset is not None
        proofs = checked._dataset.artifact.descriptor.completed_checks
        assert len(proofs) == 1 and proofs[0].fact.kind == "mapping_total"
        purposes.clear()
        assumed = members.read(field, via=via, match_verification="assume")
        assumed_result = assumed.execute()
        assert assumed_result._dataset is not None
        assert assumed_result._dataset.artifact.descriptor.execution_key_digest != (
            checked._dataset.artifact.descriptor.execution_key_digest
        )
        restored = work.session.artifact(assumed_result.state.artifact_ref)
        assert any(item.basis == "assumption" for item in restored._node.root.signature.evidence)
        assert purposes == ["analysis.graph.stage"]
        assert any(item.basis == "assumption" for item in assumed._node.root.signature.evidence)
        with sqlite3.connect(tmp_path / "r96.sqlite") as connection:
            connection.execute("DELETE FROM r96_subjects WHERE sid=1")
        with pytest.raises(MaterializationError, match="mapping_total"):
            members.read(field, via=via).execute()


@pytest.mark.runtime
def test_pairing_assumption_is_exactly_bound_and_avoids_only_its_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        values = work.session.members(ms.ref.entity("cost.facts")).read(
            ms.ref.measure("cost.facts.y")
        )
        assert isinstance(values, mv.LogicalNumericRelation)
        left, right = values.where(values.value.gt(20)), values.where(values.value.gt(16))
        assert left._node.root.signature.domain == right._node.root.signature.domain
        assert left._node.root.signature.key_domain_id != right._node.root.signature.key_domain_id
        common = values.ratio(values)
        assert not any(
            item.fact.kind == "key_set_equal" for item in common._node.root.signature.obligations
        )
        with pytest.raises(MaterializationError, match="key_set_equal"):
            left.ratio(right).execute()
        assumed = left.ratio(right, pairing=mv.ExactKeys(verification="assume"))
        signature = assumed._node.root.signature
        fact = next(item.fact for item in signature.evidence if item.basis == "assumption")
        assert tuple(item.node_id for item in fact.inputs) == (
            left._node.root.identity,
            right._node.root.identity,
        )
        assert fact in available_facts(signature)
        assert any(
            fact in item.dependencies for item in signature.evidence if item.basis == "builder"
        )
        assert assumed._node.root.fingerprint != left.ratio(right)._node.root.fingerprint
        checks: list[str] = []
        original = native._check

        def check(source, lowered, requirement, replacements):
            if isinstance(requirement, SemanticCheck):
                checks.append(requirement.requirement.obligation.fact.kind)
            return original(source, lowered, requirement, replacements)

        monkeypatch.setattr(native, "_check", check)
        result = assumed.execute()
        assert "key_set_equal" not in checks
        assert result._dataset is not None
        assert all(row["value"] == 1.0 for row in result._dataset.verified().primary.to_pylist())
        first, second = left.execute(), right.execute()
        with pytest.raises(MaterializationError, match="key sets differ"):
            first.ratio(second).execute()
        fixed = first.ratio(second, pairing=mv.ExactKeys(verification="assume")).execute()
        assert fixed._dataset is not None
        assert all(row["value"] == 1.0 for row in fixed._dataset.verified().primary.to_pylist())
        with pytest.raises(MaterializationError, match="operand is missing"):
            second.ratio(first, pairing=mv.ExactKeys(verification="assume")).execute()


@pytest.mark.runtime
def test_shared_match_check_has_one_real_completion_for_its_consumers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("sqlite", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        values = work.session.members(ms.ref.entity("cost.facts")).read(
            ms.ref.dimension("cost.subjects.sid"), via=ms.ref.relationship("cost.facts_subject")
        )
        matches_checked: list[str] = []
        original = native._check

        def check(source, lowered, requirement, replacements):
            if isinstance(requirement, SemanticCheck):
                matches_checked.append(requirement.requirement.obligation.fact.kind)
            return original(source, lowered, requirement, replacements)

        monkeypatch.setattr(native, "_check", check)
        result = values.where(values.value.eq(1)).execute()
        assert matches_checked.count("mapping_total") == 1
        assert result._dataset is not None
        descriptor = result._dataset.artifact.descriptor
        matches = [
            item for item in descriptor.completed_checks if item.fact.kind == "mapping_total"
        ]
        assert len(matches) == 2
        assert len({item.result_digest for item in matches}) == 1
        assert len({item.origin_node for item in matches}) == 2
        assert not any(item.fact.kind == "key_set_equal" for item in descriptor.completed_checks)


def test_named_verification_values_are_closed() -> None:
    with pytest.raises(AnalysisError, match="check or assume"):
        mv.ExactKeys(verification="skip")


@pytest.mark.runtime
def test_assumptions_remain_assumptions_after_source_offline_cold_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import subprocess
    import sys

    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        values = work.session.members(ms.ref.entity("cost.facts")).read(
            ms.ref.measure("cost.facts.y")
        )
        assert isinstance(values, mv.LogicalNumericRelation)
        left, right = values.where(values.value.gt(20)), values.where(values.value.gt(16))
        saved = left.ratio(right, pairing=mv.ExactKeys(verification="assume")).execute()
        assert saved._dataset is not None
        descriptor = saved._dataset.artifact.descriptor
        assert not any(item.fact.kind == "key_set_equal" for item in descriptor.completed_checks)
        assert "premise_assumptions" in dict(saved.contract()._facts)
        expected = saved._dataset.verified().primary.num_rows
        (tmp_path / "models").rename(tmp_path / "models.offline")
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[3]))
        worker = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import sys
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.analysis.materialization import graph_exchange

def forbidden(*args, **kwargs):
    raise AssertionError("cold fixed continuation must not load or read sources")
ms.load = forbidden
SourceSession.bind = forbidden
SourceSession.batches = forbidden
graph_exchange.collect = forbidden
session = mv.session.resume(sys.argv[1])
saved = session.artifact(sys.argv[2])
assert isinstance(saved, mv.MaterializedNumericRelation)
evidence = saved._node.root.signature.evidence
assumptions = [item.fact for item in evidence if item.basis == "assumption"]
assert assumptions and all(item.kind == "key_set_equal" for item in assumptions)
assert not any(item.basis == "check" and item.fact in assumptions for item in evidence)
result = saved.summarize(mv.sum()).execute()
assert result._dataset.verified().primary["value"].to_pylist() == [float(sys.argv[3])]
""",
                work.session.id,
                str(saved.state.artifact_ref),
                str(expected),
            ],
            cwd=tmp_path,
            env=env,
            text=True,
            capture_output=True,
            timeout=60,
        )
        assert worker.returncode == 0, worker.stderr


def test_composite_facts_bind_ordered_objects_and_scope() -> None:
    from dataclasses import replace

    from marivo.analysis.core.model import Binding, FactInput
    from marivo.analysis.core.rules import _fact
    from tests.analysis.graph.test_analysis_graph import _source

    left, right = _source(), _source()
    binding = left.signature.domain.binding
    fact = _fact("key_set_equal", binding, "pair", (left.signature, right.signature))
    assert fact.inputs == tuple(
        FactInput(item.signature.domain, item.signature.quantity, item.identity)
        for item in (left, right)
    )
    variants = [
        (right.signature, left.signature),
        (left.signature, replace(right.signature, node_id="other-object")),
        (
            left.signature,
            replace(
                right.signature, quantity=replace(right.signature.quantity, time_scope="october")
            ),
        ),
        (
            left.signature,
            replace(
                right.signature, domain=replace(right.signature.domain, definition_id="other-path")
            ),
        ),
        (
            left.signature,
            replace(
                right.signature,
                domain=replace(
                    right.signature.domain, binding=Binding("r33", "sales", "orders", "october")
                ),
            ),
        ),
    ]
    assert all(_fact("key_set_equal", binding, "pair", pair) != fact for pair in variants)
    assert (
        _fact("key_set_equal", binding, "other-version-or-path", (left.signature, right.signature))
        != fact
    )


def test_required_index_conflicts_fail_during_consumption() -> None:
    import pyarrow as pa

    from marivo.analysis.materialization.graph_local_execution import _index_rows

    # This is an already fetched trusted producer result, not a source audit.
    table = pa.table({"key": [1, 1], "value": [12, 15]})
    with pytest.raises(
        MaterializationError, match="duplicate complete keys during index insertion"
    ):
        _index_rows(table, ("key",))
