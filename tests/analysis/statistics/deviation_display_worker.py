"""Source producer, offline fixed tables and fresh-process display recovery."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import patch

import duckdb
import ibis
from pydantic import TypeAdapter

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.methods.deviation_numeric import DeviationMethod
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.statistics.deviation_oracle import expected, forbidden
from tests.shared_fixtures import analysis_dsl_rows


@dataclass(frozen=True, slots=True)
class Manifest:
    session: str
    raw: str
    category: str
    fit: str
    other_fit: str
    ranking: str
    source_tables: tuple[str, ...]
    fixed_tables: tuple[str, ...] = ()


MANIFEST = TypeAdapter(Manifest)


def verify_table(table: mv.MaterializedTable, method: DeviationMethod) -> None:
    rows = analysis_dsl_rows("j2").orders
    scores = dict(
        zip(
            (row[0] for row in rows),
            expected(tuple(row[-1] for row in rows), method)[3],
            strict=True,
        )
    )
    frame = table.to_pandas()
    assert dict(zip(frame.member, frame.score, strict=True)) == {
        key: scores[key] for key in frame.member
    }
    if "rank" in frame.columns:
        assert sorted(frame["rank"]) == [1, 2, 3]
    assert table._dataset is not None
    assert any(part.role in ("table_fits", "fit_state") for part in table._dataset.verified().parts)


def run(root: Path, phase: str, method: DeviationMethod) -> None:
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    os.environ["MARIVO_TELEMETRY"] = "off"
    path = root / "deviation-display.json"
    if phase == "produce":
        ms.load(workspace_dir=root)
        session = mv.session.get_or_create("r82-display", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.order"))
        raw = members.read(ms.ref.measure("sales.order.amount"))
        category = members.read(ms.ref.dimension("sales.order.channel"))
        assert isinstance(category, mv.LogicalCategoryRelation)
        fit = raw.deviation(method=method)
        other_fit = raw.deviation(method="mad" if method == "zscore" else "zscore")
        ranking = fit.score.rank(order="descending", ties="dense").limit(3)
        tables = (
            mv.table(category=category, score=fit.score),
            mv.table(original=raw, score=fit.score),
            mv.table(other=other_fit.score, score=fit.score),
            mv.table(rank=ranking.ranks, score=ranking.values),
            mv.table(observed=fit.observed, score=fit.score),
        )
        saved = tuple(table.execute() for table in tables)
        for table in saved:
            verify_table(table, method)
        original, categories = raw.execute(), category.execute()
        fitted, second, ranked = fit.execute(), other_fit.execute(), ranking.execute()
        manifest = Manifest(
            session.id,
            original.evidence_digest().artifact_ref.ref,
            categories.evidence_digest().artifact_ref.ref,
            fitted.evidence_digest().artifact_ref.ref,
            second.evidence_digest().artifact_ref.ref,
            ranked.evidence_digest().artifact_ref.ref,
            tuple(table.artifact_ref.ref for table in saved),
        )
    else:
        manifest = MANIFEST.validate_json(path.read_text(), strict=True)
        with (
            patch.object(SemanticProject, "load", forbidden),
            patch.object(ms, "load", forbidden),
            patch.object(SourceSession, "__enter__", forbidden),
            patch.object(SourceSession, "batches", forbidden),
            patch.object(duckdb, "connect", forbidden),
            patch.object(ibis.duckdb, "connect", forbidden),
        ):
            session = mv.session.resume(manifest.session, by="id")
            for ref in (*manifest.source_tables, *manifest.fixed_tables):
                recovered_table = session.artifact(ref)
                assert isinstance(recovered_table, mv.MaterializedTable)
                verify_table(recovered_table, method)
            captured_raw = session.artifact(manifest.raw)
            captured_category = session.artifact(manifest.category)
            captured_fit = session.artifact(manifest.fit)
            captured_other_fit = session.artifact(manifest.other_fit)
            captured_ranking = session.artifact(manifest.ranking)
            assert isinstance(captured_raw, mv.MaterializedNumericRelation)
            assert isinstance(captured_category, mv.MaterializedCategoryRelation)
            assert isinstance(captured_fit, mv.MaterializedDeviationResult)
            assert isinstance(captured_other_fit, mv.MaterializedDeviationResult)
            assert isinstance(captured_ranking, mv.MaterializedRankingResult)
            ranks = captured_ranking.ranks
            assert dict(ranks.contract()._facts)["original_count"] == "11"
            ranks.show()
            tables = (
                mv.table(category=captured_category, score=captured_fit.score),
                mv.table(original=captured_raw, score=captured_fit.score),
                mv.table(other=captured_other_fit.score, score=captured_fit.score),
                mv.table(rank=ranks, score=captured_ranking.values),
                mv.table(observed=captured_fit.observed, score=captured_fit.score),
            )
            saved = tuple(table.execute() for table in tables)
            for table in saved:
                verify_table(table, method)
            runs = session.runs().items
            assert tuple(table.execute().artifact_ref for table in tables) == tuple(
                table.artifact_ref for table in saved
            )
            assert session.runs().items == runs
            refs = tuple(table.artifact_ref.ref for table in saved)
            if phase == "fixed":
                manifest = replace(manifest, fixed_tables=refs)
            else:
                assert refs == manifest.fixed_tables
    path.write_text(MANIFEST.dump_json(manifest).decode())
    print(f"accepted {phase} {method}")


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2], "zscore" if sys.argv[3] == "zscore" else "mad")
