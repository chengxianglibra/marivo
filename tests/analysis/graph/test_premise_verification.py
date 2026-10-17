"""Exact invocation premises, complete pairing and source-owned checks."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.graph import MethodNode, SourceLeaf
from marivo.analysis.core.model import ObservedQuantity
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.errors import MaterializationError
from marivo.datasource.adapters import CompiledRead, SourceSession
from tests.analysis.graph.physical_workloads import workload
from tests.shared_fixtures import DslCaseFactory


def _reads(monkeypatch: pytest.MonkeyPatch) -> list[CompiledRead]:
    reads: list[CompiledRead] = []
    execute = SourceSession.execute

    def read(source: SourceSession, issued: CompiledRead) -> pd.DataFrame:
        reads.append(issued)
        return execute(source, issued)

    monkeypatch.setattr(SourceSession, "execute", read)
    return reads


def test_observation_completeness_depends_on_exact_matching_assumptions(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as database:
        database.execute(
            "INSERT INTO order_line VALUES ('a1','j1_a',100),('a2','j1_a',200),('b1','j1_b',300),('c1','j1_c',400)"
        )
    session = case.session
    members = session.members(ms.ref.entity("sales.customer"))
    values = session.observe(
        {"value": session.catalog.metrics.get("sales.line_revenue")},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=members,
        members=members,
    )
    frame = values.execute().to_pandas()
    assert dict(zip(frame.iloc[:, 0], frame[("value", "value")], strict=True)) == {
        "A": 300,
        "B": 300,
        "C": 400,
        "D": 0,
    }
    assert values.rollup().execute().to_pandas()[("value", "value")].tolist() == [1000]
    route = session.catalog.relationships.get("sales.order_buyer")
    region = session.catalog.fields.get("sales.customer.region")
    orders = session.members(ms.ref.entity("sales.order"))
    inferred = session.read(orders, {"region": region}).execute().to_pandas()
    explicit = session.read(orders, {"region": region.via((route,))}).execute().to_pandas()
    assert inferred.equals(explicit)


def test_related_field_null_is_counted_and_missing_owner_is_rejected(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as database:
        database.execute("UPDATE customer SET region=NULL WHERE customer_id='B'")
    session = case.session
    orders = session.members(ms.ref.entity("sales.order"))
    region = session.catalog.fields.get("sales.customer.region")
    buyer = session.catalog.relationships.get("sales.order_buyer")
    values = session.read(orders, {"value": region.via((buyer,))})
    reads = _reads(monkeypatch)
    result = values.execute()
    frame = result.to_pandas()
    assert frame[("value", "value")].notna().any()
    assert frame[("value", "value")].isna().any()
    assert values.reduce({"count": mv.row_count()}).execute().to_pandas()[
        ("value", "count")
    ].tolist() == [len(frame)]
    assert reads
    with duckdb.connect(str(case.database_path)) as database:
        database.execute("DELETE FROM customer WHERE customer_id='A'")
    before = session.runs().items
    with pytest.raises(AnalysisError):
        values.where(values["value"] == "east").execute()
    assert session.runs().items != before
    assert session.artifact(result.id).to_pandas().equals(frame)


def test_declared_native_inputs_need_only_one_terminal_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        reads = _reads(monkeypatch)
        result = work.total().execute()
        assert work.validate(result)["passed"] is True
        assert sum("SUM(" in read.sql.upper() for read in reads) == 1
        before = len(reads)
        assert work.session.artifact(result.id).to_pandas().equals(result.to_pandas())
        assert len(reads) == before


def test_checked_pairing_checks_unknown_images_and_reuses_proven_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        session = work.session
        values = session.read(
            session.members(ms.ref.entity("cost.facts")),
            {"value": session.catalog.fields.get("cost.facts.y")},
        )
        left = values.where(values["value"] > 20)
        unequal = values.where(values["value"] > 16)
        matching = values.where(values["value"] > 20)
        before = session.runs().items
        paired = left.align(matching)
        assert session.runs().items == before
        with pytest.raises(AnalysisError, match="equal complete endpoint key images"):
            left.align(unequal).execute()
        ratios = paired.select({"value": paired["left.value"] / paired["right.value"]}).execute()
        expected = sum(isinstance(row["y"], int) and row["y"] > 20 for row in work.rows)
        assert ratios.to_pandas()[("value", "value")].tolist() == [1.0] * expected
        # Same-domain Columns share an input; independent Queries require actual exact pairing.
        shared = values.select({"value": values["value"] / values["value"]}).execute()
        assert shared.to_pandas()[("value", "value")].tolist() == [1.0] * 64
        assert session.artifact(ratios.id).to_pandas().equals(ratios.to_pandas())


def test_shared_matching_assumption_never_becomes_completed_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        session = work.session
        members = session.members(ms.ref.entity("cost.facts"))
        field = session.catalog.fields.get("cost.subjects.sid")
        relation = session.catalog.relationships.get("cost.facts_subject")
        values = session.read(members, {"value": field.via((relation,))})
        trusted = values.execute()
        frame = trusted.to_pandas()
        assert frame[("value", "value")].tolist() == [index % 15 for index in range(64)]
        selected = values.where(values["value"] == 1).execute()
        assert selected.to_pandas()[("value", "value")].tolist() == [1] * 5
        assert not hasattr(trusted, "_graph") and not hasattr(trusted, "_node")
        assert not hasattr(trusted, "rollup")
        assert session.artifact(trusted.id).to_pandas().equals(frame)


def test_exact_has_no_public_verification_choice() -> None:
    from inspect import signature

    assert tuple(signature(mv.Query.align).parameters) == ("self", "other", "match", "how")
    assert "verification" not in signature(mv.Query.compare).parameters


@pytest.mark.slow
def test_checked_pairing_survives_source_offline_cold_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import os
    import subprocess
    import sys

    from tests.support.paths import PROJECT_ROOT

    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        session = work.session
        values = session.read(
            session.members(ms.ref.entity("cost.facts")),
            {"value": session.catalog.fields.get("cost.facts.y")},
        )
        left, right = values.where(values["value"] > 20), values.where(values["value"] > 20)
        paired = left.align(right)
        saved = paired.select({"value": paired["left.value"] / paired["right.value"]}).execute()
        expected = len(saved.to_pandas())
        scalar = paired.reduce({"count": mv.row_count()}).execute()
        assert scalar.to_pandas()[("value", "count")].tolist() == [expected]
        (tmp_path / "models").rename(tmp_path / "models.offline")
        env = dict(os.environ, PYTHONPATH=str(PROJECT_ROOT))
        reports = []
        for _ in range(2):
            worker = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    """
