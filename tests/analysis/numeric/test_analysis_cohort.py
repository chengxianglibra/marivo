"""Full opportunity cohort decisions with independent counts."""

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis._cohort import decide
from marivo.analysis.materialization.cell_arrow import rows as cell_rows
from tests.shared_fixtures import DslCaseFactory, analysis_dsl_rows, export_dsl_parquet_models
from tests.support.paths import PROJECT_ROOT


@pytest.mark.parametrize(
    "rule,k,t,u,f,expected",
    [
        ("at_least", 3, 3, 1, 0, True),
        ("at_least", 3, 1, 1, 2, False),
        ("at_least", 3, 2, 1, 1, None),
        ("any", 1, 1, 1, 0, True),
        ("any", 1, 0, 1, 1, None),
        ("all", 1, 1, 1, 1, False),
        ("all", 1, 2, 0, 0, True),
    ],
)
def test_quantifier_decisions(rule, k, t, u, f, expected) -> None:
    assert decide(rule, k, "false", t, u, f) is expected


@pytest.mark.parametrize("empty,expected", [("true", True), ("false", False), ("undefined", None)])
def test_explicit_empty_policy(empty, expected) -> None:
    assert decide("all", 1, empty, 0, 0, 0) is expected
    assert decide("any", 1, empty, 0, 0, 0) is False
    assert decide("at_least", 3, empty, 0, 0, 0) is False


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
@pytest.mark.parametrize("event_kind", ["utc", "date", "aware_local"])
def test_full_entity_time_cohort(
    analysis_dsl_case_factory: DslCaseFactory,
    parquet: bool,
    event_kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j2")
    n = case.names
    if event_kind == "date":
        import duckdb

        model = case.root / "models/semantic/sales/models.py"
        model.write_text(
            model.read_text().replace("parse=ms.timestamp(timezone='UTC')", "parse=None")
        )
        with duckdb.connect(str(case.database_path)) as db:
            db.execute('ALTER TABLE "order" ALTER ordered_at TYPE DATE')
        ms.load(workspace_dir=case.root)
    if parquet:
        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    targets = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
    grid = mv.time_grid(
        during=mv.time_scope(
            start="2026-07-01" + ("T00:00:00-04:00" if event_kind == "aware_local" else ""),
            end="2026-10-01" + ("T00:00:00-04:00" if event_kind == "aware_local" else ""),
        ),
        grain=mv.grain("month"),
        timezone="America/New_York" if event_kind == "aware_local" else "UTC",
    )
    values = targets.each(grid).observe(
        ms.ref.metric(f"{n.domain}.{n.order_count}"),
        during=grid.window,
        via=ms.ref.relationship(f"{n.domain}.{n.buyer}"),
        by=(ms.ref.entity(f"{n.domain}.{n.customer}"),),
    )
    facts = analysis_dsl_rows("j2")
    months: dict[str, set[str]] = {}
    for _, member, _, _, occurred, _ in facts.orders:
        if member is not None and "2026-07" <= str(occurred)[:7] < "2026-10":
            months.setdefault(member, set()).add(str(occurred)[:7])
    expected = {member for member, active in months.items() if len(active) >= 2}
    result = targets.cohort(values.value.gt(0), rule=mv.at_least(2)).execute()
    assert set(result.to_pandas().member) == expected
    fixed_targets, fixed_values = targets.execute(), values.execute()
    from marivo.datasource.adapters import SourceSession

    def forbid_source_read(*args: object, **kwargs: object) -> None:
        pytest.fail("Fixed full-opportunity cohort must use retained artifacts only")

    with monkeypatch.context() as fixed_only:
        fixed_only.setattr(SourceSession, "batches", forbid_source_read)
        fixed = fixed_targets.cohort(fixed_values.value.gt(0), rule=mv.at_least(2)).execute()
    assert set(fixed.to_pandas().member) == expected
    with pytest.raises(Exception, match=r"opportunity|keys"):
        targets.cohort(
            values.where(values.value.gt(0)).value.gt(0), rule=mv.any_instance()
        ).execute()


@pytest.mark.runtime
def test_cold_full_opportunity_continuation(analysis_dsl_case_factory: DslCaseFactory) -> None:
    import os
    import subprocess
    import sys

    case = analysis_dsl_case_factory("j2")
    n = case.names
    targets = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-07-01", end="2026-10-01"), grain=mv.grain("month")
    )
    values = targets.each(grid).observe(
        ms.ref.metric(f"{n.domain}.{n.order_count}"),
        during=grid.window,
        via=ms.ref.relationship(f"{n.domain}.{n.buyer}"),
        by=(ms.ref.entity(f"{n.domain}.{n.customer}"),),
    )
    saved_targets, saved_values = targets.execute(), values.execute()
    result = saved_targets.cohort(saved_values.value.gt(0), rule=mv.any_instance()).execute()
    offline = case.database_path.with_suffix(".offline")
    case.database_path.rename(offline)
    script = """
import sys
import duckdb
import marivo.analysis as mv
import marivo.semantic as ms
def unavailable(*args, **kwargs):
    raise AssertionError("fixed cohort cannot reopen sources or Semantic")
duckdb.connect = unavailable
ms.load = unavailable
session = mv.session.resume(sys.argv[1], by="id")
targets, values, saved = [session.artifact(ref) for ref in sys.argv[2:]]
result = targets.cohort(values.value.gt(0), rule=mv.any_instance(), through=values.subject_binding).execute()
assert result.to_pandas().equals(saved.to_pandas())
selected = values.where(values.value.is_defined())
assert len(selected.where(selected.value.gte(0)).members().execute().to_pandas()) == len(targets.to_pandas())
assert "cohort_decision" in {part.role for part in saved._dataset.verified().parts}
"""
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                case.session.id,
                saved_targets.state.artifact_ref.ref,
                saved_values.state.artifact_ref.ref,
                result.state.artifact_ref.ref,
            ],
            cwd=case.root,
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        offline.rename(case.database_path)
    assert completed.returncode == 0, completed.stderr


