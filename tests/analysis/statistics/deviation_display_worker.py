"""Shared source producer, offline fixed tables and fresh-process display recovery."""

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

DISPLAY_METHODS: tuple[DeviationMethod, ...] = ("zscore", "mad")


@dataclass(frozen=True, slots=True)
class MethodManifest:
    method: DeviationMethod
    fit: str
    other_fit: str
    ranking: str
    producer_tables: tuple[str, ...]
    fixed_tables: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Manifest:
    session: str
    raw: str
    category: str
    methods: tuple[MethodManifest, ...]


MANIFEST = TypeAdapter(Manifest)


def other_method(method: DeviationMethod) -> DeviationMethod:
    return "mad" if method == "zscore" else "zscore"


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
    assert any(
        part.role in ("table_fits", "fit_state")
        for part in table._dataset.artifact.descriptor.parts
    )


def run(root: Path, phase: str) -> None:
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
        original, categories = raw.execute(), category.execute()
        fits = {method: raw.deviation(method=method).execute() for method in DISPLAY_METHODS}
        rankings = {
            method: fits[method].score.rank(order="descending", ties="dense").limit(3).execute()
            for method in DISPLAY_METHODS
        }
        method_manifests = []
        for method in DISPLAY_METHODS:
            fit = fits[method]
            other_fit = fits[other_method(method)]
            ranking = rankings[method]
            tables = (
                mv.table(category=categories, score=fit.score),
                mv.table(original=original, score=fit.score),
                mv.table(other=other_fit.score, score=fit.score),
                mv.table(rank=ranking.ranks, score=ranking.values),
                mv.table(observed=fit.observed, score=fit.score),
            )
            saved = tuple(table.execute() for table in tables)
            for table in saved:
                verify_table(table, method)
            method_manifests.append(
                MethodManifest(
                    method,
                    fit.evidence_digest().artifact_ref.ref,
                    other_fit.evidence_digest().artifact_ref.ref,
                    ranking.evidence_digest().artifact_ref.ref,
                    tuple(table.artifact_ref.ref for table in saved),
                )
            )
        manifest = Manifest(
            session.id,
            original.evidence_digest().artifact_ref.ref,
            categories.evidence_digest().artifact_ref.ref,
            tuple(method_manifests),
        )
    else:
        manifest = MANIFEST.validate_json(path.read_text(), strict=True)
        with (
            patch.object(SemanticProject, "load", forbidden),
            patch.object(ms, "load", forbidden),
            patch.object(SourceSession, "__enter__", forbidden),
            patch.object(SourceSession, "execute", forbidden),
            patch.object(duckdb, "connect", forbidden),
            patch.object(ibis.duckdb, "connect", forbidden),
        ):
            session = mv.session.resume(manifest.session, by="id")
            table_refs = tuple(
                ref
                for item in manifest.methods
                for ref in (*item.producer_tables, *item.fixed_tables)
            )
            for ref in table_refs:
                recovered_table = session.artifact(ref)
                assert isinstance(recovered_table, mv.MaterializedTable)
                owner = next(
                    item.method
                    for item in manifest.methods
                    if ref in (*item.producer_tables, *item.fixed_tables)
                )
                verify_table(recovered_table, owner)
            captured_raw = session.artifact(manifest.raw)
            captured_category = session.artifact(manifest.category)
            assert isinstance(captured_raw, mv.MaterializedNumericRelation)
            assert isinstance(captured_category, mv.MaterializedCategoryRelation)
            captured_fits: dict[DeviationMethod, mv.MaterializedDeviationResult] = {}
            captured_rankings: dict[DeviationMethod, mv.MaterializedRankingResult] = {}
            for item in manifest.methods:
                restored_fit = session.artifact(item.fit)
                restored_other_fit = session.artifact(item.other_fit)
                restored_ranking = session.artifact(item.ranking)
                assert isinstance(restored_fit, mv.MaterializedDeviationResult)
                assert isinstance(restored_other_fit, mv.MaterializedDeviationResult)
                assert isinstance(restored_ranking, mv.MaterializedRankingResult)
                captured_fits[item.method] = restored_fit
                captured_rankings[item.method] = restored_ranking
                ranks = restored_ranking.ranks
                assert dict(ranks.contract()._facts)["original_count"] == "11"
                ranks.show()
            runs = session.runs().items
            updated_methods = []
            for item in manifest.methods:
                fit = captured_fits[item.method]
                other_fit = captured_fits[other_method(item.method)]
                ranking = captured_rankings[item.method]
                tables = (
                    mv.table(score=fit.score, category=captured_category),
                    mv.table(score=fit.score, original=captured_raw),
                    mv.table(score=fit.score, other=other_fit.score),
                    mv.table(score=ranking.values, rank=ranking.ranks),
                    mv.table(score=fit.score, observed=fit.observed),
                )
                saved = tuple(table.execute() for table in tables)
                refs = tuple(table.artifact_ref.ref for table in saved)
                if phase == "fixed":
                    assert set(refs).isdisjoint(item.producer_tables)
                    for table in saved:
                        verify_table(table, item.method)
                    assert tuple(table.execute().artifact_ref for table in tables) == tuple(
                        table.artifact_ref for table in saved
                    )
                    updated_methods.append(replace(item, fixed_tables=refs))
                else:
                    assert refs == item.fixed_tables
                    assert session.runs().items == runs
                    updated_methods.append(item)
            if phase == "fixed":
                manifest = replace(manifest, methods=tuple(updated_methods))
            else:
                assert session.runs().items == runs
    path.write_bytes(MANIFEST.dump_json(manifest))
    print(f"accepted {phase} display pair")


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2])
