"""Temporal producer, fixed kernel and cold recovery with a fixture-owned oracle."""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from fractions import Fraction
from itertools import pairwise
from pathlib import Path
from typing import Literal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import duckdb
import ibis
import pyarrow as pa

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._temporal import (
    TemporalSnapshotStore,
    certify_period_calendar_rows,
    period_calendar_definition_digest,
)
from marivo.analysis.materialization.cell_arrow import column
from marivo.analysis.materialization.deviation_execution import _decode, load
from marivo.analysis.materialization.graph_protocol import descriptor_plan
from marivo.analysis.methods.deviation_numeric import DeviationMethod
from marivo.analysis.methods.physical import DecimalType, ScalarType, TimeShape
from marivo.datasource.adapters import SourceSession
from marivo.semantic._definition_identity import scoped_definition_fingerprint
from marivo.semantic.reader import SemanticProject
from tests.analysis.statistics.deviation_oracle import PROFILES, decimal_finish, expected, forbidden


def publish_calendar(catalog: ms.SemanticCatalog, zone: str, *, baseline: bool = False) -> None:
    ref = ms.ref.period_calendar("sales.unequal")
    calendar = catalog._require_ready().period_calendars[ref.path]
    snapshot = certify_period_calendar_rows(
        calendar_ref=ref,
        boundary_timezone=zone,
        coverage=(date(2026, 7, 26) if baseline else date(2026, 8, 1), date(2026, 8, 7)),
        columns=("calendar_date", "period"),
        retained_values=tuple(
            {
                "calendar_date": f"2026-07-{day:02}",
                "period": "xyz"[0 if day == 26 else 1 if day <= 28 else 2],
            }
            for day in range(26, 32)
            if baseline
        )
        + tuple(
            {
                "calendar_date": f"2026-08-{day:02}",
                "period": "abc"[0 if day == 1 else 1 if day <= 3 else 2],
            }
            for day in range(1, 7)
        ),
        date_column="calendar_date",
        levels={"period": "period"},
    )
    dependency = scoped_definition_fingerprint(
        root=ref,
        definitions=catalog._state.definitions,
        dependencies=catalog._state.dependencies,
        sidecar=catalog._state.sidecar,
    )
    TemporalSnapshotStore(catalog.workspace_dir).publish(
        snapshot,
        definition_digest=period_calendar_definition_digest(
            calendar_ref=ref,
            boundary_timezone=zone,
            coverage=calendar.coverage,
            levels=calendar.levels,
            correspondences=calendar.correspondences,
            dependency_digest=dependency,
        ),
    )


