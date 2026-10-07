"""Statistical Store integrity, resource closure and composed public journeys."""

from typing import Literal

import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.runtime
@pytest.mark.parametrize("kind", ("association", "forecast"))
def test_unreadable_receipts_reject_value_reads_and_recovery(
    analysis_dsl_case_factory: DslCaseFactory, kind: Literal["association", "forecast"]
) -> None:
    from tests.analysis.statistics.deviation_fixture import prepare_profiles

    case = analysis_dsl_case_factory("j2")
    prepare_profiles(case, "KS", "parquet", "us", "UTC", False, followup=True)
    ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create("statistics-integrity", report_timezone="UTC")
    members = session.members(ms.ref.entity("sales.order"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-04"), grain=mv.grain("day")
    )
    a = members.each(grid).observe(ms.ref.metric("sales.total_0"), during=grid.window).execute()
    b = members.each(grid).observe(ms.ref.metric("sales.total_1"), during=grid.window).execute()
    logical = a.correlate(b) if kind == "association" else a.forecast(horizon=mv.periods(2))
    fixed = logical.execute()
    assert fixed._dataset is not None
    descriptor = fixed._dataset.artifact.descriptor
    ref = fixed.state.artifact_ref
    item = fixed.findings(limit=1).items[0]
    before = session.runs().items
    for receipt in (descriptor.primary_receipt.local, *(p.local for p in descriptor.parts)):
        path = case.root / receipt.project_relative_path / receipt.file_manifest[0].relative_path
        payload = path.read_bytes()
        for fault in ("missing", "corrupt"):
            if fault == "missing":
                path.unlink()
            else:
                path.write_bytes(b"damaged statistical receipt")
            try:
                for action in (
                    lambda: session.artifact(ref),
                    fixed.to_pandas,
                    logical.execute,
                ):
                    with pytest.raises(AnalysisError):
                        action()
                    assert session.runs().items == before
            finally:
                path.write_bytes(payload)
    assert fixed.finding(item.finding_id) == item
    assert session._runtime.store.resources(session.id) == ()


@pytest.mark.runtime
@pytest.mark.parametrize("kind", ("association", "forecast"))
def test_source_reader_close_failure_keeps_prior_artifact(
    analysis_dsl_case_factory: DslCaseFactory,
    kind: Literal["association", "forecast"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.datasource.adapters import SourceBatchStream
    from tests.analysis.statistics.deviation_fixture import prepare_profiles

    case = analysis_dsl_case_factory("j2")
    prepare_profiles(case, "KS", "parquet", "us", "UTC", False, followup=True)
    ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create("statistics-resources", report_timezone="UTC")
    members = session.members(ms.ref.entity("sales.order"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-04"), grain=mv.grain("day")
    )
    a = members.each(grid).observe(ms.ref.metric("sales.total_0"), during=grid.window)
    b = members.each(grid).observe(ms.ref.metric("sales.total_1"), during=grid.window)
    previous = a.execute()
    close = SourceBatchStream.close
    closed: list[bool] = []

    def fail(stream: SourceBatchStream) -> None:
        close(stream)
        closed.append(True)
        raise pa.ArrowInvalid("injected statistical source close failure")

    monkeypatch.setattr(SourceBatchStream, "close", fail)
    logical = a.correlate(b) if kind == "association" else a.forecast(horizon=mv.periods(2))
    with pytest.raises(AnalysisError):
        logical.execute()
    assert closed and session._runtime.store.resources(session.id) == ()
    assert len(previous.to_pandas()) == 9
    with session._runtime.store._read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM dataset_artifacts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM dataset_evidence").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0


@pytest.mark.runtime
def test_three_quantity_search_and_forecast_views(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from tests.analysis.statistics.deviation_fixture import prepare_profiles

    case = analysis_dsl_case_factory("j2")
    prepare_profiles(case, "KS", "parquet", "us", "UTC", False, followup=True)
    ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create("a11-statistics", report_timezone="UTC")
    members = session.members(ms.ref.entity("sales.order"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-04"), grain=mv.grain("day")
    )
    observations = tuple(
        members.each(grid)
        .observe(ms.ref.metric(f"sales.total_{i}"), during=grid.window)
        .group_by(grid)
        .rollup()
        for i in (0, 1, 5)
    )
    for method in ("pearson", "spearman", "kendall"):
        search = observations[0].correlate(*observations[1:], method=method, lag_range=range(-1, 2))
        selected = search.where(search.selected.value.eq(True))
        table = mv.table(
            coefficient=selected.coefficient.rank(order="descending", ties="dense").values
        ).execute()
        assert table.to_pandas().coefficient.tolist() == [1.0, 1.0, 1.0]
        assert table._dataset is not None
        assert table._dataset.evidence_digest().finding_count == 9
    for model in (mv.naive(), mv.drift(), mv.seasonal_naive(periods=2)):
        forecast = observations[0].forecast(horizon=mv.periods(2), model=model)
        selected_forecast = forecast.where(forecast.prediction.value.gt(0))
        table = mv.table(
            prediction=selected_forecast.prediction,
            lower=selected_forecast.lower,
            upper=selected_forecast.upper,
        ).execute()
        rows = table.to_pandas()
        assert len(rows) == 2
        assert all(rows.lower <= rows.prediction) and all(rows.prediction <= rows.upper)
        assert table._dataset is not None
        assert table._dataset.evidence_digest().finding_count == 2


@pytest.mark.runtime
def test_forecast_findings_cap_and_selection_transport(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    import duckdb

    with duckdb.connect(str(case.database_path)) as db:
        db.executemany(
            "INSERT INTO customer(customer_id, region) VALUES (?, ?)",
            [(f"cap_{i}", "zero-history") for i in range(247)],
        )
    members = case.session.members(ms.ref.entity("sales.customer"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-04"), grain=mv.grain("day")
    )
    history = members.each(grid).observe(
        ms.ref.metric("sales.order_count"),
        during=grid.window,
        via=ms.ref.relationship("sales." + case.names.buyer),
    )
    result = history.forecast(horizon=mv.periods(4)).execute()
    assert len(result.prediction.to_pandas()) == 1004
    assert result.evidence_digest().finding_count == 1000
    assert result._dataset is not None
    from marivo.analysis.materialization.graph_findings import _policy

    policy = _policy(result._dataset.verified().parts)
    assert (policy.eligible, policy.emitted, policy.truncated) == (1004, 1000, 4)
    page = result.findings(limit=100)
    assert len(page.items) == 100 and page.next_cursor is not None
    second = result.findings(limit=100, cursor=page.next_cursor)
    assert len(second.items) == 100 and not {i.finding_id for i in page.items} & {
        i.finding_id for i in second.items
    }
    empty = result.where(result.prediction.value.lt(0)).execute()
    assert empty.prediction.to_pandas().empty
    assert empty.evidence_digest().finding_count == 1000
    transported = empty.findings(limit=100)
    assert tuple((i.canonical_item_key, i.value) for i in transported.items) == tuple(
        (i.canonical_item_key, i.value) for i in page.items
    )
    assert all(i.artifact_ref == empty.state.artifact_ref for i in transported.items)
