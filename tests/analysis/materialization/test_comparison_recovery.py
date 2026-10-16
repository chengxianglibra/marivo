"""Native nested, union and ratio producers bind separate fixed/cold processes."""

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.datasource.source_cases import source_case
from tests.support.json import Json, checked, encode
from tests.support.source_trace import SourceTrace


@pytest.mark.slow
def test_native_comparison_independent_fixed_and_cold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    backend = "duckdb"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    profile = {"trino": "iceberg", "clickhouse": "mergetree"}.get(backend, "table")
    with source_case(backend, profile, tmp_path, monkeypatch, reference_data(backend)) as case:
        author_source_project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("r94-comparison", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))

        def observe(month: int) -> mv.LogicalNumericRelation:
            value = members.observe(
                ms.ref.metric("sales.total"),
                during=mv.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01"),
                by=(mv.member(),),
            )
            assert isinstance(value, mv.LogicalNumericRelation)
            return value

        current, baseline, earlier = observe(8), observe(7), observe(6)
        first, second = current.compare(baseline).absolute, baseline.compare(earlier).absolute
        left, right = current.where(current.value.gt(0)), baseline.where(baseline.value.gt(0))
        design = mv.outer(missing="keep")
        originals: dict[str, Json] = {}
        for name, logical in (
            ("current", current),
            ("first", first),
            ("second", second),
            ("left", left),
            ("right", right),
            ("nested", first.compare(second).absolute),
            ("union", left.compare(right, align=design).absolute),
            ("ratio", current.ratio(current)),
        ):
            result = logical.execute()
            originals[name] = snapshot(result)
            source_trace.record(result)
        state: dict[str, Json] = {"session": session.id, "originals": originals}
        (tmp_path / "r94-comparison.json").write_bytes(encode(state))
        source_trace.save(
            "comparison-source-" + backend,
            {**case.environment, "profile": profile},
            {"rows": 3},
            {
                "keys": [
                    ["a", 9007199254740992, 1],
                    ["a", 9007199254740993, 2],
                    ["b", 9007199254740993, 1],
                ],
                "nested": [2, 0, 4],
                "ratio": [1, None, 1],
                "union": [None, None],
            },
            None,
            (case.session,),
        )
        environment = checked({**case.environment, "profile": profile})
    shutil.rmtree(tmp_path / "models")
    for pattern in ("source.duckdb*",):
        for path in tmp_path.glob(pattern):
            path.unlink()
    reports: list[Json] = []
