"""Public comparison and attribution journeys with raw-fact oracles and source-free fixed continuations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict, replace
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Literal

import duckdb
import ibis
import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import graph_protocol
from marivo.analysis.public_dsl import _MaterializedValue
from tests.packaging.graph_journeys import NAMES
from tests.shared_fixtures import (
    DslCase,
    DslRows,
    analysis_dsl_project_files,
    analysis_dsl_rows,
    export_dsl_parquet_models,
    seed_analysis_dsl_database,
)

Scenario = Literal["a02", "a06", "a07", "a08"]
Saved = _MaterializedValue | mv.MaterializedTable


def _facts() -> DslRows:
    original = analysis_dsl_rows("j2")
    return DslRows(
        original.customers,
        tuple(
            (
                key,
                member,
                "Other"
                if member == "A" and day.startswith("2026-07")
                else "app"
                if member == "B" and day.startswith("2026-08")
                else channel,
                status,
                day,
                amount,
            )
            for key, member, channel, status, day, amount in original.orders
        ),
        original.lines,
    )


def _create(project: Path, source: str) -> tuple[mv.Session, DslCase]:
    project.mkdir()
    database = project / "warehouse.duckdb"
    seed_analysis_dsl_database(database, NAMES, _facts(), float_amount=False)
    (project / "marivo.toml").write_text('[project]\nname = "installed-relations"\n')
    for relative, content in analysis_dsl_project_files(
        NAMES, database, revenue_unit="kWh"
    ).items():
        if relative == "operations/models.py":
            content += """
running_energy = ms.cumulative(name='running_energy', base=revenue)
status_energy = ms.measure_column(name='status_energy', entity=orders, column='energy',
    additivity=ms.additive_all(except_=(ordered_at,)),
    status_time_dimension=ordered_at, status_time_fold='max', unit='kWh')
