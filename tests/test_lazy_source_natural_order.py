"""Real source reordering cannot change canonical previews or ranked prefixes."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import duckdb
import pytest

from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.storage import ReadPolicy, read_preview
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.analysis.observation.population import MaterializedPopulationDataset
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry, seed_execution_database

pytestmark = pytest.mark.runtime

_REVENUE = ref.metric("sales.revenue")
_COMPOSITE = ref.entity("sales.composite")
_VALUES: dict[int, float | None] = {1: 20.0, 2: 20.0, 3: 10.0, 4: 10.0, 5: 30.0, 6: None}
_NATURAL_ORDERS = ((6, 4, 2, 5, 1, 3), (3, 1, 5, 2, 4, 6))
_COMPOSITE_ORDERS = (
    (("b", 1), ("a", 2), ("a", 1)),
    (("a", 1), ("a", 2), ("b", 1)),
)


def _replace_source_rows(database: Path, variant: int) -> None:
    """Reinsert identical rows and prove the unqualified source scans changed."""
    order = _NATURAL_ORDERS[variant]
    composite_order = _COMPOSITE_ORDERS[variant]
    connection = duckdb.connect(str(database))
    try:
        connection.execute("DELETE FROM orders")
        connection.executemany(
            "INSERT INTO orders (id, amount) VALUES (?, ?)",
            [(identity, _VALUES[identity]) for identity in order],
        )
        connection.execute("DELETE FROM composite")
        connection.executemany("INSERT INTO composite (tenant, id) VALUES (?, ?)", composite_order)
        # No ORDER BY: these assertions observe the backend's natural scan order.
        assert connection.execute("SELECT id FROM orders").fetchall() == [
            (identity,) for identity in order
        ]
        assert connection.execute("SELECT tenant, id FROM composite").fetchall() == list(
            composite_order
        )
    finally:
        connection.close()


def _preview(
    runtime: DatasetRuntime,
    dataset: MaterializedMetricDataset | MaterializedPopulationDataset,
    policy: ReadPolicy,
) -> list[dict[str, object]]:
    record = runtime.store.artifact(dataset.state.artifact_ref.ref)
    assert record is not None
    table = read_preview(
        project_root=runtime.store.project_root,
        receipt=record.descriptor.storage_receipt,
        row_contract=dataset.row_contract,
        row_set_contract=dataset.row_set_contract,
        policy=policy,
    )
    rows: list[dict[str, object]] = table.to_pylist()
    return rows


def test_unordered_metric_and_population_previews_survive_reversed_source_scans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    policy = ReadPolicy(preview_rows=2)
    monkeypatch.setattr(admission, "_READ_POLICY", policy)
    metric_previews = []
    population_previews = []
    rendered_previews = []
    session_refs = []
    artifact_refs: list[str] = []
    for variant in range(2):
        _replace_source_rows(database, variant)
        runtime = DatasetRuntime.create(tmp_path, f"unordered-source-{variant}")
        sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
        logical_metric = sources.observe(_REVENUE)
        logical_population = sources.population(_COMPOSITE)
        assert logical_metric.row_set_contract.ordering.kind == "unordered"
        assert logical_population.row_set_contract.ordering.kind == "unordered"
        metric = logical_metric.execute()
        assert runtime.statistics.primary_queries == 1
        assert runtime.statistics.validation_queries > 0
        assert runtime.statistics.transferred_rows == 6
        population = logical_population.execute()
        assert metric.row_set_contract.ordering.kind == "unordered"
        assert population.row_set_contract.ordering.kind == "unordered"
        assert runtime.statistics.primary_queries == 1
        assert runtime.statistics.validation_queries > 0
        assert runtime.statistics.transferred_rows == 3
        metric_previews.append(_preview(runtime, metric, policy))
        population_previews.append(_preview(runtime, population, policy))
        metric.show()
        metric_lines = capsys.readouterr().out.splitlines()[1:]
        population.show()
        population_lines = capsys.readouterr().out.splitlines()[1:]
        rendered_previews.append((metric_lines, population_lines))
        assert runtime.statistics.primary_queries == 1
        assert runtime.store.resources(runtime.session_ref) == ()
        session_refs.append(runtime.session_ref)
        artifact_refs.extend((metric.state.artifact_ref.ref, population.state.artifact_ref.ref))

    assert len(set(session_refs)) == 2
    assert len(set(artifact_refs)) == 4
    assert (
        metric_previews
        == [
            [
                {"entity_identity": {"id": 1}, "revenue": 20.0},
                {"entity_identity": {"id": 2}, "revenue": 20.0},
            ]
        ]
        * 2
    )
    assert (
        population_previews
        == [
            [
                {"entity_identity": {"tenant": "a", "id": 1}},
                {"entity_identity": {"tenant": "a", "id": 2}},
            ]
        ]
        * 2
    )
    assert rendered_previews[0] == rendered_previews[1]
    assert rendered_previews[0] == (
        [
            "Preview: 2 of 6 rows (maximum 2)",
            "entity_identity | revenue",
            "<identity> | 20.0",
            "<identity> | 20.0",
        ],
        [
            "Preview: 2 of 3 rows (maximum 2)",
            "entity_identity",
            "<identity>",
            "<identity>",
        ],
    )


@pytest.mark.parametrize("ties", ["ordinal", "dense", "min", "max"])
def test_ranked_limit_cuts_the_same_tie_after_source_reordering(
    tmp_path: Path, ties: Literal["ordinal", "dense", "min", "max"]
) -> None:
    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    expected_ranks = {
        "ordinal": [1, 2, 3, 4],
        "dense": [1, 2, 2, 3],
        "min": [1, 2, 2, 4],
        "max": [1, 3, 3, 5],
    }
    artifact_refs: list[str] = []
    session_refs = []
    for variant in range(2):
        _replace_source_rows(database, variant)
        runtime = DatasetRuntime.create(tmp_path, f"ranked-source-{variant}")
        sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
        source = sources.observe(_REVENUE)
        ranked = source.rank(source.fields.metric(_REVENUE), ties=ties)
        selected = ranked.limit(4).execute()
        rows = selected.to_pandas()
        # The boundary splits the 10.0 tie; its lower identity must always win.
        assert rows["entity_identity"].tolist() == [(5,), (1,), (2,), (3,)]
        assert rows["revenue"].tolist() == [30.0, 20.0, 20.0, 10.0]
        assert rows["rank"].tolist() == expected_ranks[ties]
        assert selected.row_set_contract.ordering.kind == "ordered"
        assert runtime.statistics.primary_queries == 1
        assert runtime.statistics.validation_queries > 0
        assert runtime.statistics.transferred_rows == 4
        assert runtime.store.resources(runtime.session_ref) == ()
        artifact_refs.append(selected.state.artifact_ref.ref)
        session_refs.append(runtime.session_ref)

    assert len(set(session_refs)) == 2
    assert len(set(artifact_refs)) == 2
