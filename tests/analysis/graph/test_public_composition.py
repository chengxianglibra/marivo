"""Public Store 8 replacements for the retired scenario-specific v6 tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Literal

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import graph_store
from marivo.analysis.materialization.graph_protocol import SourceRunInput
from tests.shared_fixtures import DslCase, DslCaseFactory
from tests.support.paths import PROJECT_ROOT


def _ratio(case: DslCase, *, coordinates: tuple[str, ...] = ()) -> mv.LogicalRatioRelation:
    n = case.names
    return case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}")).observe(
        ms.ref.metric(f"{n.domain}.{n.aov}"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=mv.routes(
            mv.route(
                ms.ref.entity(f"{n.domain}.{n.order_line}"),
                through=(
                    ms.ref.relationship(f"{n.domain}.{n.line_order}"),
                    ms.ref.relationship(f"{n.domain}.{n.buyer}"),
                ),
            ),
            mv.route(
                ms.ref.entity(f"{n.domain}.{n.order}"),
                through=(ms.ref.relationship(f"{n.domain}.{n.buyer}"),),
            ),
        ),
        by=(
            ms.ref.entity(f"{n.domain}.{n.customer}"),
            *tuple(ms.ref.dimension(f"{n.domain}.{n.order}.{name}") for name in coordinates),
        ),
    )


def _observations(case: DslCase) -> tuple[mv.LogicalNumericRelation, mv.LogicalNumericRelation]:
    n = case.names
    members = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
    metric = ms.ref.metric(f"{n.domain}.{n.revenue}")
    via = ms.ref.relationship(f"{n.domain}.{n.buyer}")
    return (
        members.observe(
            metric,
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=via,
            by=(ms.ref.entity(f"{n.domain}.{n.customer}"),),
        ),
        members.observe(
            metric,
            during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
            via=via,
            by=(ms.ref.entity(f"{n.domain}.{n.customer}"),),
        ),
    )


@pytest.mark.runtime
def test_ratio_complete_coordinate_tuple_and_fixed_original_components(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("tuple_union")
    observed = _ratio(case, coordinates=(case.names.channel, case.names.status))
    saved = observed.execute()
    frame = saved.to_pandas()
    assert frame.set_index(["group", "coord_0", "coord_1"])["value"].to_dict() == {
        ("A", "web", "paid"): 40.0,
        ("A", "mobile", "cancelled"): 0.0,
    }
    for name in (case.names.channel, case.names.status):
        coordinate = ms.ref.dimension(f"{case.names.domain}.{case.names.order}.{name}")
        live = observed.group_by(coordinate).rollup().execute()
        fixed = saved.group_by(coordinate).rollup().execute()
        assert live.to_pandas().equals(fixed.to_pandas())
    assert saved.summarize(mv.mean()).execute().to_pandas().iloc[0]["value"] == 20


@pytest.mark.runtime
@pytest.mark.parametrize("scenario", ["j3_weighting", "empty_group", "zero_denominator"])
def test_ratio_original_state_and_empty_contribution(
    analysis_dsl_case_factory: DslCaseFactory,
    scenario: Literal["j3_weighting", "empty_group", "zero_denominator"],
) -> None:
    case = analysis_dsl_case_factory(scenario)
    relation = _ratio(case)
    fixed = relation.execute()
    if scenario == "j3_weighting":
        assert fixed.to_pandas().set_index("member")["value"].to_dict() == {"A": 1.0, "B": 100.0}
        assert fixed.summarize(mv.mean()).execute().to_pandas().iloc[0]["value"] == 50.5
        assert fixed.rollup().execute().to_pandas().iloc[0]["value"] == pytest.approx(200 / 101)
    elif scenario == "empty_group":
        assert fixed.to_pandas().iloc[0]["value"] == 0
    else:
        row = fixed.to_pandas().iloc[0]
        assert row["cell_tag"] == "undefined" and row["cell_reason"] == "zero_denominator"


@pytest.mark.runtime
@pytest.mark.parametrize("operator", ["lt", "lte", "gt", "gte", "eq"])
def test_numeric_selection_source_fixed_parity_and_ordered_endpoints(
    analysis_dsl_case_factory: DslCaseFactory,
    operator: str,
) -> None:
    case = analysis_dsl_case_factory("j2")
    current, baseline = _observations(case)
    difference = current.compare(baseline)
    fixed = current.execute().compare(baseline.execute()).execute()
    source_selection = difference.where(getattr(difference.value, operator)(0)).execute()
    fixed_selection = fixed.where(getattr(fixed.value, operator)(0)).execute()
    assert source_selection.to_pandas().equals(fixed_selection.to_pandas())
    assert fixed.to_pandas().set_index("member")["value"].to_dict() == {
        "A": -40,
        "B": 20,
        "C": -50,
        "D": 0,
    }
    reverse = baseline.compare(current).execute().to_pandas().set_index("member")["value"].to_dict()
    assert reverse == {"A": 40, "B": -20, "C": 50, "D": 0}


@pytest.mark.runtime
def test_empty_numeric_selection_has_distinct_row_policies(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    current, baseline = _observations(analysis_dsl_case_factory("j2"))
    difference = current.compare(baseline).execute()
    empty = difference.where(difference.value.lt(-1000)).execute()
    assert empty.to_pandas().empty
    assert empty.summarize(mv.sum()).execute().to_pandas().iloc[0]["value"] == 0
    assert empty.summarize(mv.count()).execute().to_pandas().iloc[0]["value"] == 0
    mean = empty.summarize(mv.mean()).execute().to_pandas().iloc[0]
    assert (mean["cell_tag"], mean["cell_reason"]) == ("undefined", "empty_mean")


@pytest.mark.runtime
@pytest.mark.parametrize("corruption", ["part", "snapshot", "state_version"])
def test_exact_artifact_corruption_blocks_recovery_and_dynamic_k(
    analysis_dsl_case_factory: DslCaseFactory,
    corruption: str,
) -> None:
    case = analysis_dsl_case_factory("j1")
    current, _ = _observations(case)
    saved = current.execute()
    store = case.session._runtime.store
    with store._read() as connection:
        record = graph_store.artifact(store, connection, saved.state.artifact_ref.ref)
    assert record is not None
    reference = record.artifact_ref
    before = len(case.session.runs().items)
    if corruption == "part":
        next(
            (case.root / record.descriptor.parts[0].local.project_relative_path).glob("*.parquet")
        ).unlink()
    else:
        with store._write() as connection:
            value = json.loads(
                connection.execute(
                    "SELECT descriptor_payload FROM dataset_artifacts WHERE artifact_ref=?",
                    (reference,),
                ).fetchone()[0]
            )
            if corruption == "snapshot":
                value["continuation_snapshot"] = "{}"
            elif corruption == "state_version":
                value["method_state"]["version"] = 999
            else:
                value["primary_receipt"]["input_binding"] = "wrong-binding"
            connection.execute(
                "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                (json.dumps(value, sort_keys=True, separators=(",", ":")), reference),
            )
    for action in (lambda: case.session.artifact(reference), saved.contract, saved.to_pandas):
        with pytest.raises(AnalysisError):
            action()
    assert len(case.session.runs().items) == before


@pytest.mark.runtime
def test_public_history_and_guarded_abandonment_use_graph_runs(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = analysis_dsl_case_factory("j1")
    current, _ = _observations(case)
    saved = current.execute()
    session, store = case.session, case.session._runtime.store
    run = session.get_run(saved.state.producing_run_ref)
    assert run.lifecycle == "succeeded"
    assert isinstance(run.dataset_input, SourceRunInput)
    assert mv.session.current().id == session.id
    assert mv.session.resume(session.id, by="id").id == session.id
    assert mv.session.inspect(session.name).runs.items[0].run_id == run.run_id
    assert session.graph().artifacts[0].artifact_ref == saved.state.artifact_ref
    session.show()
    run.show()
    with pytest.raises(AnalysisError):
        mv.session.abandon_run(session_id=session.id, run_id=run.run_id)
    graph_store.admit(store, session.id, "pending-key", run.dataset_input, "pending")
    mv.session.abandon_run(session_id=session.id, run_id="pending")
    assert session.get_run("pending").lifecycle == "failed"
    graph_store.admit(store, session.id, "other-key", run.dataset_input, "other")
    mv.session.abandon_run(session_id=session.id, run_id="pending")

    def unavailable(*args: object, **kwargs: object) -> None:
        raise RuntimeError("proof unavailable")

    monkeypatch.setattr(
        "marivo.analysis.materialization.graph_publication.discharge_resources", unavailable
    )
    with pytest.raises(RuntimeError, match="proof unavailable"):
        mv.session.abandon_run(session_id=session.id, run_id="other")
    assert session.get_run("other").lifecycle == "incomplete"


@pytest.mark.runtime
@pytest.mark.parametrize("journey", ["j2", "j3", "j4"])
def test_public_exact_cold_continuations_after_source_and_models_are_deleted(
    analysis_dsl_case_factory: DslCaseFactory,
    journey: Literal["j2", "j3", "j4"],
) -> None:
    case = analysis_dsl_case_factory(journey)
    if journey == "j3":
        saved = _ratio(case, coordinates=(case.names.channel,)).execute()
    elif journey == "j2":
        current, baseline = _observations(case)
        saved = current.compare(baseline).execute()
    else:
        n = case.names
        members = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
        window = mv.time_scope(start="2026-08-01", end="2026-09-01")
        via = ms.ref.relationship(f"{n.domain}.{n.buyer}")
        revenue = members.observe(
            ms.ref.metric(f"{n.domain}.{n.revenue}"),
            during=window,
            via=via,
            by=(ms.ref.entity(f"{n.domain}.{n.customer}"),),
        )
        count = members.observe(
            ms.ref.metric(f"{n.domain}.{n.order_count}"),
            during=window,
            via=via,
            by=(ms.ref.entity(f"{n.domain}.{n.customer}"),),
        )
        saved = revenue.correlate(count, method="spearman").execute()
    reference = saved.state.artifact_ref.ref
    case.database_path.unlink()
    import shutil

    shutil.rmtree(case.root / "models")
    script = """