folded_energy = ms.aggregate(name='folded_energy', measure=status_energy, agg='sum')
"""
        path = (
            project
            / "models"
            / (relative if relative.startswith("datasources/") else f"semantic/{relative}")
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.replace("unit='CNY'", "unit='kWh'"))
    catalog = ms.load(workspace_dir=project)
    session = mv.session.get_or_create("installed-relations", report_timezone="UTC")
    case = DslCase("j2", NAMES, project, database, catalog, session)
    if source == "parquet":
        export_dsl_parquet_models(case, project)
        ms.load(workspace_dir=project)
    return session, case


def _oracle() -> dict[str, object]:
    facts = _facts()
    amounts: dict[str, dict[str, int]] = {}
    counts: dict[str, dict[str, int]] = {}
    for month in ("2026-06", "2026-07", "2026-08", "2026-09", "2026-10"):
        amounts[month] = {
            str(member): sum(
                int(amount)
                for _, buyer, _, _, day, amount in facts.orders
                if buyer == member and day.startswith(month)
            )
            for member, _ in facts.customers
        }
        counts[month] = {
            str(member): sum(
                buyer == member and day.startswith(month) for _, buyer, _, _, day, _ in facts.orders
            )
            for member, _ in facts.customers
        }
    current, baseline = amounts["2026-08"], amounts["2026-07"]
    differences = {key: value - baseline[key] for key, value in current.items()}
    selected = sorted(key for key, value in differences.items() if value < 0)
    total = sum(current.values())
    grouped = {
        str(region): sum(
            counts["2026-08"][str(member)] for member, label in facts.customers if label == region
        )
        for _, region in facts.customers
    }
    return {
        "amounts": amounts,
        "counts": counts,
        "difference": differences,
        "selected": selected,
        "next_observation": {key: amounts["2026-09"][key] for key in selected},
        "share": {key: float(Fraction(value, total)) for key, value in current.items()},
        "denominator": total,
        "standardized": float(
            sum(
                (Fraction(value * value, sum(grouped.values())) for value in grouped.values()),
                Fraction(),
            )
        ),
        "cohort": sorted(
            key
            for key in current
            if sum(counts[month][key] > 0 for month in ("2026-07", "2026-08", "2026-09")) >= 3
        ),
    }


def _snapshot(value: Saved) -> dict[str, object]:
    dataset = value._dataset
    assert dataset is not None
    verified = dataset.verified()
    parts = []
    for part in verified.parts:
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, part.table.schema) as writer:
            writer.write_table(part.table)
        parts.append(
            {
                "role": part.role,
                "schema": str(part.table.schema),
                "rows": part.table.num_rows,
                "sha256": hashlib.sha256(sink.getvalue().to_pybytes()).hexdigest(),
            }
        )
    descriptor = dataset.artifact.descriptor
    return {
        "artifact": dataset.artifact.artifact_ref,
        "run": descriptor.producing_run_ref,
        "type": type(value).__name__,
        "rows": json.loads(value.to_pandas().to_json(orient="table", index=False)),
        "descriptor": json.loads(graph_protocol.encode(descriptor, graph_protocol.DESCRIPTOR)),
        "parts": parts,
        "contract": None
        if isinstance(value, mv.MaterializedTable)
        else json.loads(json.dumps(asdict(value.contract()))),
    }


def _numeric(value: Saved) -> _MaterializedValue:
    assert isinstance(value, _MaterializedValue)
    return value


def _mapping(value: Saved) -> dict[str, object]:
    frame = value.to_pandas()
    return dict(zip(frame.member, frame.value, strict=True))


def _assert_oracles(saved: dict[str, Saved], scenario: Scenario) -> None:
    """Check every journey's values from facts, independently of saved snapshots."""
    facts = _facts()
    members = tuple(str(member) for member, _ in facts.customers)
    months = ("2026-07", "2026-08", "2026-09")

    def amount(member: str, month: str) -> Decimal:
        return sum(
            (
                Decimal(str(value))
                for _, buyer, _, _, day, value in facts.orders
                if buyer == member and day.startswith(month)
            ),
            Decimal(),
        )

    def count(member: str, month: str, channel: str | None = None) -> int:
        return sum(
            buyer == member and day.startswith(month) and (channel is None or sensor == channel)
            for _, buyer, sensor, _, day, _ in facts.orders
        )

    def cells(name: str, expected: dict[tuple[str, ...], tuple[str, object]], *keys: str) -> None:
        rows = saved[name].to_pandas().to_dict(orient="records")
        actual = {
            tuple(str(row[key])[:7] if key == "coord_0" else str(row[key]) for key in keys): row
            for row in rows
        }
        assert len(actual) == len(rows) and actual.keys() == expected.keys(), name
        for key, (tag, value) in expected.items():
            row = actual[key]
            assert row["cell_tag"] == tag, (name, key, row)
            if tag == "defined":
                assert row["value"] == value and row["cell_reason"] is None, (name, key, row, value)
            else:
                assert row["value"] is None or row["value"] != row["value"], (name, key, row)

    if scenario in ("a02", "a06"):
        for name, month in (("current", months[1]), ("baseline", months[0])):
            cells(name, {(m,): ("defined", amount(m, month)) for m in members}, "member")
    if scenario == "a02":
        cells(
            "difference",
            {(m,): ("defined", amount(m, months[1]) - amount(m, months[0])) for m in members},
            "member",
        )
        selected = {m for m in members if amount(m, months[1]) < amount(m, months[0])}
        assert set(saved["members"].to_pandas().member) == selected
        cells("next", {(m,): ("defined", amount(m, months[2])) for m in selected}, "member")
    elif scenario == "a06":
        cells(
            "relative",
            {
                (m,): (
                    "defined",
                    float(
                        Fraction(amount(m, months[1]) - amount(m, months[0]))
                        / abs(Fraction(amount(m, months[0])))
                    ),
                )
                if amount(m, months[0])
                else ("undefined", None)
                for m in members
            },
            "member",
        )
        cells(
            "union",
            {
                (m,): ("defined", amount(m, months[1]) - amount(m, months[0]))
                if amount(m, months[0]) > 80
                else ("undefined", None)
                for m in members
            },
            "member",
        )
        cells(
            "nested",
            {
                (m,): (
                    "defined",
                    count(m, months[1]) - 2 * count(m, months[0]) + count(m, "2026-06"),
                )
                for m in members
            },
            "member",
        )
        cells(
            "ratio",
            {
                (m,): ("defined", float(Fraction(amount(m, months[1])) / count(m, months[1])))
                for m in members
            },
            "member",
        )
        cells(
            "branch_ratio",
            {
                (m,): ("defined", float(Fraction(count(m, months[1], "web"), count(m, months[1]))))
                for m in members
            },
            "member",
        )
        cells(
            "shared_ratio",
            {
                (m,): ("defined", 1.0) if amount(m, months[1]) else ("undefined", None)
                for m in members
            },
            "member",
        )
        cells(
            "negative_relative",
            {
                (m,): (
                    "defined",
                    float(
                        Fraction(2 * (amount(m, months[1]) - amount(m, months[0])))
                        / abs(Fraction(amount(m, months[0]) - amount(m, months[1])))
                    ),
                )
                if amount(m, months[0]) != amount(m, months[1])
                else ("undefined", None)
                for m in members
            },
            "member",
        )
        cells(
            "period",
            {
                (m, left): ("defined", count(m, left) - count(m, right))
                for m in members
                for left, right in (("2026-09", "2026-07"), ("2026-10", "2026-08"))
            },
            "member",
            "coord_0",
        )
        cells(
            "running",
            {
                (m, month): (
                    "defined",
                    sum((amount(m, earlier) for earlier in months[: i + 1]), Decimal()),
                )
                for m in members
                for i, month in enumerate(months)
            },
            "member",
            "coord_0",
        )
        cells(
            "folded",
            {
                (m, month): ("defined", amount(m, month)) if count(m, month) else ("null", None)
                for m in members
                for month in months
            },
            "member",
            "coord_0",
        )
    elif scenario == "a07":
        total = sum((amount(m, months[1]) for m in members), Decimal())
        cells("counts", {(m,): ("defined", count(m, months[1])) for m in members}, "member")
        cells(
            "difference",
            {
                (): (
                    "defined",
                    sum((amount(m, months[1]) - amount(m, months[0]) for m in members), Decimal()),
                )
            },
        )
        cells(
            "share",
            {
                (m,): ("defined", float(Fraction(amount(m, months[1])) / Fraction(total)))
                for m in members
            },
            "member",
        )
        cells(
            "ranking",
            {
                (m,): ("defined", float(Fraction(amount(m, months[1])) / Fraction(total)))
                for m in members
            },
            "member",
        )
        ordered = sorted(members, key=lambda m: (-amount(m, months[1]), m))
        ranking = saved["ranking"]
        assert isinstance(ranking, mv.MaterializedRankingResult)
        assert ranking.to_pandas().member.tolist() == ordered
        assert ranking.ranks.to_pandas().value.tolist() == [
            1 + sum(amount(other, months[1]) > amount(m, months[1]) for other in members)
            for m in ordered
        ]
        assert saved["limited"].to_pandas().member.tolist() == ordered[:1]
        group_counts = {
            str(zone): sum(count(str(m), months[1]) for m, z in facts.customers if z == zone)
            for _, zone in facts.customers
        }
        denominator = sum(group_counts.values())
        cells("groups", {(g,): ("defined", n) for g, n in group_counts.items()}, "group")
        cells(
            "weights",
            {(g,): ("defined", float(Fraction(n, denominator))) for g, n in group_counts.items()},
            "group",
        )
        cells(
            "standardized",
            {
                (): (
                    "defined",
                    float(
                        sum(
                            (Fraction(n * n, denominator) for n in group_counts.values()),
                            Fraction(),
                        )
                    ),
                )
            },
        )
        cells(
            "penetration",
            {
                (): (
                    "defined",
                    float(Fraction(sum(count(m, months[1]) > 0 for m in members), len(members))),
                )
            },
        )
        sides = {
            month: {
                sensor: sum(
                    (
                        Decimal(str(value))
                        for _, _, s, _, day, value in facts.orders
                        if s == sensor and day.startswith(month)
                    ),
                    Decimal(),
                )
                for _, _, sensor, _, _, _ in facts.orders
            }
            for month in months[:2]
        }
        channels = sorted(sides[months[0]])
        channels.sort(
            key=lambda s: abs(sides[months[0]][s]) + abs(sides[months[1]][s]), reverse=True
        )
        first = channels[0]
        expected = {
            (first, 0): (sides[months[1]][first], sides[months[0]][first]),
            (None, 1): tuple(
                sum((sides[month][s] for s in channels[1:]), Decimal())
                for month in (months[1], months[0])
            ),
        }
        table_rows = saved["table"].to_pandas().to_dict(orient="records")
        assert len(table_rows) == len(expected)
        for row in table_rows:
            current, baseline = expected[(row["coord_0"], row["coord_1"])]
            assert (row["current"], row["baseline"], row["contribution"]) == (
                current,
                baseline,
                current - baseline,
            )
        for name in ("attribution", "selected"):
            rows = saved[name].to_pandas().to_dict(orient="records")
            admitted = {
                key: left - right
                for key, (left, right) in expected.items()
                if name == "attribution" or left - right > 0
            }
            assert len(rows) == len(admitted)
            for row in rows:
                assert row["cell_tag"] == "defined"
                assert row["value"] == admitted[(row["coord_0"], row["coord_1"])]
        mix = saved["component_mix"]
        assert isinstance(mix, mv.MaterializedAttributionResult)
        assert dict(mix.contract()._facts)["allocation_method"] == "component_mix"
        mix_channels = sorted(channels)
        mix_channels.sort(
            key=lambda sensor: sum(
                day.startswith(month) and channel == sensor
                for _, _, channel, _, day, _ in facts.orders
                for month in months[:2]
            ),
            reverse=True,
        )
        mix_first = mix_channels[0]
        expected_mix = {
            (mix_first, 0): tuple(
                Fraction(sides[month][mix_first]) / sum(count(m, month) for m in members)
                for month in (months[1], months[0])
            ),
            (None, 1): tuple(
                sum((Fraction(sides[month][s]) for s in mix_channels[1:]), Fraction())
                / sum(count(m, month) for m in members)
                for month in (months[1], months[0])
            ),
        }
        for view, position in ((mix.current, 0), (mix.baseline, 1), (mix.contribution, 2)):
            rows = view.to_pandas().to_dict(orient="records")
            assert len(rows) == len(expected_mix)
            for row in rows:
                left, right = expected_mix[(row["coord_0"], row["coord_1"])]
                assert row["cell_tag"] == "defined"
                assert row["value"] == float((left, right, left - right)[position])
    else:
        cells(
            "values",
            {(m, month): ("defined", count(m, month)) for m in members for month in months},
            "member",
            "coord_0",
        )
        for name, decision in (
            ("cohort", lambda n: n >= 3),
            ("any", lambda n: n > 0),
            ("all", lambda n: n == len(months)),
        ):
            assert set(saved[name].to_pandas().member) == {
                m for m in members if decision(sum(count(m, month) > 0 for month in months))
            }