import json, os, sys
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession

def forbidden(*args, **kwargs):
    raise AssertionError('terminal recovery touched a live source')
ms.load = forbidden
SourceSession.bind = forbidden
SourceSession.execute = forbidden
session = mv.session.resume(sys.argv[1], by='id')
before = len(session.runs().items)
saved = session.artifact(sys.argv[2])
frame = saved.to_pandas()
assert frame[('value','value')].tolist() == [1.0] * int(sys.argv[4])
assert frame[('value','value')].sum() == int(sys.argv[4])
assert session.artifact(sys.argv[3]).to_pandas()[('value','count')].tolist() == [int(sys.argv[4])]
assert len(session.runs().items) == before
print(json.dumps({'pid':os.getpid(),'rows':len(frame)}))
""",
                    session.id,
                    saved.id,
                    scalar.id,
                    str(expected),
                ],
                cwd=tmp_path,
                env=env,
                text=True,
                capture_output=True,
                timeout=60,
            )
            assert worker.returncode == 0, worker.stdout + worker.stderr
            reports.append(json.loads(worker.stdout.strip().splitlines()[-1]))
        assert len({os.getpid(), *(report["pid"] for report in reports)}) == 3
        assert all(report["rows"] == expected for report in reports)


def test_composite_facts_bind_ordered_objects_and_scope() -> None:

    from marivo.analysis.core.model import Binding, FactInput
    from marivo.analysis.core.rules import _fact
    from tests.analysis.graph.test_analysis_graph import _source

    left, right = _source(), _source()
    assert isinstance(right.signature.quantity, ObservedQuantity)
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