import sys
import ibis
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.runtime import DatasourceConnectionService

def forbidden(*args, **kwargs):
    raise AssertionError('cold continuation consulted a source or current Semantic')
ibis.duckdb.connect = forbidden
ms.load = forbidden
DatasourceConnectionService.use_backend = forbidden
session = mv.session.resume(sys.argv[1], by='id')
saved = session.artifact(sys.argv[2])
saved.contract()
saved.show()
journey = sys.argv[3]
if journey == 'j2':
    selected = saved.where(saved.value.lt(0)).execute()
    assert selected.summarize(mv.sum()).execute().to_pandas().iloc[0]['value'] == -90
elif journey == 'j3':
    assert saved.rollup().execute().to_pandas().iloc[0]['value'] == 40
    assert abs(saved.summarize(mv.mean()).execute().to_pandas().iloc[0]['value'] - 130/3) < 1e-12
else:
    selected = saved.coefficient.where(saved.coefficient.value.lt(0.1)).execute()
    assert abs(selected.summarize(mv.mean()).execute().to_pandas().iloc[0]['value'] + .4) < 1e-12
"""
    process = subprocess.run(
        [sys.executable, "-c", script, case.session.id, reference, journey],
        cwd=case.root,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert process.returncode == 0, process.stdout + process.stderr


@pytest.mark.runtime
def test_incompatible_inputs_reject_before_business_or_artifact_reads(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.materialization import graph_dataset
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j2")
    n = case.names
    members = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
    read = members.read(ms.ref.dimension(f"{n.domain}.{n.customer}.{n.region}"))
    first_members = read.where(read.value.eq("east")).members()
    second_members = read.where(read.value.eq("east")).members()
    metric, via = (
        ms.ref.metric(f"{n.domain}.{n.revenue}"),
        ms.ref.relationship(f"{n.domain}.{n.buyer}"),
    )
    august, july = (
        mv.time_scope(start="2026-08-01", end="2026-09-01"),
        mv.time_scope(start="2026-07-01", end="2026-08-01"),
    )
    first = first_members.observe(
        metric, during=august, via=via, by=(ms.ref.entity(f"{n.domain}.{n.customer}"),)
    )
    second = second_members.observe(
        metric, during=july, via=via, by=(ms.ref.entity(f"{n.domain}.{n.customer}"),)
    )
    current, baseline = _observations(case)
    fixed = current.execute()
    second_fixed = second.execute()
    before = len(case.session.runs().items)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("rejected composition read an input")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    monkeypatch.setattr(graph_dataset, "read_result", forbidden)
    for action in (
        lambda: first.compare(second),
        lambda: fixed.compare(baseline),
        lambda: fixed.compare(second_fixed),
    ):
        with pytest.raises(AnalysisError):
            action()
    assert len(case.session.runs().items) == before


def test_members_schema_preflight_does_not_submit_business_rows(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.datasource.adapters import SourceSession

    case = analysis_dsl_case_factory("j1")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("construction submitted business rows")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    monkeypatch.setattr(SourceSession, "stage_derived", forbidden)
    current, _ = _observations(case)
    assert current.contract().phase == "logical"
    assert case.session.runs().items == ()


@pytest.mark.runtime
@pytest.mark.parametrize("threshold", [float("inf"), float("nan"), 2**63])
def test_invalid_threshold_rejects_without_another_run(
    analysis_dsl_case_factory: DslCaseFactory,
    threshold: int | float,
) -> None:
    case = analysis_dsl_case_factory("j2")
    current, baseline = _observations(case)
    difference = current.compare(baseline)
    with pytest.raises(AnalysisError):
        difference.value.lt(threshold)
    assert case.session.runs().items == ()


@pytest.mark.runtime
@pytest.mark.parametrize("journey", ["j1", "j2", "j3", "j4", "j4_ties"])
def test_public_parquet_journeys_have_separate_source_evidence(
    analysis_dsl_case_factory: DslCaseFactory,
    journey: Literal["j1", "j2", "j3", "j4", "j4_ties"],
) -> None:
    from dataclasses import replace

    from tests.shared_fixtures import export_dsl_parquet_models

    case = analysis_dsl_case_factory(journey)
    export_dsl_parquet_models(case, case.root)
    case = replace(
        case,
        catalog=ms.load(workspace_dir=case.root),
        session=mv.session.get_or_create("parquet", report_timezone="UTC"),
    )
    if journey == "j3":
        observed = _ratio(case, coordinates=(case.names.channel,)).execute()
        assert observed.rollup().execute().to_pandas().iloc[0]["value"] == 40
        assert observed.summarize(mv.mean()).execute().to_pandas().iloc[0][
            "value"
        ] == pytest.approx(130 / 3)
    elif journey in ("j4", "j4_ties"):
        n = case.names
        members = case.session.members(ms.ref.entity(f"{n.domain}.{n.customer}"))
        during, via = (
            mv.time_scope(start="2026-08-01", end="2026-09-01"),
            ms.ref.relationship(f"{n.domain}.{n.buyer}"),
        )
        revenue = members.observe(
            ms.ref.metric(f"{n.domain}.{n.revenue}"),
            during=during,
            via=via,
            by=(ms.ref.entity(f"{n.domain}.{n.customer}"),),
        )
        count = members.observe(
            ms.ref.metric(f"{n.domain}.{n.order_count}"),
            during=during,
            via=via,
            by=(ms.ref.entity(f"{n.domain}.{n.customer}"),),
        )
        saved = revenue.correlate(count, method="spearman").execute()
        import pandas as pd

        from tests.shared_fixtures import analysis_dsl_rows

        facts = analysis_dsl_rows(journey)
        amounts = {
            key: sum(row[-1] for row in facts.orders if row[1] == key) for key, _ in facts.customers
        }
        counts = {key: sum(row[1] == key for row in facts.orders) for key, _ in facts.customers}
        oracle = pd.Series(amounts).rank().corr(pd.Series(counts).rank())
        assert saved.to_pandas().iloc[0]["coefficient"] == pytest.approx(oracle)
    else:
        current, baseline = _observations(case)
        if journey == "j1":
            assert current.rollup().execute().to_pandas().iloc[0]["value"] == 1000
        else:
            difference = current.compare(baseline).execute()
            assert difference.to_pandas().set_index("member")["value"].to_dict() == {
                "A": -40,
                "B": 20,
                "C": -50,
                "D": 0,
            }
    assert case.session._runtime.store.layout.generation == 8