def _decision_edges(saved: dict[str, Saved]) -> dict[str, bool]:
    """Exercise existing Cells at the fixed consumer boundary, without a new producer."""
    from marivo.analysis.compiler.graph_lowering import LoweredLocal, canonical_layout
    from marivo.analysis.compiler.graph_plan import LocalMethodStage
    from marivo.analysis.materialization.graph_local_execution import _cohort_stage
    from marivo.analysis.methods.builtin import implementations
    from marivo.analysis.methods.semantics import MethodKey

    targets, values = saved["targets"], saved["controlled_values"]
    assert isinstance(targets, mv.MaterializedAnalysisDomain)
    assert isinstance(values, mv.MaterializedNumericRelation)
    assert targets._dataset is not None and values._dataset is not None
    target, supplied = targets._dataset.verified(), values._dataset.verified()
    node = targets.cohort(
        values.value.gt(0), rule=mv.at_least(3), through=values.subject_binding
    )._node.root
    implementation = next(
        item
        for item in implementations(MethodKey("domain.cohort"))
        if item.key.route == "artifact_python"
    )
    stage = LoweredLocal(
        LocalMethodStage("cohort", (), node, implementation),
        (),
        canonical_layout(node.signature, has_value=False),
    )
    grid = values._node.root.signature.domain.time_grid
    assert grid is not None
    cells = tuple(cell.identity for cell in grid.cells)
    checks = {}
    for name, tags, payloads, expected in (
        ("decidable_unknown_true", ("defined",) * 3 + ("unknown",), (1, 1, 1, None), True),
        (
            "decidable_unknown_false",
            ("defined", "unknown", "defined", "defined"),
            (1, None, 0, 0),
            False,
        ),
        ("undecidable_unknown", ("defined",) * 3 + ("unknown",), (1, 1, 0, None), None),
        ("undefined_hard_failure", ("defined",) * 3 + ("undefined",), (1, 1, 1, None), None),
    ):
        rows = supplied.primary.to_pylist()
        for row in rows:
            index = cells.index(row["key_1"])
            row.update(
                value=payloads[index],
                cell_tag=tags[index],
                cell_reason=None if tags[index] == "defined" else "controlled_consumer",
            )
        controlled = replace(
            supplied, primary=pa.Table.from_pylist(rows, schema=supplied.primary.schema)
        )
        if expected is None:
            with pytest.raises(AnalysisError, match=r"undecidable|Cell tag undefined"):
                _cohort_stage(stage, target, (controlled,), "controlled")
            checks[name] = True
        else:
            result = _cohort_stage(stage, target, (controlled,), "controlled")
            assert len(result.primary) == (len(target.primary) if expected else 0)
            decision = next(part.table for part in result.parts if part.role == "cohort_decision")
            assert set(decision["cohort_decision__accepted"].to_pylist()) == {expected}
            checks[name] = True
    return checks


