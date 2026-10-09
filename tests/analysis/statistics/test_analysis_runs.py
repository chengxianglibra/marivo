"""Public complete-grid runs and independent interval expectations."""

import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.graph_exchange import ExchangePart, from_arrow
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.runtime
def test_ratio_without_retained_coverage_rejects_with_typed_error(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-04"), grain=mv.grain("day")
    )
    history = members.observe(
        ms.ref.metric("sales.order_count"),
        during=grid,
        via=ms.ref.relationship("sales." + case.names.buyer),
        by=(mv.member(),),
    ).execute()
    ratio = history.ratio(history).execute()
    with pytest.raises(AnalysisError, match="original captured coverage fact"):
        ratio.runs(where=ratio.value.gt(0)).execute()


@pytest.mark.runtime
def test_public_runs(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-04"), grain=mv.grain("day")
    )
    daily = members.observe(
        ms.ref.metric("sales.revenue"),
        during=grid,
        via=ms.ref.relationship("sales." + case.names.buyer),
        by=(mv.member(),),
    )
    assert isinstance(daily, mv.LogicalNumericRelation)
    segments = daily.runs(where=daily.value.gt(-1))
    fixed = segments.execute()
    assert fixed.count.to_pandas().value.tolist() == [1]
    assert all(value.days == 1 for value in fixed.duration.to_pandas().value)
    selected = fixed.where(fixed.count.value.gt(0)).execute()
    assert selected.count.to_pandas().value.tolist() == fixed.count.to_pandas().value.tolist()
    from datetime import timedelta

    duration_selected = fixed.where(fixed.duration.value.gt(timedelta(hours=12))).execute()
    assert duration_selected.start.to_pandas().equals(fixed.start.to_pandas())
    fixed_daily = daily.execute()
    for observation in (daily, fixed_daily):
        filtered = observation.where(observation.value.is_defined())
        with pytest.raises(AnalysisError):
            filtered.runs(where=filtered.value.gt(0))
    with pytest.raises(AnalysisError):
        fixed.duration.rank(order="descending", ties="dense")
    assert fixed._dataset is not None
    checked = fixed._dataset.verified()
    for role in ("condition_cells", "run_cells", "grid_cells", "subject", "finding_policy"):
        with pytest.raises(AnalysisError):
            from_arrow(
                checked.primary,
                checked.contract,
                parts=tuple(p for p in checked.parts if p.role != role),
            )
    for part in checked.parts:
        if part.role in ("condition_cells", "run_cells"):
            corrupted = part.table.set_column(0, part.table.column_names[0], pa.array(["{}"]))
            with pytest.raises(AnalysisError):
                from_arrow(
                    checked.primary,
                    checked.contract,
                    parts=tuple(
                        ExchangePart(p.role, corrupted) if p.role == part.role else p
                        for p in checked.parts
                    ),
                )
    cached = fixed_daily.runs(where=fixed_daily.value.gt(-1))
    receipt_result = cached.execute()
    assert receipt_result._dataset is not None
    artifact_ref = receipt_result.evidence_digest().artifact_ref
    descriptor = receipt_result._dataset.artifact.descriptor
    receipts = (descriptor.primary_receipt.local, *(p.local for p in descriptor.parts))
    before = case.session.runs().items
    for receipt in receipts:
        path = case.root / receipt.project_relative_path / receipt.file_manifest[0].relative_path
        payload = path.read_bytes()
        for fault in ("missing", "corrupt"):
            if fault == "missing":
                path.unlink()
            else:
                path.write_bytes(b"damaged runs receipt")
            try:
                for action_index, action in enumerate(
                    (
                        lambda: case.session.artifact(artifact_ref),
                        lambda: receipt_result.duration.to_pandas(),
                        lambda: receipt_result.where(receipt_result.count.value.gt(0)).execute(),
                        cached.execute,
                    )
                ):
                    try:
                        action()
                    except AnalysisError:
                        pass
                    else:
                        pytest.fail(
                            f"receipt {receipt.project_relative_path} {fault}: action {action_index} accepted"
                        )
                    assert case.session.runs().items == before
            finally:
                path.write_bytes(payload)


@pytest.mark.runtime
@pytest.mark.parametrize("calendar", [False, True])
def test_three_process_offline_runs(
    analysis_dsl_case_factory: DslCaseFactory, calendar: bool
) -> None:
    import subprocess
    import sys

    from tests.analysis.statistics.deviation_fixture import prepare_profiles

    case = analysis_dsl_case_factory("j2")
    prepare_profiles(case, "KS", "parquet", "us", "UTC", calendar, followup=True)
    for phase in ("produce", "fixed", "cold"):
        if phase == "fixed":
            case.database_path.rename(case.database_path.with_suffix(".offline"))
            for path in (case.root / "source_files").glob("*.parquet"):
                path.rename(path.with_suffix(".offline"))
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.statistics.runs_worker",
                str(case.root),
                phase,
                "calendar" if calendar else "day",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr

    import os
    from pathlib import Path

    attachment = os.environ.get("MARIVO_R83_EVIDENCE")
    if attachment and not calendar:
        Path(attachment).write_text((case.root / "runs-recovery.json").read_text())


@pytest.mark.runtime
@pytest.mark.parametrize("fixed", [False, True])
def test_new_grid_after_subject_selection(
    analysis_dsl_case_factory: DslCaseFactory, fixed: bool
) -> None:
    case = analysis_dsl_case_factory("j2")
    scope = mv.time_scope(start="2026-08-01", end="2026-08-04")
    via = ms.ref.relationship("sales." + case.names.buyer)
    members = case.session.members(ms.ref.entity("sales.customer"))
    values = members.observe(
        ms.ref.metric("sales.revenue"), during=scope, via=via, by=(mv.member(),)
    )
    assert isinstance(values, mv.LogicalNumericRelation)
    selected = values.where(values.value.is_defined()).members()
    assert isinstance(selected, mv.LogicalAnalysisDomain)
    grid = mv.time_grid(during=scope, grain=mv.grain("day"))
    fresh = selected.observe(
        ms.ref.metric("sales.revenue"),
        during=grid,
        via=via,
        by=(mv.member(),),
    )
    assert isinstance(fresh, mv.LogicalNumericRelation)
    receiver = fresh.execute() if fixed else fresh
    result = receiver.runs(where=receiver.value.is_defined()).execute()
    assert result.count.to_pandas().value.tolist() == [1]
    limited = receiver.rank(order="descending", ties="dense").limit(1).values
    before = case.session.runs().items
    with pytest.raises(AnalysisError) as failure:
        limited.runs(where=limited.value.is_defined())
    assert "analysis.dsl.NumericComparison.runs" in str(failure.value)
    assert case.session.runs().items == before
    assert not any(".runs(" in action.call for action in limited.contract().actions)
