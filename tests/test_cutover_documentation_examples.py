"""Execute current bilingual workflow and evidence examples against public APIs."""

import ast
import re
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms

ROOT = Path(__file__).resolve().parents[1]


def _blocks(language: str, page: str) -> tuple[str, ...]:
    prefix = "docs" if language == "en" else "zh-cn/docs"
    path = ROOT / f"site/src/content/docs/{prefix}/latest/concepts/{page}.mdx"
    return tuple(
        match[1]
        for match in re.findall(
            r"(?m)^(```|~~~)python\n(.*?)^\1\s*$", path.read_text(), flags=re.DOTALL
        )
    )


@pytest.mark.parametrize(
    "page,count", [("analysis-workflow", 1), ("evidence", 2), ("semantic-layer", 45)]
)
def test_bilingual_examples_have_identical_executable_contracts(page: str, count: int) -> None:
    assert len(_blocks("en", page)) == count
    assert _blocks("en", page) == _blocks("zh", page)


def test_semantic_tutorial_uses_current_observation_and_alignment_contracts() -> None:
    for code in _blocks("en", "semantic-layer"):
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "observe":
                    assert not {"grain", "dimensions"} & {kw.arg for kw in node.keywords}
                assert node.func.attr not in {"occurrence_progress", "working_day_progress"}
    for prefix in ("docs", "zh-cn/docs"):
        pages = ROOT / f"site/src/content/docs/{prefix}/latest"
        for path in pages.rglob("*.mdx"):
            if "release-notes" in path.parts:
                continue
            text = path.read_text()
            assert "mv.occurrence_progress" not in text, path
            assert "mv.working_day_progress" not in text, path
            assert "meta.zero_denominator_rows" not in text, path


@pytest.mark.runtime
def test_semantic_tutorial_monthly_observation_executes(
    authoring_evidence_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(authoring_evidence_project)
    model = authoring_evidence_project / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace(
            "name='log_date', entity=orders", "name='order_date', entity=orders"
        )
    )
    with duckdb.connect(str(authoring_evidence_project / "warehouse.duckdb")) as connection:
        identifiers = connection.execute("SELECT query_id FROM orders ORDER BY query_id").fetchall()
        assert len(identifiers) == 4
        for identifier, day, amount in zip(
            identifiers,
            ("20260101", "20260201", "20260301", "20260401"),
            (10, 20, 30, 40),
            strict=True,
        ):
            connection.execute(
                "UPDATE orders SET log_date=?, amount=? WHERE query_id=?",
                [day, amount, identifier[0]],
            )
    session = mv.session.get_or_create("semantic-example", report_timezone="UTC")
    namespace: dict[str, object] = {
        "mv": mv,
        "ms": ms,
        "session": session,
        "catalog": session.catalog,
    }
    code = next(
        block
        for block in _blocks("en", "semantic-layer")
        if block.startswith('revenue_entry = catalog.metrics.get("sales.revenue")')
    )
    exec(compile(code, "semantic-observation-example", "exec"), namespace)
    logical = namespace["dataset"]
    assert isinstance(logical, mv.LogicalMetricDataset)
    assert session.runs().items == ()
    assert not session._runtime.statistics.statements
    rows = logical.execute().to_pandas()
    assert sorted(rows["revenue"].tolist()) == [10.0, 20.0, 30.0]
    assert len(session.runs().items) == 1


@pytest.mark.runtime
def test_workflow_evidence_and_cold_recovery_examples(
    authoring_evidence_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(authoring_evidence_project)
    database = authoring_evidence_project / "warehouse.duckdb"
    with duckdb.connect(str(database)) as connection:
        identifiers = connection.execute("SELECT query_id FROM orders ORDER BY query_id").fetchall()
        assert len(identifiers) == 4
        for identifier, day, amount in zip(
            identifiers,
            ("20260701", "20260702", "20260601", "20260602"),
            (10, 20, 5, 7),
            strict=True,
        ):
            connection.execute(
                "UPDATE orders SET log_date=?, amount=? WHERE query_id=?",
                [day, amount, identifier[0]],
            )
    namespace: dict[str, object] = {}
    exec(compile(_blocks("en", "analysis-workflow")[0], "workflow-example", "exec"), namespace)
    change = namespace["change"]
    assert isinstance(change, mv.MaterializedDeltaDataset)
    rows = change.to_pandas()
    assert rows[["current_value", "baseline_value", "delta"]].to_dict("records") == [
        {"current_value": 30.0, "baseline_value": 12.0, "delta": 18.0}
    ]
    session = namespace["session"]
    assert isinstance(session, mv.Session)
    assert len(session.runs().items) == 1
    exec(compile(_blocks("en", "evidence")[0], "evidence-example", "exec"), namespace)
    # Reusing the identical definition must not invent another successful Run.
    assert len(session.runs().items) == 1
    database.rename(database.with_suffix(".offline"))
    resumed = mv.session.resume(session.id)
    namespace["session"] = resumed
    namespace["run_id"] = session.runs().items[0].run_id
    exec(compile(_blocks("en", "evidence")[1], "recovery-example", "exec"), namespace)
    recovered = namespace["artifact"]
    assert isinstance(recovered, mv.MaterializedDeltaDataset)
    assert recovered.to_pandas()["delta"].tolist() == [18.0]
    assert not resumed._runtime.statistics.statements
