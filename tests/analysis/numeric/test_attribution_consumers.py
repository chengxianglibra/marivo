"""Bounded additive and component attribution source and retained consumers."""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, NoReturn

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.numeric.attribution_workflows import allocation
from tests.analysis.statistics.pandas_workflows import plain
from tests.datasource.source_cases import source_case
from tests.support.source_trace import SourceTrace


@pytest.mark.slow
def test_source_and_retained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    backend = "duckdb"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    profile = "table"
    data = reference_data(backend)
    data = replace(
        data,
        columns=data.columns + ", region VARCHAR(10), null_region VARCHAR(10)",
        values=data.values.replace("00:00:00')", "00:00:00','east',NULL)"),
        clickhouse_columns=data.clickhouse_columns
        + ", region String, null_region Nullable(String)",
        rows=[{**row, "region": "east", "null_region": None} for row in data.rows],
    )
    data = replace(
        data,
        values=data.values
        + ",(9007199254740994,1,'b',3,'2026-07-31 00:00:00','east',NULL),(9007199254740995,1,'c',5,'2026-07-31 00:00:00','east',NULL)",
        rows=data.rows
        + [
            {
                "id": identity,
                "revision": 1,
                "tenant": tenant,
                "amount": amount,
                "happened": datetime(2026, 7, 31, tzinfo=timezone.utc),
                "region": "east",
                "null_region": None,
            }
            for identity, tenant, amount in ((9007199254740994, "b", 3), (9007199254740995, "c", 5))
        ],
    )

    def forbid_snapshot(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("Ordinary C09 reads must not claim a snapshot capture")

    monkeypatch.setattr("marivo.datasource.domain_snapshot.capture", forbid_snapshot)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        author_source_project(backend, case, monkeypatch, semantic_project_factory)
        models = tmp_path / "models/semantic/sales/models.py"
        models.write_text(
            models.read_text().replace(
                "frozen = ms.finalize()",
                "region = ms.dimension_column(name='region', entity=facts, column='region')\n"
                "null_region = ms.dimension_column(name='null_region', entity=facts, column='null_region')\n"
                "frozen = ms.finalize()",
            )
        )
        session = mv.session.get_or_create("r93-attribution", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))
        fields = {
            name: session.catalog.fields.get("sales.facts." + path)
            for name, path in (
                ("channel", "bucket"),
                ("region", "region"),
                ("null_region", "null_region"),
                ("amount", "amount"),
                ("time", "happened"),
            )
        }
        facts = session.read(members, fields)
        current = facts.where(
            (facts["time"] >= datetime(2026, 8, 1, tzinfo=timezone.utc))
            & (facts["time"] < datetime(2026, 8, 2, tzinfo=timezone.utc))
        ).execute()
        baseline = facts.where(
            (facts["time"] >= datetime(2026, 7, 31, tzinfo=timezone.utc))
            & (facts["time"] < datetime(2026, 8, 1, tzinfo=timezone.utc))
        ).execute()
        source_trace.record(current)
        source_trace.record(baseline)
        current_frame, baseline_frame = plain(current), plain(baseline)
        assert (
            current_frame.amount.tolist()[0] == 2
            and pd.isna(current_frame.amount.tolist()[1])
            and current_frame.amount.tolist()[2] == 4
        )
        assert baseline_frame.amount.tolist() == [3, 5]
        cases: tuple[tuple[Literal["sum", "mean"], str], ...] = (
            ("sum", "single"),
            ("mean", "joint"),
            ("sum", "null"),
        )
        for metric_kind, axis_kind in cases:
            axes = (
                ("channel", "region")
                if axis_kind == "joint"
                else ("channel", "null_region")
                if axis_kind == "null"
                else ("channel",)
            )
            runs_before = session.runs().items
            if axis_kind == "null":
                # Nullable source Fields are legal. This explicit external recipe
                # requires a complete non-null classification for allocation.
                assert (
                    current_frame.null_region.isna().all()
                    and baseline_frame.null_region.isna().all()
                )
                with pytest.raises(ValueError, match="non-null labels"):
                    allocation(current_frame, baseline_frame, method="sum", axes=axes, parents=())
                assert session.runs().items == runs_before
                continue
            result = allocation(
                current_frame, baseline_frame, method=metric_kind, axes=axes, parents=()
            )
            divisor = 1 if metric_kind == "sum" else 2
            assert dict(zip(result.channel, result.current, strict=True)) == {
                "a": 2 / divisor,
                "b": 4 / divisor,
                "c": 0,
            }
            assert dict(zip(result.channel, result.baseline, strict=True)) == {
                "a": 0,
                "b": 3 / divisor,
                "c": 5 / divisor,
            }
            assert dict(zip(result.channel, result.contribution, strict=True)) == {
                "a": 2 / divisor,
                "b": 1 / divisor,
                "c": -5 / divisor,
            }
            if axis_kind == "joint":
                assert result.region.tolist() == ["east"] * 3
            if axis_kind == "single":
                with monkeypatch.context() as offline:

                    def forbid(*args: object, **kwargs: object) -> NoReturn:
                        pytest.fail("External pandas allocation read source")

                    offline.setattr(SourceSession, "execute", forbid)
                    restored = allocation(
                        plain(session.artifact(current.id)),
                        plain(session.artifact(baseline.id)),
                        method="sum",
                        axes=axes,
                        parents=(),
                    )
                    pd.testing.assert_frame_equal(restored, result)
                assert session.runs().items == runs_before
                assert session._runtime.store.resources(session.id) == ()
            source_trace.save(
                f"c09-source-{backend}-{metric_kind}-{axis_kind}",
                {**case.environment, "profile": profile},
                {"columns": data.columns, "values": data.values},
                {
                    "metric_kind": metric_kind,
                    "axis_kind": axis_kind,
                    "current": [2 / divisor, 4 / divisor, 0],
                    "baseline": [0, 3 / divisor, 5 / divisor],
                    "contributions": [2 / divisor, 1 / divisor, -5 / divisor],
                },
                current,
                (case.session,),
            )
