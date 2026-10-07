"""Installed display and fixed-grid composition with independent fact oracles."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
from pathlib import Path

import duckdb
import ibis
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from tests.packaging.graph_journeys import _create
from tests.shared_fixtures import analysis_dsl_rows
from tests.support.json import Json, checked, read


def journey(phase: str, project: Path) -> dict[str, object]:
    """Bind source production, offline continuation and cold table reads."""
    os.environ["MARIVO_PROJECT_ROOT"] = str(project)
    state_path = project / "display.json"
    with pytest.MonkeyPatch.context() as patch:
        from marivo.datasource.adapters import SourceSession
        from marivo.datasource.runtime import DatasourceConnectionService

        def forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("installed display consulted source or current semantics")

        def forbid_sources() -> None:
            for owner, name in (
                (duckdb, "connect"),
                (ibis.duckdb, "connect"),
                (ms, "load"),
                (SourceSession, "__init__"),
                (DatasourceConnectionService, "use_backend"),
            ):
                patch.setattr(owner, name, forbidden)

        original = ibis.duckdb.connect
        patch.setattr(ibis.duckdb, "connect", lambda *a, **kw: original(*a, **{**kw, "threads": 1}))
        if phase != "produce":
            forbid_sources()
        if phase == "produce":
            session = _create(project, "j2", "parquet")
            grid = mv.time_grid(
                during=mv.time_scope(start="2026-07-01", end="2026-10-01"), grain=mv.grain("month")
            )
            domain = session.members(ms.ref.entity("operations.device")).each(grid)
            produced_values = domain.observe(
                ms.ref.metric("operations.reading_count"),
                during=grid.window,
                via=ms.ref.relationship("operations.reading_device"),
            ).execute()
            assert isinstance(produced_values, mv.MaterializedNumericRelation)
            values = produced_values
            produced_categories = domain.read(ms.ref.dimension("operations.device.zone")).execute()
            assert isinstance(produced_categories, mv.MaterializedCategoryRelation)
            categories = produced_categories
            ranking = values.rank(
                order="descending", ties="dense", partition_by=(categories,)
            ).execute()
            table = mv.table(amount=ranking.values, zone=categories, rank=ranking.ranks).execute()
            state_path.write_text(
                json.dumps(
                    {
                        "session": session.id,
                        "values": values.state.artifact_ref.ref,
                        "categories": categories.state.artifact_ref.ref,
                        "ranking": ranking.state.artifact_ref.ref,
                        "table": table.artifact_ref.ref,
                    }
                )
            )
            (project / "warehouse.duckdb").unlink()
            shutil.rmtree(project / "models")
            shutil.rmtree(project / "source_files")
        else:
            assert phase in ("fixed", "cold")
            assert not (project / "warehouse.duckdb").exists()
            assert not (project / "models").exists() and not (project / "source_files").exists()
            state = read(state_path)
            references = {}
            for name in ("session", "values", "categories", "ranking", "table"):
                reference = state[name]
                assert isinstance(reference, str)
                references[name] = reference
            session = mv.session.resume(references["session"], by="id")
            loaded_values = session.artifact(references["values"])
            loaded_categories = session.artifact(references["categories"])
            loaded_ranking = session.artifact(references["ranking"])
            loaded_table = session.artifact(references["table"])
            assert isinstance(loaded_values, mv.MaterializedNumericRelation)
            assert isinstance(loaded_categories, mv.MaterializedCategoryRelation)
            assert isinstance(loaded_ranking, mv.MaterializedRankingResult)
            assert isinstance(loaded_table, mv.MaterializedTable)
            values, categories, ranking, table = (
                loaded_values,
                loaded_categories,
                loaded_ranking,
                loaded_table,
            )
        forbid_sources()
        assert isinstance(values, mv.MaterializedNumericRelation)
        assert isinstance(categories, mv.MaterializedCategoryRelation)
        assert isinstance(ranking, mv.MaterializedRankingResult)
        assert isinstance(table, mv.MaterializedTable)
        facts = analysis_dsl_rows("j2")
        zones: dict[str, str] = {}
        for member, zone in facts.customers:
            assert isinstance(member, str) and isinstance(zone, str)
            zones[member] = zone
        counts = {
            (member, month): sum(
                buyer == member and str(day).startswith(month)
                for _, buyer, _, _, day, _ in facts.orders
            )
            for member in zones
            for month in ("2026-07", "2026-08", "2026-09")
        }
        expected_ranks = {
            key: sorted(
                {value for (member, _), value in counts.items() if zones[member] == zones[key[0]]},
                reverse=True,
            ).index(value)
            + 1
            for key, value in counts.items()
        }

        def snapshot(current: mv.MaterializedTable) -> Json:
            frame = current.to_pandas()
            assert len(frame) == 12
            for row in frame.itertuples(index=False):
                assert isinstance(row.member, str)
                key = (row.member, str(row.coord_0)[:7])
                assert row.amount == counts[key] and row.zone == zones[row.member]
                assert row.rank == expected_ranks[key]
            return checked(json.loads(frame.to_json(orient="records")))

        rows = snapshot(table)
        rebuilt = mv.table(amount=ranking.values, zone=categories, rank=ranking.ranks).execute()
        assert snapshot(rebuilt) == rows
        continuation = None
        if phase != "produce":
            continuation = (
                values.rank(order="descending", ties="dense", partition_by=(categories,))
                .limit(4)
                .execute()
                .to_pandas()
                .to_json(orient="records")
            )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            table.show(n=2)
        text = output.getvalue()
        assert len(text.encode()) <= 8192 and "12 total; 2 shown" in text
        assert "Omitted: 10 rows; reason=row_limit" in text and text.count("<identity>") == 2
        assert "j2_" not in text and "call=" not in text
        return {
            "pid": os.getpid(),
            "session": session.id,
            "rows": rows,
            "display": text,
            "continuation": continuation,
            "run_count": len(session.runs().items),
        }


if __name__ == "__main__":
    from tests.packaging.wheel_probe import assert_installed_origin

    assert_installed_origin()
    report = journey(sys.argv[1], Path(sys.argv[2]))
    report["origin"] = assert_installed_origin()
    Path(sys.argv[3]).write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