def _produce(
    session: mv.Session, scenario: Scenario, shared_nodes: dict[str, int]
) -> dict[str, Saved]:
    targets = session.members(ms.ref.entity("operations.device"))
    metric = ms.ref.metric("operations.energy_total")
    count = ms.ref.metric("operations.reading_count")
    via = ms.ref.relationship("operations.reading_device")
    august = mv.time_scope(start="2026-08-01", end="2026-09-01")
    july = mv.time_scope(start="2026-07-01", end="2026-08-01")
    current = targets.observe(metric, during=august, via=via)
    baseline = targets.observe(metric, during=july, via=via)
    oracle = _oracle()
    saved: dict[str, Saved] = {}
    if scenario == "a02":
        change = current.compare(baseline)
        saved["difference"] = change.execute()
        selected = change.where(change.value.lt(0)).members()
        saved["members"] = selected.execute()
        next_values = selected.observe(
            metric, during=mv.time_scope(start="2026-09-01", end="2026-10-01"), via=via
        ).execute()
        assert _mapping(next_values) == oracle["next_observation"]
        saved["next"] = next_values
        saved["current"], saved["baseline"] = current.execute(), baseline.execute()
    elif scenario == "a06":
        saved["relative"] = current.compare(baseline, value="relative_change").execute()
        saved["negative_relative"] = (
            current.compare(baseline)
            .compare(baseline.compare(current), value="relative_change")
            .execute()
        )
        saved["union"] = current.compare(
            baseline.where(baseline.value.gt(80)),
            design=mv.TimeChange(pairing=mv.UnionKeys(missing="keep")),
        ).execute()
        counts = targets.observe(count, during=august, via=via)
        previous = targets.observe(count, during=july, via=via)
        earlier = targets.observe(
            count, during=mv.time_scope(start="2026-06-01", end="2026-07-01"), via=via
        )
        saved["nested"] = counts.compare(previous).compare(previous.compare(earlier)).execute()
        saved["ratio"] = current.ratio(counts).execute()
        branch = mv.runtime_metric.ratio(
            mv.runtime_metric.slice(
                count,
                by={ms.ref.dimension("operations.reading.sensor"): "web"},
                label="web readings",
            ),
            count,
            label="web readings per all readings",
        )
        saved["branch_ratio"] = targets.observe(branch, during=august, via=via).execute()
        from marivo.datasource.adapters import SourceSession

        stage = SourceSession.stage_derived
        captures = []

        def counted_stage(source_session, read):
            result = stage(source_session, read)
            if "original_state__sum" in read.schema.names:
                captures.append(read)
            return result

        with pytest.MonkeyPatch.context() as counted_patch:
            counted_patch.setattr(SourceSession, "stage_derived", counted_stage)
            saved["shared_ratio"] = current.ratio(current).execute()
        assert len(captures) == 1
        shared_nodes["reused_observation"] = len(captures)
        grids = [
            mv.time_grid(during=mv.time_scope(start=start, end=end), grain=mv.grain("month"))
            for start, end in (("2026-09-01", "2026-11-01"), ("2026-07-01", "2026-09-01"))
        ]
        endpoints = [
            targets.each(grid).observe(count, during=grid.window, via=via) for grid in grids
        ]
        saved["period"] = (
            endpoints[0]
            .compare(endpoints[1], design=mv.PeriodChange(alignment=mv.window_bucket()))
            .execute()
        )
        temporal_grid = mv.time_grid(
            during=mv.time_scope(start="2026-07-01", end="2026-10-01"), grain=mv.grain("month")
        )
        temporal_targets = targets.each(temporal_grid)
        saved["running"] = temporal_targets.observe(
            ms.ref.metric("operations.running_energy"), at=temporal_grid.end, via=via
        ).execute()
        saved["folded"] = temporal_targets.observe(
            ms.ref.metric("operations.folded_energy"), during=temporal_grid.window, via=via
        ).execute()
        saved["current"], saved["baseline"] = current.execute(), baseline.execute()
    elif scenario == "a07":
        region = targets.read(ms.ref.dimension("operations.device.zone"))
        counts = targets.observe(count, during=august, via=via)
        saved["counts"], saved["region"] = counts.execute(), region.execute()
        shares = current.share_of(current.rollup())
        ranking = shares.rank(order="descending", ties="min")
        saved["share"], saved["ranking"] = shares.execute(), ranking.execute()
        saved["limited"] = ranking.limit(1).execute()
        saved["penetration"] = (
            counts.where(counts.value.gt(0)).members().penetration_in(targets).execute()
        )
        groups = counts.group_by(region).rollup()
        weights = groups.share_of(groups.rollup())
        saved["groups"], saved["weights"] = groups.execute(), weights.execute()
        saved["standardized"] = groups.standardize(
            reference=mv.reference_weights(
                weights, strata=(region,), unit=ms.ref.entity("operations.reading")
            )
        ).execute()
        axis = ms.ref.dimension("operations.reading.sensor")
        left = targets.observe(metric, during=august, via=via, coordinates=(axis,)).rollup()
        right = targets.observe(metric, during=july, via=via, coordinates=(axis,)).rollup()
        saved["difference"] = left.compare(right).execute()
        allocation = left.compare(right).attribute(axes=(axis,), top_k=1)
        saved["attribution"] = allocation.execute()
        saved["selected"] = allocation.where(allocation.contribution.value.gt(0)).execute()
        mean_energy = mv.runtime_metric.ratio(metric, count, label="energy per reading")
        mix_endpoints = [
            targets.observe(mean_energy, during=scope, via=via, coordinates=(axis,)).rollup()
            for scope in (august, july)
        ]
        saved["component_mix"] = (
            mix_endpoints[0].compare(mix_endpoints[1]).attribute(axes=(axis,), top_k=1).execute()
        )
        from marivo.analysis.materialization import graph_attribution

        result = graph_attribution.result
        allocations = []

        def counted_result(*args, **kwargs):
            if kwargs.get("verify", True):
                allocations.append(args[0].domain.definition_id)
            return result(*args, **kwargs)

        with pytest.MonkeyPatch.context() as counted_patch:
            counted_patch.setattr(graph_attribution, "result", counted_result)
            saved["table"] = mv.table(
                contribution=allocation.contribution,
                current=allocation.current,
                baseline=allocation.baseline,
            ).execute()
        assert len(allocations) == 1
        shared_nodes["three_attribution_views"] = len(allocations)
    else:
        grid = mv.time_grid(
            during=mv.time_scope(start="2026-07-01", end="2026-10-01"), grain=mv.grain("month")
        )
        values = targets.each(grid).observe(count, during=grid.window, via=via)
        saved["targets"], saved["values"] = targets.execute(), values.execute()
        controlled_grid = mv.time_grid(
            during=mv.time_scope(start="2026-06-01", end="2026-10-01"), grain=mv.grain("month")
        )
        saved["controlled_values"] = (
            targets.each(controlled_grid)
            .observe(count, during=controlled_grid.window, via=via)
            .execute()
        )
        saved["cohort"] = targets.cohort(values.value.gt(0), rule=mv.at_least(3)).execute()
        assert sorted(saved["cohort"].to_pandas().member) == oracle["cohort"]
        saved["any"] = targets.cohort(values.value.gt(0), rule=mv.any_instance()).execute()
        saved["all"] = targets.cohort(
            values.value.gt(0), rule=mv.all_instances(empty=mv.empty_opportunity.false())
        ).execute()
        with pytest.raises(AnalysisError, match=r"opportunity|keys"):
            targets.cohort(
                values.where(values.value.gt(0)).value.gt(0), rule=mv.any_instance()
            ).execute()
    return saved


