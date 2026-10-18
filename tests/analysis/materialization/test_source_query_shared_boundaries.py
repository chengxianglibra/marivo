"""Shared source keys preserve independent host consumers and sample/window grids."""

from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.compiler.graph_lowering import LoweredQuery
from marivo.analysis.compiler.graph_plan import LocalCompute, QueryPlan, SourceRead
from marivo.analysis.core.artifact_data import OutputEvidence
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import query_key_fusion, terminal_publication
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_source_execution import PreparedSources
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import CompiledRead, SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_source_original_samples_remote import _session
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.source_trace import capture_source

CASES = [("duckdb", "table"), ("trino", "iceberg"), ("clickhouse", "mergetree")]


@pytest.fixture
def shared_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> Iterator[Session]:
    rows: list[dict[str, object]] = [
        {
            "id": identity,
            "tenant": tenant,
            "category": category,
            "amount": amount,
            "big": 1,
            "weight": 1,
            "day": date(2026, 7, 1),
        }
        for identity, tenant, category, amount in (
            (1, "a", "A", 10),
            (2, "a", "A", 30),
            (3, "b", "B", 20),
            (4, "b", None, None),
            (5, "c", "C", None),
        )
    ]
    data = SourceData(
        "id BIGINT NOT NULL, tenant VARCHAR(16) NOT NULL, category VARCHAR(16), amount BIGINT, big BIGINT, weight BIGINT, day DATE",
        "(1,'a','A',10,1,1,DATE '2026-07-01'),"
        "(2,'a','A',30,1,1,DATE '2026-07-01'),"
        "(3,'b','B',20,1,1,DATE '2026-07-01'),"
        "(4,'b',NULL,NULL,1,1,DATE '2026-07-01'),"
        "(5,'c','C',NULL,1,1,DATE '2026-07-01')",
        "id Int64, tenant String, category Nullable(String), amount Nullable(Int64), big Int64, weight Int64, day Date",
        rows,
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        yield mv.session.get_or_create("shared-boundaries", report_timezone="UTC")


def _baseline(query: mv.Query, monkeypatch: pytest.MonkeyPatch) -> pd.DataFrame:
    def unchanged(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
        return lowered

    with monkeypatch.context() as scoped:
        scoped.setattr(query_key_fusion, "fuse_query_keys", unchanged)
        scoped.setattr(query_key_fusion, "fuse_query_terminal", unchanged)
        return query.execute().to_pandas()


def _plans(monkeypatch: pytest.MonkeyPatch) -> list[QueryPlan]:
    plans: list[QueryPlan] = []
    previous = terminal_publication._compute

    def compute(
        prepared: PreparedSources | None, lowered: LoweredQuery, evidence: list[OutputEvidence]
    ) -> StageOutput:
        plans.append(lowered.plan)
        return previous(prepared, lowered, evidence)

    monkeypatch.setattr(terminal_publication, "_compute", compute)
    return plans


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_shared_selected_keys_keep_both_host_endpoint_values(
    shared_session: Session, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    session = shared_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    producer = session.observe({"n": amount.count()}, by=(category,))
    chosen = producer.where(producer["n"] >= 0)
    consumer = session.observe(
        {"n": amount.count().where(mv.in_population(chosen.population()))}, by=(category,)
    )
    query = chosen.align(consumer)
    expected = _baseline(query, monkeypatch)
    plans = _plans(monkeypatch)
    trace = capture_source(monkeypatch)
    actual = query.execute().to_pandas()
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    assert sorted(actual[("value", "left.n")].tolist()) == [0, 0, 1, 2]
    assert actual[("value", "left.n")].equals(actual[("value", "right.n")])
    assert actual.iloc[:, 0].isna().sum() == 1
    assert trace.uploaded_key_rows == []
    assert trace.returned_rows == [4]
    # Fused endpoint transport must retain both complete host inputs, including
    # their values and presence, rather than retaining only the membership keys.
    finishers = [
        stage.native_query_finish
        for stage in plans[-1].stages
        if isinstance(stage, SourceRead) and stage.native_query_finish is not None
    ]
    assert len(finishers) == 1 and len(finishers[0].inputs) == 2
    assert all(
        any(name not in endpoint.endpoint.keys for name, _ in endpoint.columns)
        for endpoint in finishers[0].inputs
    )
    trace.save(
        f"query-shared-boundaries-{backend}-mixed-endpoints",
        {"provider": backend},
        {"rows": 5},
        {"scenario": "mixed host endpoints and source membership", "uploaded_key_rows": []},
        None,
        (),
    )


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("same_grid", (False, True))
def test_shared_predicate_keeps_plain_and_per_grids_independent(
    shared_session: Session, monkeypatch: pytest.MonkeyPatch, same_grid: bool, backend: str
) -> None:
    session = shared_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    attributes = session.read(session.members(ms.ref.entity("sales.facts")), {"amount": amount})
    condition = mv.in_population(attributes.where(attributes["amount"] >= 10).population())
    total = amount.sum(empty="zero").where(condition)
    query = session.observe(
        {
            "total": total.per(category).sum() if same_grid else total,
            "mean": total.per(category).mean(),
        }
    )
    expected = _baseline(query, monkeypatch)
    trace = capture_source(monkeypatch)
    actual = query.execute().to_pandas()
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    assert actual[("value", "total")].tolist() == [60]
    # A has two contributions (10 + 30), B only one (20). The sample mean is
    # (40 + 20) / 2, not the mean of the three raw contribution values.
    assert actual[("value", "mean")].tolist() == [30.0]
    assert trace.uploaded_key_rows == []
    assert sorted(trace.returned_rows) == ([2] if same_grid else [1, 2])
    trace.save(
        f"query-shared-boundaries-{backend}-{'same-per-grid' if same_grid else 'plain-and-per'}",
        {"provider": backend},
        {"rows": 5},
        {
            "scenario": "same per grid" if same_grid else "plain and per grids",
            "total": 60,
            "mean": 30.0,
        },
        None,
        (),
    )


@pytest.mark.parametrize("backend,profile", source_params([("duckdb", "table")]))
@pytest.mark.parametrize("empty", ("zero", "null"))
@pytest.mark.parametrize("selected", (False, True), ids=("unselected", "where-selected"))
def test_shared_query_population_remains_a_real_host_sample_domain(
    shared_session: Session, monkeypatch: pytest.MonkeyPatch, empty: str, selected: bool
) -> None:
    session = shared_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    producer = session.observe({"n": amount.count()}, by=(category,))
    samples = producer.where(producer["n"] >= 0).population() if selected else producer.population()
    query = session.observe(
        {
            "direct": amount.count().where(mv.in_population(samples)),
            "sample": amount.sum(empty=empty).where(amount >= 20).per(samples).mean(),
        }
    )
    expected = _baseline(query, monkeypatch)
    plans = _plans(monkeypatch)
    trace = capture_source(monkeypatch)
    actual = query.execute().to_pandas()
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    assert actual[("value", "direct")].tolist() == [3]
    assert actual[("value", "sample")].tolist() == [12.5 if empty == "zero" else 25.0]
    # A selected host sample domain must remain fixed for the direct consumer.
    # An unselected producer can stay a closed source fragment with no upload.
    assert trace.uploaded_key_rows == ([4] if selected else [])
    # C and the null category have no qualifying contributions but still belong
    # to the independently consumed sample domain. Membership fusion may not
    # prune that producer out of the host sample formula.
    plan = plans[-1]
    identifiers = {stage.stage_id for stage in plan.stages}
    required = {item.stage_id: item for item in plan.required_slots}
    programs = [
        stage
        for stage in plan.stages
        if isinstance(stage, LocalCompute)
        and stage.program is not None
        and stage.program.populations
    ]
    assert len(programs) == 1
    program = programs[0].program
    assert program is not None
    for population in program.populations:
        assert population.stage_id in identifiers
        assert population.stage_id in programs[0].inputs
        assert set(population.keys) <= set(required[population.stage_id].columns)


@pytest.mark.parametrize("selection", ("top", "where"))
def test_shared_selected_host_domain_stays_fixed_when_source_ranking_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    selection: str,
) -> None:
    schema = pa.schema(
        [
            pa.field("id", pa.int64(), nullable=False),
            pa.field("category", pa.string()),
            pa.field("amount", pa.int64()),
        ]
    )
    initial = [{"id": 1, "category": "A", "amount": 10}, {"id": 2, "category": "B", "amount": 1}]
    changed = [{**initial[0]}, {**initial[1], "amount": 1000}]
    path = tmp_path / "facts.parquet"
    duckdb.connect(str(tmp_path / "warehouse.duckdb")).close()
    pq.write_table(pa.Table.from_pylist(initial, schema=schema), path)
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.duckdb(name='warehouse',path={str(tmp_path / 'warehouse.duckdb')!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Analytics',default=True)\n",
            "sales/model.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            + f"facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source=md.parquet({str(path)!r}),primary_key=['id'])\n"
            + "category=ms.dimension_column(name='category',entity=facts,column='category')\n"
            + "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n",
        }
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("shared-fixed-selection", report_timezone="UTC")
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    producer = session.observe({"n": amount.sum()}, by=(category,))
    selected = (
        producer.top(1, by=producer["n"])
        if selection == "top"
        else producer.where(producer["n"] >= 10)
    ).population()
    query = session.observe(
        {
            "direct": amount.sum().where(mv.in_population(selected)),
            "sample": amount.sum(empty="zero").per(selected).mean(),
        }
    )
    frames: list[pd.DataFrame] = []
    for optimized in (False, True):
        pq.write_table(pa.Table.from_pylist(initial, schema=schema), path)
        with monkeypatch.context() as scoped:
            trace = capture_source(scoped)
            previous = SourceSession.execute
            mutated = False

            def execute(
                owner: SourceSession,
                read: CompiledRead,
                previous: Callable[[SourceSession, CompiledRead], pd.DataFrame] = previous,
            ) -> pd.DataFrame:
                nonlocal mutated
                frame = previous(owner, read)
                if (
                    read.purpose.startswith("query.")
                    and read.purpose != "query.prepare"
                    and not mutated
                ):
                    pq.write_table(pa.Table.from_pylist(changed, schema=schema), path)
                    mutated = True
                return frame

            scoped.setattr(SourceSession, "execute", execute)
            frames.append(query.execute().to_pandas() if optimized else _baseline(query, scoped))
            assert mutated
        assert frames[-1][("value", "direct")].tolist() == [10]
        assert frames[-1][("value", "sample")].tolist() == [10.0]
        assert trace.uploaded_key_rows == [1]
    pd.testing.assert_frame_equal(frames[1], frames[0], check_exact=True)


@pytest.mark.parametrize("backend,profile", source_params([("duckdb", "table")]))
def test_shared_empty_selection_cannot_erase_parent_strict_error(
    shared_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = shared_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    producer = session.observe({"n": amount.sum(nulls="error")}, by=(category,))
    samples = producer.top(0, by=producer["n"]).population()
    query = session.observe(
        {
            "direct": amount.count().where(mv.in_population(samples)),
            "sample": amount.sum(empty="zero").per(samples).mean(),
        }
    )
    with pytest.raises(AnalysisError, match="non-NA source values"):
        _baseline(query, monkeypatch)
    trace = capture_source(monkeypatch)
    with pytest.raises(AnalysisError, match="non-NA source values"):
        query.execute()
    assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_shared_population_does_not_merge_distinct_window_occurrences(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    with _session(
        backend, profile, tmp_path, monkeypatch, semantic_project_factory, window=True
    ) as session:
        amount = session.catalog.fields.get("sales.facts.amount")
        customer = session.catalog.fields.get("sales.facts.customer")
        day = session.catalog.fields.get("sales.facts.day")
        producer = session.observe({"n": amount.count()}, by=(customer,))
        selected = producer.where(producer["n"] > 0).population()
        total = amount.sum(time=day).where(mv.in_population(selected))
        calendar = session.time_scope(start="2026-10-07", end="2026-10-09").by(mv.grain("day"))
        query = session.observe(
            {
                "history": total.window(mv.all_history()),
                "recent": total.window(mv.trailing_duration(duration=timedelta(days=2))),
            },
            during=calendar,
        )
        expected = _baseline(query, monkeypatch)
        trace = capture_source(monkeypatch)
        actual = query.execute().to_pandas()
        pd.testing.assert_frame_equal(actual, expected, check_exact=True)
        assert actual[("value", "history")].tolist() == [1000, 1000]
        assert actual[("value", "recent")].tolist() == [500, 500]
        assert trace.uploaded_key_rows == []
        assert trace.returned_rows == [1, 1, 1, 1]
        trace.save(
            f"query-shared-boundaries-{backend}-distinct-windows",
            {"provider": backend},
            {"rows": 1000},
            {
                "scenario": "distinct window occurrences",
                "history": [1000, 1000],
                "recent": [500, 500],
            },
            None,
            (),
        )