@pytest.mark.runtime
def test_existing_unknown_consumption_and_decision_evidence(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    """Control Cells at the consumer boundary; this does not qualify a new producer."""
    from dataclasses import replace

    import ibis
    import pyarrow as pa

    from marivo.analysis.compiler.graph_lowering import (
        LoweredLocal,
        LoweredRelation,
        _transport,
        canonical_layout,
    )
    from marivo.analysis.compiler.graph_plan import LocalMethodStage, SourceMethodStage
    from marivo.analysis.materialization.graph_exchange import ExchangePart, from_arrow
    from marivo.analysis.materialization.graph_local_execution import _cohort_stage
    from marivo.analysis.methods.builtin import implementations
    from marivo.analysis.methods.semantics import MethodKey

    case = analysis_dsl_case_factory("j2")
    targets = case.session.members(ms.ref.entity("sales.customer"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-06-01", end="2026-10-01"), grain=mv.grain("month")
    )
    values = targets.each(grid).observe(
        ms.ref.metric("sales.order_count"),
        during=grid.window,
        via=ms.ref.relationship(f"sales.{case.names.buyer}"),
        by=(ms.ref.entity("sales.customer"),),
    )
    saved_targets, saved_values = targets.execute(), values.execute()
    target = saved_targets._dataset.verified()
    supplied = saved_values._dataset.verified()
    local_node = saved_targets.cohort(saved_values.value.gt(0), rule=mv.at_least(3))._node.root
    source_node = targets.cohort(values.value.gt(0), rule=mv.at_least(3))._node.root
    local_impl = next(
        item
        for item in implementations(MethodKey("domain.cohort"))
        if item.key.route == "artifact_python"
    )
    source_impl = next(
        item for item in implementations(MethodKey("domain.cohort")) if item.key.route == "ibis"
    )
    local_stage = LoweredLocal(
        LocalMethodStage("cohort", (), local_node, local_impl),
        (),
        canonical_layout(local_node.signature, has_value=False),
    )
    source_stage = SourceMethodStage("cohort", (), source_node, source_impl, "ibis")
    target_table = target.primary.append_column("subject__key_0", target.primary["key_0"])
    source_target = LoweredRelation(
        "target",
        targets._node.root,
        ibis.memtable(target_table),
        canonical_layout(targets._node.root.signature, has_value=False),
        (),
    )
    backend = ibis.duckdb.connect()
    try:
        for tags, payloads, expected in (
            (("defined",) * 3 + ("unknown",), (1, 1, 1, None), True),
            (("defined", "unknown", "defined", "defined"), (1, None, 0, 0), False),
            (("defined", "defined", "defined", "unknown"), (1, 1, 0, None), None),
            (("defined",) * 3 + ("undefined",), (1, 1, 1, None), None),
        ):
            rows = cell_rows(supplied.primary)
            for row in rows:
                index = [
                    item.identity for item in values._node.root.signature.domain.time_grid.cells
                ].index(row["key_1"])
                row.update(
                    value=payloads[index],
                    cell_tag=tags[index],
                    cell_reason=None if tags[index] == "defined" else "controlled_consumer",
                )
            table = pa.Table.from_pylist(rows, schema=supplied.primary.schema)
            controlled = replace(supplied, primary=table)
            source_input = LoweredRelation(
                "value",
                values._node.root,
                ibis.memtable(table),
                canonical_layout(values._node.root.signature, has_value=True),
                (),
            )
            checks = []
            retained = []
            expression, _ = _transport(
                source_stage, source_target, checks, (source_input,), retained
            )
            violations = [len(backend.to_pyarrow(check.violations)) for check in checks]
            if expected is None:
                assert any(violations)
                with pytest.raises(Exception, match=r"undecidable|Cell tag undefined"):
                    _cohort_stage(local_stage, target, (controlled,), "controlled")
                continue
            assert not any(violations)
            result = _cohort_stage(local_stage, target, (controlled,), "controlled")
            assert len(result.primary) == (len(target.primary) if expected else 0)
            assert len(backend.to_pyarrow(expression)) == len(result.primary)
            decisions = next(part.table for part in result.parts if part.role == "cohort_decision")
            assert len(decisions) == len(target.primary)
            assert set(decisions["cohort_decision__accepted"].to_pylist()) == {expected}
            corrupted = decisions.set_column(
                decisions.schema.get_field_index("cohort_decision__true_count"),
                "cohort_decision__true_count",
                pa.array([99] * len(decisions), type=pa.int64()),
            )
            with pytest.raises(Exception, match="cohort decision"):
                from_arrow(
                    result.primary,
                    result.contract,
                    parts=tuple(
                        ExchangePart(part.role, corrupted)
                        if part.role == "cohort_decision"
                        else part
                        for part in result.parts
                    ),
                    method_state=result.method_state,
                )
        other_source = values.where(values.value.is_defined())
        other_fixed = saved_values.where(saved_values.value.is_defined())
        other_rows = cell_rows(supplied.primary)
        for row in other_rows:
            row.update(value=1, cell_tag="defined", cell_reason=None)
        other_table = pa.Table.from_pylist(other_rows, schema=supplied.primary.schema)
        other_input = replace(supplied, primary=other_table)
        second = LoweredRelation(
            "other",
            other_source._node.root,
            ibis.memtable(other_table),
            canonical_layout(other_source._node.root.signature, has_value=True),
            (),
        )
        for tag in ("unknown", "undefined"):
            rows = cell_rows(supplied.primary)
            for row in rows:
                row.update(value=None, cell_tag=tag, cell_reason="controlled_consumer")
            table = pa.Table.from_pylist(rows, schema=supplied.primary.schema)
            controlled = replace(supplied, primary=table)
            first = replace(source_input, expression=ibis.memtable(table))
            for combine, right_true, expected in (
                (mv.all_of, False, False),
                (mv.any_of, True, True),
            ):
                source_predicate = combine(
                    values.value.gt(0),
                    other_source.value.gt(0) if right_true else other_source.value.lt(0),
                )
                fixed_predicate = combine(
                    saved_values.value.gt(0),
                    other_fixed.value.gt(0) if right_true else other_fixed.value.lt(0),
                )
                source_node = targets.cohort(source_predicate, rule=mv.any_instance())._node.root
                fixed_node = saved_targets.cohort(
                    fixed_predicate, rule=mv.any_instance()
                )._node.root
                checks = []
                expression, _ = _transport(
                    replace(source_stage, node=source_node),
                    source_target,
                    checks,
                    (first, second),
                    [],
                )
                violations = [len(backend.to_pyarrow(check.violations)) for check in checks]
                if tag == "undefined":
                    assert any(violations)
                    with pytest.raises(Exception, match="Cell tag undefined"):
                        _cohort_stage(
                            replace(local_stage, stage=replace(local_stage.stage, node=fixed_node)),
                            target,
                            (controlled, other_input),
                            "hard",
                        )
                else:
                    assert not any(violations)
                    result = _cohort_stage(
                        replace(local_stage, stage=replace(local_stage.stage, node=fixed_node)),
                        target,
                        (controlled, other_input),
                        "composite",
                    )
                    assert len(result.primary) == (len(target.primary) if expected else 0)
                    assert len(backend.to_pyarrow(expression)) == len(result.primary)
        incomplete = replace(controlled, primary=table.slice(1))
        with pytest.raises(Exception, match="opportunity"):
            _cohort_stage(local_stage, target, (incomplete,), "missing")
    finally:
        backend.disconnect()


@pytest.mark.runtime
def test_no_time_quantifiers_and_complete_target_decisions(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    targets = case.session.members(ms.ref.entity("sales.customer"))
    category = targets.read(ms.ref.dimension("sales.customer.region"))
    expected = {member for member, region in analysis_dsl_rows("j2").customers if region == "east"}
    for target, values in ((targets, category), (targets.execute(), category.execute())):
        for rule in (
            mv.any_instance(),
            mv.at_least(1),
            mv.all_instances(empty=mv.empty_opportunity.undefined()),
        ):
            result = target.cohort(mv.not_(mv.not_(values.value.eq("east"))), rule=rule).execute()
            assert set(result.to_pandas().member) == expected
            decisions = next(
                part.table
                for part in result._dataset.verified().parts
                if part.role == "cohort_decision"
            )
            assert len(decisions) == len(analysis_dsl_rows("j2").customers)
            assert {
                row["key_0"] for row in decisions.to_pylist() if row["cohort_decision__accepted"]
            } == expected


@pytest.mark.runtime
@pytest.mark.parametrize("damage", ["missing", "corrupt", "receipt", "version"])
def test_cohort_required_decision_damage_revokes_recovery(
    analysis_dsl_case_factory: DslCaseFactory, damage: str
) -> None:
    import json

    from marivo.analysis.errors import AnalysisError
    from marivo.analysis.materialization.graph_protocol import DESCRIPTOR, encode

    case = analysis_dsl_case_factory("j2")
    targets = case.session.members(ms.ref.entity("sales.customer"))
    category = targets.read(ms.ref.dimension("sales.customer.region"))
    saved = targets.cohort(category.value.eq("east"), rule=mv.any_instance()).execute()
    descriptor = saved._dataset.artifact.descriptor
    part = next(part for part in descriptor.parts if part.role == "cohort_decision")
    path = case.root / part.local.project_relative_path / part.local.file_manifest[0].relative_path
    if damage == "missing":
        path.unlink()
    elif damage == "corrupt":
        path.write_bytes(b"corrupt cohort decisions")
    else:
        payload = json.loads(encode(descriptor, DESCRIPTOR))
        if damage == "receipt":
            next(part for part in payload["parts"] if part["role"] == "cohort_decision")[
                "input_binding"
            ] = "foreign-cohort"
        else:
            payload["method_state"]["contract_version"] = 2
        with case.session._runtime.store._write() as connection:
            connection.execute(
                "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                (json.dumps(payload), saved.state.artifact_ref.ref),
            )
    with pytest.raises(AnalysisError):
        saved.contract()
    with pytest.raises(AnalysisError):
        case.session.artifact(saved.state.artifact_ref)