def _continue(saved: dict[str, Saved], scenario: Scenario) -> dict[str, Saved]:
    outputs: dict[str, Saved] = {}
    if scenario == "a02":
        difference = saved["difference"]
        assert isinstance(difference, mv.MaterializedDifferenceRelation)
        selected = difference.where(difference.value.lt(0))
        outputs["where"] = selected.execute()
        outputs["members"] = selected.members().execute()
        outputs["sum"] = difference.summarize(mv.sum()).execute()
        assert sorted(outputs["members"].to_pandas().member) == _oracle()["selected"]
        current, baseline = saved["current"], saved["baseline"]
        assert isinstance(current, mv.MaterializedNumericRelation) and isinstance(
            baseline, mv.MaterializedNumericRelation
        )
        outputs["compare"] = current.compare(baseline).execute()
        assert _mapping(outputs["compare"]) == _oracle()["difference"]
    elif scenario == "a06":
        for name in (
            "relative",
            "union",
            "nested",
            "ratio",
            "period",
            "shared_ratio",
            "negative_relative",
        ):
            value = _numeric(saved[name])
            selected = value.where(value.value.is_defined())
            outputs[name] = selected.where(selected.value.gte(-1000)).execute()
        branch = saved["branch_ratio"]
        assert isinstance(branch, mv.MaterializedRatioRelation)
        outputs["branch_ratio"] = branch.rollup().execute()
        facts = _facts()
        readings = [row for row in facts.orders if row[4].startswith("2026-08")]
        assert outputs["branch_ratio"].to_pandas().value.tolist() == [
            float(Fraction(sum(row[2] == "web" for row in readings), len(readings)))
        ]
        running = _numeric(saved["running"])
        outputs["running"] = running.group_by(mv.grain("month")).rollup().execute()
        assert outputs["running"].to_pandas().value.tolist() == [
            sum(Decimal(str(row[5])) for row in facts.orders if row[4][:7] <= month)
            for month in ("2026-07", "2026-08", "2026-09")
        ]
        with pytest.raises(AnalysisError, match=r"cumulative|overlap"):
            running.group_by(ms.ref.entity("operations.device")).rollup()
        outputs["folded"] = (
            _numeric(saved["folded"])
            .group_by(ms.ref.entity("operations.device"))
            .rollup()
            .execute()
        )
        assert outputs["folded"].to_pandas().value.tolist() == [
            max(Decimal(str(row[5])) for row in facts.orders if row[1] == member)
            for member, _ in facts.customers
        ]
        assert _mapping(saved["nested"]) == dict.fromkeys("ABCD", -1)
        assert _mapping(saved["ratio"]) == _oracle()["amounts"]["2026-08"]
        assert (
            saved["relative"].to_pandas().set_index("member").loc["D", "cell_reason"]
            == "zero_baseline"
        )
        assert (
            saved["union"].to_pandas().set_index("member").loc["C", "cell_reason"] == "missing_side"
        )
    elif scenario == "a07":
        ranking = saved["ranking"]
        assert isinstance(ranking, mv.MaterializedRankingResult)
        outputs["limit"] = ranking.limit(1).execute()
        assert outputs["limit"].to_pandas().member.tolist() == ["B"]
        assert (
            dict(outputs["limit"].contract()._facts)["reference"]
            == dict(ranking.contract()._facts)["reference"]
        )
        assert _mapping(saved["share"]) == _oracle()["share"]
        assert saved["standardized"].to_pandas().value.tolist() == [_oracle()["standardized"]]
        groups, weights, region = saved["groups"], saved["weights"], saved["region"]
        assert isinstance(groups, mv.MaterializedGroupedNumericRelation)
        assert isinstance(weights, mv.MaterializedNumericRelation)
        assert isinstance(region, mv.MaterializedCategoryRelation)
        outputs["standardized"] = groups.standardize(
            reference=mv.reference_weights(
                weights, strata=(region,), unit=ms.ref.entity("operations.reading")
            )
        ).execute()
        attribution = saved["attribution"]
        assert isinstance(attribution, mv.MaterializedAttributionResult)
        outputs["where"] = attribution.where(attribution.contribution.value.gt(0)).execute()
        assert dict(outputs["where"].contract()._facts)["complete_partition"] == "False"
        outputs["view_rank"] = attribution.contribution.rank(
            order="descending", ties="ordinal"
        ).execute()
        outputs["table"] = mv.table(
            contribution=attribution.contribution,
            current=attribution.current,
            baseline=attribution.baseline,
        ).execute()
        mix = saved["component_mix"]
        assert isinstance(mix, mv.MaterializedAttributionResult)
        outputs["component_mix_where"] = mix.where(mix.contribution.value.gt(0)).execute()
        assert (
            dict(outputs["component_mix_where"].contract()._facts)["complete_partition"] == "False"
        )
        outputs["component_mix_table"] = mv.table(
            contribution=mix.contribution, current=mix.current, baseline=mix.baseline
        ).execute()
        assert not hasattr(saved["table"], "contract") and not hasattr(saved["table"], "where")
    else:
        targets, values = saved["targets"], saved["values"]
        assert isinstance(targets, mv.MaterializedAnalysisDomain)
        assert isinstance(values, mv.MaterializedNumericRelation)
        for name, rule in (
            ("cohort", mv.at_least(3)),
            ("any", mv.any_instance()),
            ("all", mv.all_instances(empty=mv.empty_opportunity.false())),
        ):
            outputs[name] = targets.cohort(
                values.value.gt(0), rule=rule, through=values.subject_binding
            ).execute()
            assert outputs[name].to_pandas().equals(saved[name].to_pandas())
        selected = values.where(values.value.is_defined())
        outputs["where_members"] = selected.where(selected.value.gte(0)).members().execute()
    return outputs


