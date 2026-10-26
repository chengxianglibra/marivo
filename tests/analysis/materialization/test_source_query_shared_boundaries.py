"""Explicit shared Frame keys retain independent source sample and window grids."""

from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import ParamSpec, TypeVar

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.frame_plan import CompiledFramePlan
from marivo.analysis.compiler.graph_plan import FrameKeysInput, QueryPlan
from marivo.analysis.materialization import frame_evaluation
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_source_execution import run_frame_plan
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_source_original_samples_remote import _session
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.source_trace import capture_source

CASES = [("duckdb", "table"), ("trino", "iceberg"), ("clickhouse", "mergetree")]
P = ParamSpec("P")
R = TypeVar("R")


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
        yield mv.session.get_or_create("shared-boundaries", timezone="UTC")


def _plans(monkeypatch: pytest.MonkeyPatch) -> list[QueryPlan]:
    plans: list[QueryPlan] = []

    def tracked(function: Callable[P, R]) -> Callable[P, R]:
        def run(*args: P.args, **kwargs: P.kwargs) -> R:
            assert args and isinstance(args[0], CompiledFramePlan)
            plan = args[0].lowered.plan
            plans.append(plan)
            completed: set[str] = set()
            for stage in plan.stages:
                assert not isinstance(stage, FrameKeysInput)
                assert set(stage.inputs) <= completed
                completed.add(stage.stage_id)
            return function(*args, **kwargs)

        return run

    monkeypatch.setattr(frame_evaluation, "run_frame_plan", tracked(run_frame_plan))
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
    keys = session.population_from(chosen, on=((chosen.axes[0], category),))
    consumer = session.observe({"n": amount.count().where(mv.in_population(keys))}, by=(category,))
    query = chosen.align(consumer, on=((chosen.axes[0], consumer.axes[0]),))
    plans = _plans(monkeypatch)
    trace = capture_source(monkeypatch)
    rows = query.compute().to_pandas()
    assert sorted(rows["left.n"].tolist()) == [0, 0, 1, 2]
    assert rows["left.n"].equals(rows["right.n"])
    assert rows.iloc[:, 0].isna().sum() == 1
    assert rows["left_present"].all() and rows["right_present"].all()
    assert len(plans) == 1
    assert trace.uploaded_key_rows == [4]
    assert trace.returned_rows == [4, 4]


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("same_grid", (False, True))
def test_shared_predicate_keeps_plain_and_per_grids_independent(
    shared_session: Session, monkeypatch: pytest.MonkeyPatch, same_grid: bool, backend: str
) -> None:
    session = shared_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    attributes = session.read(
        session.members(session.catalog.entities.get("sales.facts")), {"amount": amount}
    )
    chosen = attributes.where(attributes["amount"] >= 10)
    keys = session.population_from(
        chosen, on=((chosen.axes[0], session.catalog.entities.get("sales.facts")),)
    )
    total = amount.sum().where(mv.in_population(keys))
    query = session.observe(
        {
            "total": total.per(category).sum() if same_grid else total,
            "mean": total.per(category).mean(),
        }
    )
    trace = capture_source(monkeypatch)
    rows = query.compute().to_pandas()
    assert rows["total"].tolist() == [60]
    assert rows["mean"].tolist() == [30.0]
    # Matching axes do not qualify sampled contributions for common-source
    # sharing. Remote consumers retain their own checked parameter boundary.
    assert trace.uploaded_key_rows == ([3] if backend == "duckdb" else [3, 3])


@pytest.mark.parametrize("backend,profile", source_params([("duckdb", "table")]))
@pytest.mark.parametrize("selected", (False, True), ids=("unselected", "where-selected"))
def test_shared_query_population_remains_a_real_host_sample_domain(
    shared_session: Session, monkeypatch: pytest.MonkeyPatch, selected: bool
) -> None:
    session = shared_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    producer = session.observe({"n": amount.count()}, by=(category,))
    chosen = producer.where(producer["n"] >= 0) if selected else producer
    samples = session.population_from(chosen, on=((chosen.axes[0], category),))
    query = session.observe(
        {
            "direct": amount.count().where(mv.in_population(samples)),
            "sample": amount.sum().where(amount >= 20).per(samples).mean(),
        }
    )
    plans = _plans(monkeypatch)
    trace = capture_source(monkeypatch)
    rows = query.compute().to_pandas()
    assert rows["direct"].tolist() == [3]
    assert rows["sample"].tolist() == [13]
    assert len(plans) == 1
    assert trace.uploaded_key_rows == [4]
    # The same four-key sample input is retained for both source consumers and
    # the local sample finisher; the schedule assertion rejects dangling inputs.
    assert any(len(stage.inputs) > 1 for stage in plans[0].stages)


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
    session = mv.session.get_or_create("shared-fixed-selection", timezone="UTC")
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    producer = session.observe({"n": amount.sum()}, by=(category,))
    chosen = (
        producer.top(1, by=producer["n"])
        if selection == "top"
        else producer.where(producer["n"] >= 10)
    )
    fixed = chosen.compute()
    assert fixed.to_pandas().iloc[:, 0].tolist() == ["A"]
    pq.write_table(pa.Table.from_pylist(changed, schema=schema), path)
    selected = session.population_from(fixed, on=((fixed.axes[0], category),))
    query = session.observe(
        {
            "direct": amount.sum().where(mv.in_population(selected)),
            "sample": amount.sum().per(selected).mean(),
        }
    )
    trace = capture_source(monkeypatch)
    rows = query.compute().to_pandas()
    assert rows["direct"].tolist() == [10]
    assert rows["sample"].tolist() == [10.0]
    assert trace.uploaded_key_rows == [1]


@pytest.mark.parametrize("backend,profile", source_params([("duckdb", "table")]))
def test_shared_empty_selection_cannot_erase_parent_strict_error(
    shared_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = shared_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    producer = session.observe({"n": amount.sum(nulls="error")}, by=(category,))
    chosen = producer.top(0, by=producer["n"])
    samples = session.population_from(chosen, on=((chosen.axes[0], category),))
    query = session.observe(
        {
            "direct": amount.count().where(mv.in_population(samples)),
            "sample": amount.sum().per(samples).mean(),
        }
    )
    trace = capture_source(monkeypatch)
    with pytest.raises(MaterializationError, match="non-NA source values"):
        query.compute()
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
    ) as (session, _):
        amount = session.catalog.fields.get("sales.facts.amount")
        customer = session.catalog.fields.get("sales.facts.customer")
        day = session.catalog.fields.get("sales.facts.day")
        producer = session.observe({"n": amount.count()}, by=(customer,))
        chosen = producer.where(producer["n"] > 0)
        selected = session.population_from(chosen, on=((chosen.axes[0], customer),))
        total = amount.sum(time=day).where(mv.in_population(selected))
        calendar = session.time_scope(start="2026-10-07", end="2026-10-09").by(mv.grain("day"))
        query = session.observe(
            {
                "history": total.window(mv.all_history()),
                "recent": total.window(mv.trailing_duration(duration=timedelta(days=2))),
            },
            by=(calendar,),
        )
        trace = capture_source(monkeypatch)
        rows = query.compute().to_pandas()
        assert rows["history"].tolist() == [1000, 1000]
        assert rows["recent"].tolist() == [500, 500]
        assert trace.uploaded_key_rows == ([2] if backend == "duckdb" else [2, 2, 2, 2])
