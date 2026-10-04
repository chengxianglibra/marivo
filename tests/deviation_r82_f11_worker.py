"""Actual F11 source DAGs with known contributions and source-prefix auditing."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Iterator
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pyarrow as pa

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._temporal import TimeScope
from marivo.analysis.core.graph import MethodNode
from marivo.analysis.core.rules import DeviationFit
from marivo.analysis.materialization import deviation_execution as scoring
from marivo.analysis.materialization.graph_exchange import ExchangeResult
from marivo.analysis.materialization.graph_protocol import descriptor_plan, receipt_digest
from marivo.analysis.methods.deviation_numeric import DeviationMethod
from marivo.analysis.methods.physical import QualificationKey, TimeShape
from marivo.datasource.adapters import SourceBatchStream
from scripts.r81_static_freeze import Json
from scripts.r82_deviation_requirements import key_json
from tests.deviation_r82_oracle import PROFILES, decimal_finish, expected
from tests.deviation_r82_time_worker import publish_calendar

METHODS: tuple[DeviationMethod, ...] = ("zscore", "mad")


def accept_chain(
    current: mv.LogicalNumericRelation,
    baseline: mv.LogicalNumericRelation,
    grid: mv.TimeGrid,
    window: TimeScope,
    raw: tuple[int | float | Decimal, ...],
    method: DeviationMethod,
    index: int,
    profile: str,
    unit: Literal["s", "ms", "us", "ns"],
    zone: str,
    domain: str,
) -> dict[str, Json]:
    metric = ms.ref.metric(f"sales.total_{index}")
    changed = (
        current.compare(baseline, design=mv.PeriodChange(alignment=mv.window_bucket()))
        if domain == "entity_time"
        else current.compare(baseline)
    )
    scored = changed.deviation(method=method)
    defined = scored.where(scored.score.value.is_defined())
    positive = defined.where(defined.score.value.gt(0))
    selected = positive.observed.members()
    assert isinstance(selected, mv.LogicalAnalysisDomain)
    observed = (
        selected.each(grid).observe(metric, during=grid.window)
        if domain == "entity_time"
        else selected.observe(metric, during=window)
    )
    assert isinstance(observed, mv.LogicalNumericRelation)
    trace: list[str] = []
    fits: list[dict[str, Json]] = []
    next_batch, execute = (
        SourceBatchStream._iterate,
        scoring.execute,
    )

    def read(stream: SourceBatchStream) -> Iterator[pa.RecordBatch]:
        for batch in next_batch(stream):
            trace.append("source_read")
            yield batch

    def score(node: MethodNode, inputs: tuple[ExchangeResult, ...], binding: str) -> ExchangeResult:
        if isinstance(node.parameters, DeviationFit):
            trace.append("local_consume")
        result = execute(node, inputs, binding)
        if isinstance(node.parameters, DeviationFit):
            original, state = scoring._decode(result.parts)
            rows, views = scoring.load(original.primary), scoring.load(state.views)
            valid = [i for i, tag in enumerate(rows["cell_tag"].to_pylist()) if tag == "defined"]
            fit_values = (*raw, *((raw[0] * 0,) * 6)) if domain == "entity_time" else raw
            assert len(valid) == len(fit_values) and rows.num_rows == len(fit_values)
            assert sorted(rows["value"][i].as_py() for i in valid) == sorted(fit_values)
            center, scale, branch, scores = expected(fit_values, method)
            fitted = state.partitions[0].fit
            assert fitted.center is not None and fitted.raw_scale is not None
            assert (fitted.center.value(), fitted.raw_scale.value(), fitted.branch) == (
                center,
                scale,
                branch,
            )
            expected_scores = dict(zip(fit_values, scores, strict=True))
            field_type = views.schema.field("reference__value").type
            for i in valid:
                value = rows["value"][i].as_py()
                assert views["score__value"][i].as_py() == expected_scores[value]
                assert views["reference__value"][i].as_py() == (
                    decimal_finish(center, field_type.scale)
                    if isinstance(field_type, pa.Decimal128Type)
                    else float(center)
                )
                delta = Fraction(value) - center
                assert views["deviation__value"][i].as_py() == (
                    decimal_finish(delta, field_type.scale)
                    if isinstance(field_type, pa.Decimal128Type)
                    else float(delta)
                )
            fits.append(
                {
                    "input_type": node.parameters.input_type,
                    "fit_scope": node.parameters.fit_id,
                    "input_digest": state.input_digest,
                    "parts": {
                        p.role: hashlib.sha256(scoring.save(p.table).data.encode()).hexdigest()
                        for p in result.parts
                    },
                }
            )
        return result

    with (
        patch.object(SourceBatchStream, "_iterate", read),
        patch.object(scoring, "execute", score),
    ):
        followup = observed.execute()
        values = followup.to_pandas()
        assert followup._dataset is not None
        checked = followup._dataset.verified()
        retained_grid = followup._node.root.signature.domain.time_grid
        selected_indices = (
            (1, 2)
            if domain == "entity_time" and method == "zscore"
            else (0, 1, 2)
            if domain == "entity_time"
            else (2,)
        )
        wanted: dict[tuple[str | int, ...], int | float | Decimal] = {}
        for selected_index in selected_indices:
            member = selected_index + 1 if profile == "KI" else "abc"[selected_index]
            prefix = (member, selected_index + 11) if profile == "KC" else (member,)
            if retained_grid is not None:
                for cell_index, cell in enumerate(retained_grid.cells):
                    wanted[(*prefix, cell.identity)] = (
                        raw[selected_index] if cell_index == selected_index else raw[0] * 0
                    )
            else:
                wanted[prefix] = raw[selected_index]
        keys = checked.contract.key_fields
        actual_rows = checked.primary.to_pylist()
        assert (
            len(actual_rows) == len(wanted)
            and {tuple(row[key] for key in keys): row["value"] for row in actual_rows} == wanted
        )
        assert values.cell_tag.tolist() == ["defined"] * len(wanted)
        components = next(p.table for p in checked.parts if p.role == "original_state")
        assert {
            tuple(row[key] for key in keys): row["original_state__sum"]
            for row in components.to_pylist()
        } == wanted
        assert sorted(components["original_state__non_null_count"].to_pylist()) == [0] * (
            len(wanted) - len(selected_indices)
        ) + [1] * len(selected_indices)
        assert len(fits) == 1 and "local_consume" in trace
        assert max(i for i, event in enumerate(trace) if event == "source_read") < trace.index(
            "local_consume"
        )
        first_trace = tuple(trace)
        trace.clear()
        fits.clear()
        summary = observed.summarize(method=mv.count_defined()).execute()
        assert summary.to_pandas().value.tolist() == [len(wanted)]
        assert len(fits) == 1
        assert max(i for i, event in enumerate(trace) if event == "source_read") < trace.index(
            "local_consume"
        )
    assert summary._dataset is not None
    plan = descriptor_plan(summary._dataset.artifact.descriptor, summary._node.definition)
    actual: QualificationKey = next(
        p.key for p in plan.physical_requirements if p.key.method.name == f"deviation.{method}"
    )
    if domain == "entity_time":
        assert actual.shape.time == TimeShape("instant", unit, zone)
    return {
        "method": method,
        "contribution_type": PROFILES[index],
        "actual": key_json(actual),
        "domain": domain,
        "key_profile": profile,
        "followup_artifact": followup.evidence_digest().artifact_ref.ref,
        "summary_artifact": summary.evidence_digest().artifact_ref.ref,
        "summary_run": summary._dataset.artifact.producing_run_ref,
        "primary_receipt": receipt_digest(summary._dataset.artifact.descriptor.primary_receipt),
        "source_read_count": first_trace.count("source_read"),
        "local_count": first_trace.count("local_consume"),
        "last_source_read": max(i for i, event in enumerate(first_trace) if event == "source_read"),
        "first_local_consume": first_trace.index("local_consume"),
        "fit": fits[0],
    }


def run(
    root: Path,
    profile: str,
    unit: Literal["s", "ms", "us", "ns"],
    zone: str,
    domain: str,
    calendar: bool,
) -> None:
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    os.environ["MARIVO_TELEMETRY"] = "off"
    catalog = ms.load(workspace_dir=root)
    if calendar:
        publish_calendar(catalog, zone, baseline=True)
    session = mv.session.get_or_create("r82-f11", report_timezone=zone)
    members = session.members(ms.ref.entity("sales.order"))
    window = mv.time_scope(start="2026-08-01", end="2026-08-07" if calendar else "2026-08-04")
    grid = mv.time_grid(
        during=window,
        grain=ms.calendar_grain(calendar=ms.ref.period_calendar("sales.unequal"), level="period")
        if calendar
        else mv.grain("day"),
    )
    baseline_window = mv.time_scope(
        start="2026-07-26" if calendar else "2026-07-29", end="2026-08-01"
    )
    baseline_grid = mv.time_grid(
        during=baseline_window,
        grain=ms.calendar_grain(calendar=ms.ref.period_calendar("sales.unequal"), level="period")
        if calendar
        else mv.grain("day"),
    )
    entries: list[Json] = []
    for index in range(len(PROFILES)):
        receiver = members.each(grid) if domain == "entity_time" else members
        during = grid.window if domain == "entity_time" else window
        metric = ms.ref.metric(f"sales.total_{index}")
        current = receiver.observe(metric, during=during)
        baseline_receiver = members.each(baseline_grid) if domain == "entity_time" else members
        baseline = baseline_receiver.observe(
            metric, during=baseline_grid.window if domain == "entity_time" else baseline_window
        )
        assert isinstance(current, mv.LogicalNumericRelation)
        assert isinstance(baseline, mv.LogicalNumericRelation)
        raw = (
            tuple(
                Decimal((0, (digit,), -int(PROFILES[index].split(",")[1][:-1])))
                for digit in (1, 2, 7)
            )
            if index > 1
            else (0.1, 0.2, 0.7)
            if index
            else (1, 2, 7)
        )
        for method in METHODS:
            entries.append(
                accept_chain(
                    current, baseline, grid, window, raw, method, index, profile, unit, zone, domain
                )
            )
    (root / "r82-f11.json").write_text(json.dumps({"session": session.id, "entries": entries}))
    print(json.dumps({"accepted": "source_f11", "entries": len(entries)}))


if __name__ == "__main__":
    source_unit = sys.argv[3]
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
    run(Path(sys.argv[1]), sys.argv[2], unit, sys.argv[4], sys.argv[5], sys.argv[6] == "calendar")