def journey(phase: str, project: Path, scenario_name: str, source: str) -> dict[str, object]:
    assert scenario_name in ("a02", "a06", "a07", "a08")
    scenario: Scenario = next(
        value for value in ("a02", "a06", "a07", "a08") if value == scenario_name
    )
    os.environ["MARIVO_PROJECT_ROOT"] = str(project)
    patch = pytest.MonkeyPatch()
    original = ibis.duckdb.connect
    patch.setattr(
        ibis.duckdb, "connect", lambda *args, **kwargs: original(*args, **{**kwargs, "threads": 1})
    )
    state_path = project / "r6-journey.json"
    try:
        if phase == "produce":
            session, case = _create(project, source)
            shared_nodes: dict[str, int] = {}
            saved = _produce(session, scenario, shared_nodes)
            _assert_oracles(saved, scenario)
            snapshots = {name: _snapshot(value) for name, value in saved.items()}
            state = {
                "session": session.id,
                "shared_nodes": shared_nodes,
                "artifacts": {name: item["artifact"] for name, item in snapshots.items()},
                "snapshots": snapshots,
            }
            state_path.write_text(json.dumps(state, sort_keys=True))
            # A real fact change distinguishes fresh source evaluation from frozen recovery.
            if source == "table":
                with duckdb.connect(str(case.database_path)) as connection:
                    connection.execute(
                        "UPDATE reading SET energy = energy + 7 WHERE device_id = ? AND recorded_at >= ? AND recorded_at < ?",
                        ["A", "2026-08-01", "2026-09-01"],
                    )
            else:
                import pyarrow.parquet as pq

                path = project / "source_files/reading.parquet"
                table = pq.read_table(path)
                rows = table.to_pylist()
                for row in rows:
                    if row["device_id"] == "A" and str(row["recorded_at"]).startswith("2026-08"):
                        row["energy"] += 7
                pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path)
            targets = session.members(ms.ref.entity("operations.device"))
            fresh = targets.observe(
                ms.ref.metric("operations.energy_total"),
                during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
                via=ms.ref.relationship("operations.reading_device"),
            ).execute()
            assert _mapping(fresh)["A"] == 67
            assert {name: _snapshot(value) for name, value in saved.items()} == snapshots
            case.database_path.unlink()
            shutil.rmtree(project / "models")
            if (project / "source_files").exists():
                shutil.rmtree(project / "source_files")
            continuations = {}
        else:
            assert phase in ("continue", "recover")
            assert not (project / "warehouse.duckdb").exists() and not (project / "models").exists()
            from marivo.datasource.adapters import SourceSession
            from marivo.datasource.runtime import DatasourceConnectionService

            def forbidden(*args: object, **kwargs: object) -> None:
                raise AssertionError("fixed relation journey consulted source, Semantic or DuckDB")

            for owner, name in (
                (duckdb, "connect"),
                (ibis.duckdb, "connect"),
                (ms, "load"),
                (SourceSession, "__init__"),
                (DatasourceConnectionService, "use_backend"),
            ):
                patch.setattr(owner, name, forbidden)
            state = json.loads(state_path.read_text())
            shared_nodes = state["shared_nodes"]
            session = mv.session.resume(state["session"], by="id")
            saved = {}
            for name, reference in state["artifacts"].items():
                value = session.artifact(reference)
                assert isinstance(value, (_MaterializedValue, mv.MaterializedTable))
                saved[name] = value
            _assert_oracles(saved, scenario)
            snapshots = {name: _snapshot(value) for name, value in saved.items()}
            for name, snapshot in snapshots.items():
                for field, actual in snapshot.items():
                    assert actual == state["snapshots"][name][field], (
                        name,
                        field,
                        actual,
                        state["snapshots"][name][field],
                    )
            continuations = {
                name: _snapshot(value) for name, value in _continue(saved, scenario).items()
            }
        return {
            "phase": phase,
            "pid": os.getpid(),
            "session": session.id,
            "snapshots": snapshots,
            "continuations": continuations,
            "run_count": len(session.runs().items),
            "oracle": _oracle(),
            "shared_nodes": shared_nodes,
            "edge_checks": _decision_edges(saved) if scenario == "a08" else {},
            "facts_sha256": hashlib.sha256(
                json.dumps(asdict(_facts()), sort_keys=True).encode()
            ).hexdigest(),
        }
    finally:
        patch.undo()


if __name__ == "__main__":
    import sys

    report = journey(sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4])
    Path(sys.argv[5]).write_text(json.dumps(report, sort_keys=True))