def verify(
    result: mv.MaterializedDeviationResult,
    index: int,
    method: DeviationMethod,
    domain: str,
    key_profile: str,
    unit: Literal["s", "ms", "us", "ns"],
    zone: str,
    route: str,
) -> None:
    assert result._dataset is not None
    retained = result._dataset.verified().parts
    inputs, state = _decode(retained)
    original, views = load(inputs.primary), load(state.views)
    values = (
        tuple(
            Decimal((0, (digit,), -int(PROFILES[index].split(",")[1][:-1]))) for digit in (1, 2, 7)
        )
        if index > 1
        else (0.1, 0.2, 0.7)
        if index
        else (1, 2, 7)
    )
    assert original.num_rows == (3 if domain == "time" else 9)
    grid = result._node.root.signature.domain.time_grid
    assert grid is not None
    dates = (1, 2, 4, 7) if grid.snapshot_digest is not None else (1, 2, 3, 4)
    boundaries = tuple(
        datetime(2026, 8, day, tzinfo=ZoneInfo(zone)).astimezone(timezone.utc) for day in dates
    )
    assert tuple((cell.start, cell.end) for cell in grid.cells) == tuple(pairwise(boundaries))
    cell_ids = tuple(cell.identity for cell in grid.cells)
    subjects = (
        ((1,), (2,), (3,))
        if key_profile == "KI"
        else (("a", 11), ("b", 12), ("c", 13))
        if key_profile == "KC"
        else (("a",), ("b",), ("c",))
    )
    expected_keys = (
        {(cell,) for cell in cell_ids}
        if domain == "time"
        else {(*subject, cell) for subject in subjects for cell in cell_ids}
        if domain == "entity_time"
        else {(category, cell) for category in "abc" for cell in cell_ids}
    )
    actual_keys = [tuple(row[name] for name in inputs.keys) for row in original.to_pylist()]
    assert len(actual_keys) == len(set(actual_keys)) and set(actual_keys) == expected_keys
    original_tags = column(original, "cell_tag").to_pylist()
    defined = [i for i, tag in enumerate(original_tags) if tag == "defined"]
    assert len(defined) == 3
    actual_values = [original["value"][i].as_py() for i in defined]
    assert sorted(actual_values) == list(values)
    center, _, _, scores = expected(values, method)
    expected_scores = dict(zip(values, scores, strict=True))
    for i in range(original.num_rows):
        if i in defined:
            value = original["value"][i].as_py()
            assert views["score__value"][i].as_py() == expected_scores[value]
            field_type = views.schema.field("reference__value").type
            reference = (
                decimal_finish(center, field_type.scale)
                if isinstance(field_type, pa.Decimal128Type)
                else float(center)
            )
            deviation = (
                decimal_finish(Fraction(value) - center, field_type.scale)
                if isinstance(field_type, pa.Decimal128Type)
                else float(Fraction(value) - center)
            )
            assert views["reference__value"][i].as_py() == reference
            assert views["deviation__value"][i].as_py() == deviation
        else:
            assert column(views, "score__cell_tag")[i].as_py() == original_tags[i]
    assert state.partitions[0].fit.n == 3
    assert "grid_cells" in {part.role for part in retained}
    if domain == "entity_time":
        assert "subject_map" in {part.role for part in retained}
        member_keys = inputs.keys[:-1]
        assert original[member_keys[0]].to_pylist().count(1 if key_profile == "KI" else "a") == 3
        if key_profile == "KC":
            assert len(member_keys) == 2 and set(original[member_keys[1]].to_pylist()) == {
                11,
                12,
                13,
            }
    actual = next(
        p.key
        for p in descriptor_plan(
            result._dataset.artifact.descriptor, result._node.definition
        ).physical_requirements
        if p.key.method.name == f"deviation.{method}"
    )
    typ = (
        DecimalType(*map(int, PROFILES[index][8:-1].split(",")))
        if index > 1
        else ScalarType("int64" if index == 0 else "float64")
    )
    assert actual.input_types == (typ,) and actual.route == route
    # Parquet stores an Arrow timestamp[s] through its millisecond logical carrier.
    assert actual.shape.time == TimeShape("instant", "ms" if unit == "s" else unit, "UTC")
    assert result._node.root.signature.domain.time_grid is not None
    assert result._node.root.signature.domain.time_grid.report_timezone == zone
    assert actual.input_domains == (("entity",) if domain == "entity_time" else ("group",))


