"""Declaration trust, exact invocation premises and actual check scheduling."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.compiler.graph_lowering import SemanticCheck
from marivo.analysis.core.graph import MethodNode, SourceLeaf
from marivo.analysis.core.model import available_facts
from marivo.analysis.core.rules import derive
from marivo.analysis.materialization import graph_source_execution as native
from marivo.analysis.materialization.errors import MaterializationError
from marivo.datasource.adapters import CompiledRead, SourceSession
from tests.analysis.graph.physical_workloads import workload
from tests.shared_fixtures import DslCaseFactory


def _reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    purposes: list[str] = []
    original = SourceSession.execute

    def read(source: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        purposes.append(issued.purpose)
        return original(source, issued)

    monkeypatch.setattr(SourceSession, "execute", read)
    return purposes


def test_observation_completeness_depends_on_exact_matching_assumptions(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    values = case.session.members(ms.ref.entity("sales.customer")).observe(
        ms.ref.metric("sales.line_revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=mv.path(
            ms.ref.relationship("sales.line_order"), ms.ref.relationship("sales.order_buyer")
        ),
        by=(mv.member(),),
    )
    signature = values._node.root.signature
    mappings = tuple(
        item.fact
        for item in signature.evidence
        if item.basis == "assumption" and item.fact.kind == "mapping_total"
    )
    completeness = tuple(
        item
        for item in signature.evidence
        if item.fact.kind in ("contribution_partition", "complete_coverage")
    )
    assert mappings and len(completeness) == 2
    assert all(set(mappings) <= set(item.dependencies) for item in completeness)
    assert all(item.fact in available_facts(signature) for item in completeness)
    rolled = values.rollup()._node.root
    assert isinstance(rolled, MethodNode)
    for mapping in mappings:
        stripped = replace(
            signature, evidence=tuple(item for item in signature.evidence if item.fact != mapping)
        )
        assert all(item.fact not in available_facts(stripped) for item in completeness)
        derivation = derive((stripped,), rolled.parameters)
        assert {item.fact.kind for item in derivation.obligations} >= {
            "contribution_partition",
            "complete_coverage",
        }


def test_related_field_null_is_counted_and_missing_owner_is_rejected(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as database:
        database.execute("UPDATE customer SET region=NULL WHERE customer_id='B'")
    values = case.session.members(ms.ref.entity("sales.order")).read(
        ms.ref.dimension("sales.customer.region"), via=ms.ref.relationship("sales.order_buyer")
    )
    purposes = _reads(monkeypatch)
    result = values.execute()
    assert result._dataset is not None
    records = result._dataset.verified().primary.to_dict("records")
    assert any(row["value"] is not None and row["cell_tag"] == "defined" for row in records)
    nulls = [row for row in records if pd.isna(row["value"])]
    assert nulls and all(
        row["cell_tag"] == "null" and row["cell_reason"] == "source_null" for row in nulls
    )
    assert result._dataset.verified().completed_checks == ()
    assert purposes == ["analysis.graph.check", "analysis.graph.stage"]
    purposes.clear()
    assert values.aggregate(mv.count()).execute().to_pandas().value.tolist() == [len(records)]
    assert purposes == [
        "analysis.graph.check",
        "analysis.graph.stage",
        "analysis.graph.stage",
    ]
    with duckdb.connect(str(case.database_path)) as database:
        database.execute("DELETE FROM customer WHERE customer_id='A'")
    checks: list[str] = []
    original = native._check

    def check(source, lowered, requirement, replacements):
        if isinstance(requirement, SemanticCheck):
            checks.append(requirement.requirement.obligation.fact.kind)
        return original(source, lowered, requirement, replacements)

    monkeypatch.setattr(native, "_check", check)
    purposes.clear()
    with pytest.raises(MaterializationError, match="required field owner operand is missing"):
        values.where(values.value.eq("east")).execute()
    assert "mapping_total" not in checks
    # The owner lookup rejects before a value predicate can hide the missing row.
    assert purposes == ["analysis.graph.check", "analysis.graph.stage"]


def test_declared_native_inputs_need_only_one_terminal_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        members = work.session.members(ms.ref.entity("cost.facts"))
        logical = members.observe(
            ms.ref.metric("cost.facts_total"),
            during=mv.time_scope(start="2026-08-01", end="2026-08-02"),
            by=(mv.member(),),
        ).rollup()
        purposes = _reads(monkeypatch)
        from marivo.analysis.materialization import graph_exchange

        def forbidden_audit(*args: object, **kwargs: object) -> None:
            pytest.fail("an owned producer result was subjected to a repeated business audit")

        monkeypatch.setattr(graph_exchange, "collect", forbidden_audit)
        result = logical.execute()
        # Physical non-null identity admission is separate from semantic premises.
        assert purposes == ["analysis.graph.check", "analysis.graph.stage"]
        assert result._dataset is not None
        exchange = result._dataset.verified()
        rows = exchange.primary.to_dict("records")
        assert rows == [
            {
                "value": sum(row["amount"] for row in work.rows if isinstance(row["amount"], int)),
                "cell_tag": "defined",
                "cell_reason": None,
            }
        ]
        assert exchange.completed_checks == ()


def test_checked_pairing_checks_unknown_images_and_reuses_proven_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        values = work.session.members(ms.ref.entity("cost.facts")).read(
            ms.ref.measure("cost.facts.y")
        )
        assert isinstance(values, mv.LogicalNumericRelation)
        left = values.where(values.value.gt(20))
        unequal = values.where(values.value.gt(16))
        matching = values.where(values.value.gt(20))
        common = values.ratio(values)
        assert not any(
            item.fact.kind == "key_set_equal" for item in common._node.root.signature.obligations
        )
        checked = left.ratio(matching, align=mv.exact())
        assert checked._node.root.fingerprint == left.ratio(matching)._node.root.fingerprint
        assert any(
            item.fact.kind == "key_set_equal" for item in checked._node.root.signature.obligations
        )
        assert not any(
            item.basis == "assumption" and item.fact.kind == "key_set_equal"
            for item in checked._node.root.signature.evidence
        )
        with pytest.raises(MaterializationError, match="key_set_equal"):
            left.ratio(unequal).execute()
        result = checked.execute()
        assert result._dataset is not None
        assert any(
            item.fact.kind == "key_set_equal"
            for item in result._dataset.artifact.descriptor.completed_checks
        )
        assert all(
            row["value"] == 1.0 for row in result._dataset.verified().primary.to_dict("records")
        )
        first, second, wrong = left.execute(), matching.execute(), unequal.execute()
        fixed = first.ratio(second, align=mv.exact()).execute()
        assert fixed._dataset is not None
        assert all(
            row["value"] == 1.0 for row in fixed._dataset.verified().primary.to_dict("records")
        )
        with pytest.raises(MaterializationError, match="key sets differ"):
            first.ratio(wrong).execute()


def test_shared_matching_assumption_never_becomes_completed_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        members = work.session.members(ms.ref.entity("cost.facts"))
        field = ms.ref.dimension("cost.subjects.sid")
        via = ms.ref.relationship("cost.facts_subject")
        values = members.read(field, via=via)
        purposes = _reads(monkeypatch)
        trusted = values.execute()
        assert trusted._dataset is not None
        assert trusted._dataset.artifact.descriptor.completed_checks == ()
        assert purposes == ["analysis.graph.check", "analysis.graph.stage"]
        assert any(
            item.basis == "assumption" and item.fact.kind == "mapping_total"
            for item in values._node.root.signature.evidence
        )
        purposes.clear()
        result = values.where(values.value.eq(1)).execute()
        assert result._dataset is not None
        assert purposes.count("analysis.graph.check") == 2
        descriptor = result._dataset.artifact.descriptor
        assert not any(item.fact.kind == "mapping_total" for item in descriptor.completed_checks)
        for relation in (values, work.session.artifact(result.state.artifact_ref)):
            assert any(
                item.basis == "assumption" and item.fact.kind == "mapping_total"
                for item in relation._node.root.signature.evidence
            )
        with pytest.raises(TypeError, match="match_verification"):
            members.read(field, via=via, **{"match_verification": "check"})


def test_exact_has_no_public_verification_choice() -> None:
    from inspect import signature

    assert tuple(signature(mv.exact).parameters) == ("time",)
    assert not hasattr(mv.exact(), "verification")


@pytest.mark.slow
def test_checked_pairing_survives_source_offline_cold_recovery(
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
        left, right = values.where(values.value.gt(20)), values.where(values.value.gt(20))
        saved = left.ratio(right, align=mv.exact()).execute()
        assert saved._dataset is not None
        descriptor = saved._dataset.artifact.descriptor
        assert any(item.fact.kind == "key_set_equal" for item in descriptor.completed_checks)
        expected = len(saved._dataset.verified().primary)
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
SourceSession.execute = forbidden
graph_exchange.collect = forbidden
session = mv.session.resume(sys.argv[1])
saved = session.artifact(sys.argv[2])
assert isinstance(saved, mv.MaterializedNumericRelation)
evidence = saved._node.root.signature.evidence
assert not any(item.basis == "assumption" and item.fact.kind == "key_set_equal" for item in evidence)
assert any(item.fact.kind == "key_set_equal" for item in saved._dataset.artifact.descriptor.completed_checks)
result = saved.aggregate(mv.sum()).execute()
assert result._dataset.verified().primary["value"].tolist() == [float(sys.argv[3])]
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


def _cohort_premise_node() -> MethodNode:
    from dataclasses import replace

    from marivo.analysis.core.graph import Edge, method_node
    from marivo.analysis.core.model import SubjectPart
    from marivo.analysis.core.predicates import ValuePredicate
    from marivo.analysis.core.rules import PartsTransport
    from marivo.analysis.methods.physical import ScalarType
    from tests.analysis.graph.test_analysis_graph import _source

    inputs: list[SourceLeaf] = []
    for index in range(3):
        source = _source()
        domain = source.signature.domain
        subject = SubjectPart(
            domain.binding,
            domain.instance_key[0].entity_ref,
            domain.instance_key,
            domain.instance_key,
            True,
            True,
            "v1",
        )
        inputs.append(
            replace(
                source,
                definition=replace(source.definition, ref=domain.instance_key[0].entity_ref)
                if index == 0
                else source.definition,
                signature=replace(
                    source.signature,
                    quantity=None if index == 0 else source.signature.quantity,
                    parts=(subject,),
                ),
            )
        )
    target = inputs[0].signature.domain
    return method_node(
        tuple(
            Edge("subject" if index == 0 else "quantity", node) for index, node in enumerate(inputs)
        ),
        PartsTransport(
            "cohort",
            target,
            ("subject",),
            False,
            predicates=(
                ValuePredicate(target.binding, "gt", 0, input_index=1),
                ValuePredicate(target.binding, "lt", 10, input_index=2),
            ),
            cohort_rule="any",
            opportunity_domain=inputs[1].signature.domain,
        ),
        value_type=ScalarType("int64"),
    )


def test_fact_inputs_resolve_each_cohort_premise_operands() -> None:
    from marivo.analysis.compiler.graph_lowering import _fact_inputs

    node = _cohort_premise_node()
    checks = {item.check_id: item for item in node.derivation.obligations}
    identities = tuple(edge.node.identity for edge in node.inputs)
    assert _fact_inputs(node, checks["source.complete_coverage@v1"]) == (identities[:2],)
    assert _fact_inputs(node, checks["source.exact_pairing@v1"]) == (identities[1:],)


def test_fact_inputs_preserve_recorded_order_and_repeated_operands() -> None:
    from dataclasses import replace

    from marivo.analysis.compiler.graph_lowering import _fact_inputs
    from marivo.analysis.core.model import FactInput

    node = _cohort_premise_node()
    obligation = next(
        item
        for item in node.derivation.obligations
        if item.check_id == "source.complete_coverage@v1"
    )
    for positions in ((0, 1, 2), (2, 1), (1, 1)):
        operands = tuple(node.inputs[index].node for index in positions)
        fact = replace(
            obligation.fact,
            inputs=tuple(
                FactInput(item.signature.domain, item.signature.quantity, item.identity)
                for item in operands
            ),
        )
        # Isolate the resolver contract from the semantic owner's operand choice.
        object.__setattr__(
            node, "derivation", replace(node.derivation, pre=(*node.derivation.pre, fact))
        )
        assert _fact_inputs(node, replace(obligation, fact=fact)) == (
            tuple(item.identity for item in operands),
        )


def test_fact_inputs_reject_mismatched_direct_operands() -> None:
    from dataclasses import replace

    from marivo.analysis.compiler.graph_lowering import _fact_inputs
    from marivo.analysis.core.model import CoreRuleError

    node = _cohort_premise_node()
    obligation = next(
        item
        for item in node.derivation.obligations
        if item.check_id == "source.complete_coverage@v1"
    )
    target, opportunity = obligation.fact.inputs
    for change in ("node", "domain", "quantity"):
        if change == "node":
            invalid = replace(opportunity, node_id="foreign-node")
        elif change == "domain":
            invalid = replace(
                opportunity, domain=replace(opportunity.domain, definition_id="foreign-domain")
            )
        else:
            invalid = replace(opportunity, quantity=None)
        fact = replace(obligation.fact, inputs=(target, invalid))
        object.__setattr__(
            node, "derivation", replace(node.derivation, pre=(*node.derivation.pre, fact))
        )
        with pytest.raises(CoreRuleError, match="originating graph inputs"):
            _fact_inputs(node, replace(obligation, fact=fact))


def test_required_index_conflicts_fail_during_consumption() -> None:
    import pyarrow as pa

    from marivo.analysis.materialization.graph_local_execution import _index_rows

    # This is an already fetched trusted producer result, not a source audit.
    table = pa.table({"key": [1, 1], "value": [12, 15]})
    with pytest.raises(
        MaterializationError, match="duplicate complete keys during index insertion"
    ):
        _index_rows(table, ("key",))