def run(
    root: Path,
    phase: str,
    key_profile: str,
    unit: Literal["s", "ms", "us", "ns"],
    zone: str,
    grid_kind: str,
) -> None:
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    os.environ["MARIVO_TELEMETRY"] = "off"
    path = root / "deviation-time.json"
    if phase == "produce":
        catalog = ms.load(workspace_dir=root)
        if grid_kind == "calendar":
            publish_calendar(catalog, zone)
        session = mv.session.get_or_create("r82-time", report_timezone=zone)
        members = session.members(ms.ref.entity("sales.order"))
        grid = mv.time_grid(
            during=mv.time_scope(
                start="2026-08-01", end="2026-08-07" if grid_kind == "calendar" else "2026-08-04"
            ),
            grain=ms.calendar_grain(
                calendar=ms.ref.period_calendar("sales.unequal"), level="period"
            )
            if grid_kind == "calendar"
            else mv.grain("day"),
        )
        entries = []
        for index in range(len(PROFILES)):
            raw = members.observe(
                ms.ref.metric(f"sales.maximum_{index}"),
                during=grid,
                by=(mv.member(),),
            )
            assert isinstance(raw, mv.LogicalNumericRelation)
            sources: dict[str, mv.LogicalNumericRelation | mv.LogicalRolledNumericRelation] = {
                "entity_time": raw
            }
            if key_profile == "KS":
                sources["time"] = raw.group_by(grid).rollup()
                category = members.read(ms.ref.dimension("sales.order.channel"), at=grid.before_end)
                assert isinstance(category, mv.LogicalCategoryRelation)
                sources["category_time"] = raw.group_by(category, grid).rollup()
            for domain, source in sources.items():
                original = source.execute()
                for method in ("zscore", "mad"):
                    result = source.deviation(method=method).execute()
                    verify(result, index, method, domain, key_profile, unit, zone, "ibis_python")
                    entries.append(
                        {
                            "index": index,
                            "method": method,
                            "domain": domain,
                            "input": original.evidence_digest().artifact_ref.ref,
                            "result": result.evidence_digest().artifact_ref.ref,
                        }
                    )
        path.write_text(json.dumps({"session": session.id, "entries": entries}))
    else:
        manifest = json.loads(path.read_text())
        with (
            patch.object(SemanticProject, "load", forbidden),
            patch.object(ms, "load", forbidden),
            patch.object(SourceSession, "__enter__", forbidden),
            patch.object(SourceSession, "batches", forbidden),
            patch.object(duckdb, "connect", forbidden),
            patch.object(ibis.duckdb, "connect", forbidden),
        ):
            session = mv.session.resume(manifest["session"], by="id")
            for entry in manifest["entries"]:
                captured_input, recovered = (
                    session.artifact(entry["input"]),
                    session.artifact(entry["result"]),
                )
                assert isinstance(
                    captured_input,
                    (mv.MaterializedNumericRelation, mv.MaterializedGroupedNumericRelation),
                )
                original = captured_input
                assert isinstance(recovered, mv.MaterializedDeviationResult)
                verify(
                    recovered,
                    entry["index"],
                    entry["method"],
                    entry["domain"],
                    key_profile,
                    unit,
                    zone,
                    "ibis_python",
                )
                fixed = original.deviation(method=entry["method"]).execute()
                verify(
                    fixed,
                    entry["index"],
                    entry["method"],
                    entry["domain"],
                    key_profile,
                    unit,
                    zone,
                    "artifact_python",
                )
                if phase == "fixed":
                    entry["fixed"] = fixed.evidence_digest().artifact_ref.ref
                else:
                    assert fixed.evidence_digest().artifact_ref.ref == entry["fixed"]
                    cold_input = original.rank(order="ascending", ties="dense").values.execute()
                    cold_logical = cold_input.deviation(method=entry["method"])
                    cold = cold_logical.execute()
                    verify(
                        cold,
                        entry["index"],
                        entry["method"],
                        entry["domain"],
                        key_profile,
                        unit,
                        zone,
                        "artifact_python",
                    )
                    entry["cold"] = cold.evidence_digest().artifact_ref.ref
                    assert entry["cold"] != entry["fixed"]
                    runs = session.runs().items
                    assert (
                        cold_logical.execute().evidence_digest().artifact_ref.ref == entry["cold"]
                    )
                    assert session.runs().items == runs
            path.write_text(json.dumps(manifest))
    print(
        json.dumps(
            {
                "accepted": phase,
                "profiles": len(PROFILES),
                "key_profile": key_profile,
                "unit": unit,
                "zone": zone,
                "grid": grid_kind,
            }
        )
    )


if __name__ == "__main__":
    source_unit = sys.argv[4]
    assert source_unit in ("s", "ms", "us", "ns")
    unit: Literal["s", "ms", "us", "ns"] = (
        "s"
        if source_unit == "s"
        else "ms"
        if source_unit == "ms"
        else "us"
        if source_unit == "us"
        else "ns"
    )
    run(Path(sys.argv[1]), sys.argv[2], sys.argv[3], unit, sys.argv[5], sys.argv[6])
