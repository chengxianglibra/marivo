"""Real Frame journeys, committed receipts and source-free pandas recipes."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Literal

import duckdb
import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo import _temporal
from marivo.analysis.core.data_schema import CalendarDataAxis, DataSchema, PairedDataAxis
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import _scalar, forbidden, snapshot
from tests.analysis.numeric.attribution_workflows import allocation
from tests.packaging.graph_journeys import NAMES
from tests.shared_fixtures import (
    DslCase,
    DslRows,
    analysis_dsl_project_files,
    analysis_dsl_rows,
    export_dsl_parquet_models,
    seed_analysis_dsl_database,
)
from tests.support.json import Json, arr, checked, encode, obj, read
from tests.support.source_trace import capture_source

Scenario = Literal["a02", "a06", "a07", "a08"]


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
    project.mkdir(parents=True, exist_ok=True)
    database = project / "warehouse.duckdb"
    seed_analysis_dsl_database(database, NAMES, _facts(), float_amount=False)
    (project / "marivo.toml").write_text('[project]\nname = "installed-relations"\n')
    for relative, content in analysis_dsl_project_files(
        NAMES, database, revenue_unit="kWh"
    ).items():
        if relative == "operations/models.py":
            content = content.replace(
                "finalized = ms.finalize()",
                "status_energy = ms.measure_column(name='status_energy', entity=orders, column='energy', "
                "additivity=ms.additive_all(except_=(ordered_at,)), status_time_dimension=ordered_at, unit='kWh')\n"
                "finalized = ms.finalize()",
                1,
            )
            content += """
import marivo.analysis as mv
history = mv.all_history()
status_value = finalized.resolve(status_energy)
@ms.metric()
def running_energy():
    return energy_total.window(history)
@ms.metric()
def folded_energy():
    return status_value.sum(time=time_value, empty="null").per(time_value).max()
"""
        path = (
            project
            / "models"
            / (relative if relative.startswith("datasources/") else f"semantic/{relative}")
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
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


def _frame(exported: pd.DataFrame) -> pd.DataFrame:
    """Flatten one already-collected export without reading a result again."""
    frame = exported.copy(deep=True)
    names: list[str] = []
    key = 0
    for label in frame.columns:
        assert isinstance(label, tuple) and len(label) == 2
        kind, name = label
        if kind == "value":
            assert isinstance(name, str)
            names.append(name)
        elif kind == "presence":
            assert isinstance(name, str)
            names.append("presence." + name)
        elif kind == "coordinate":
            names.append(f"key_{key}")
            key += 1
        else:
            raise AssertionError(f"Unexpected Frame export kind: {kind}")
    frame.columns = names
    return frame


def _frozen_tables(exported: pd.DataFrame) -> dict[str, pd.DataFrame]:
    parts: dict[str, pd.DataFrame] = {}
    for item in exported.attrs["frozen_coordinates"]:
        name, rows = item["name"], item["rows"]
        assert isinstance(name, str) and isinstance(rows, pd.DataFrame)
        assert name not in parts
        parts[name] = rows
    return parts


def _records(frame: pd.DataFrame) -> Json:
    return [_scalar(row) for row in frame.to_dict(orient="records")]


def _produce(
    session: mv.Session, scenario: Scenario, patch: pytest.MonkeyPatch
) -> tuple[dict[str, mv.AnalysisFrame], dict[str, Json]]:
    targets = session.members(ms.ref.entity("operations.device"))
    metric = session.catalog.metrics.get("operations.energy_total")
    count = session.catalog.metrics.get("operations.reading_count")

    def monthly(month: int) -> mv.AnalysisFrame:
        return session.observe(
            {"value": metric},
            during=session.time_scope(start=f"2026-{month:02}-01", end=f"2026-{month + 1:02}-01"),
            by=targets,
        )

    current, baseline = monthly(8), monthly(7)
    frames: dict[str, mv.AnalysisFrame] = {}
    shared: dict[str, Json] = {}
    if scenario == "a02":
        current, baseline = current.compute(), baseline.compute()
        comparison = current.compare(baseline, on=((current.axes[0], baseline.axes[0]),))
        change = comparison.select({"value": comparison["absolute.value"]}).compute()
        chosen = change.where(change["value"] < 0).compute()
        keys = session.population_from(
            chosen,
            on=((chosen.axes[0], session.catalog.entities.get("operations.device")),),
        )
        selected = session.members(ms.ref.entity("operations.device"), where=mv.in_population(keys))
        frames = {
            "difference": change,
            "members": session.frame(selected),
            "next": session.observe(
                {"value": metric},
                during=session.time_scope(start="2026-09-01", end="2026-10-01"),
                by=selected,
                members=selected,
            ),
            "current": current,
            "baseline": baseline,
        }
    elif scenario == "a06":
        trace = capture_source(patch)
        reused = current.select({"value": current["value"] / current["value"]}).compute()
        numeric_reads = sum('"energy"' in sql for sql in trace.native_sql)
        assert numeric_reads == 1 and all(owner._closed for owner in trace.owners)
        reused = reused.persist()
        assert sum('"energy"' in sql for sql in trace.native_sql) == numeric_reads
        shared["reused_observation"] = numeric_reads
        current, baseline = current.compute(), baseline.compute()
        comparison = current.compare(baseline, on=((current.axes[0], baseline.axes[0]),))
        reverse = baseline.compare(current, on=((baseline.axes[0], current.axes[0]),))
        absolute = comparison.select({"value": comparison["absolute.value"]})
        reversed_absolute = reverse.select({"value": reverse["absolute.value"]})
        negative = absolute.compare(
            reversed_absolute, on=((absolute.axes[0], reversed_absolute.axes[0]),)
        )
        filtered = baseline.where(baseline["value"] > 80)
        union = current.compare(filtered, on=((current.axes[0], filtered.axes[0]),), how="outer")
        counts_by_month = [
            session.observe(
                {"value": count},
                during=session.time_scope(
                    start=f"2026-{month:02}-01", end=f"2026-{month + 1:02}-01"
                ),
                by=targets,
            )
            for month in (8, 7, 6)
        ]
        first, second = (
            counts_by_month[0].compare(
                counts_by_month[1], on=((counts_by_month[0].axes[0], counts_by_month[1].axes[0]),)
            ),
            counts_by_month[1].compare(
                counts_by_month[2], on=((counts_by_month[1].axes[0], counts_by_month[2].axes[0]),)
            ),
        )
        first_delta = first.select({"value": first["absolute.value"]})
        second_delta = second.select({"value": second["absolute.value"]})
        nested = first_delta.compare(
            second_delta, on=((first_delta.axes[0], second_delta.axes[0]),)
        )
        quantities = session.observe(
            {"energy": metric, "count": count},
            during=session.time_scope(start="2026-08-01", end="2026-09-01"),
            by=targets,
        )
        sensor = session.catalog.fields.get("operations.reading.sensor")
        branch = count.where(sensor == "web") / count
        grids = [
            session.time_scope(start=start, end=end).by(mv.grain("month"))
            for start, end in (("2026-09-01", "2026-11-01"), ("2026-07-01", "2026-09-01"))
        ]
        endpoints = [session.observe({"value": count}, during=grid, by=targets) for grid in grids]
        period = endpoints[0].compare(
            endpoints[1],
            on=tuple(zip(endpoints[0].axes, endpoints[1].axes, strict=True)),
            match=mv.ordinal(),
        )
        temporal = session.time_scope(start="2026-07-01", end="2026-10-01").by(mv.grain("month"))
        running = session.observe(
            {"value": session.catalog.metrics.get("operations.running_energy")},
            during=temporal,
            by=targets,
        )
        folded = session.observe(
            {"value": session.catalog.metrics.get("operations.folded_energy")},
            during=temporal,
            by=targets,
        )
        frames = {
            "current": current,
            "baseline": baseline,
            "relative": comparison.select({"value": comparison["relative.value"]}),
            "negative_relative": negative.select({"value": negative["relative.value"]}),
            "union": union,
            "nested": nested.select({"value": nested["absolute.value"]}),
            "ratio": quantities.select({"value": quantities["energy"] / quantities["count"]}),
            "branch_ratio": session.observe(
                {"value": branch},
                during=session.time_scope(start="2026-08-01", end="2026-09-01"),
                by=targets,
            ),
            "branch_total": session.observe(
                {"value": branch}, during=session.time_scope(start="2026-08-01", end="2026-09-01")
            ),
            "period": period,
            "running": running,
            "running_total": session.observe(
                {"value": session.catalog.metrics.get("operations.running_energy")}, during=temporal
            ),
            "folded": folded,
            "folded_total": session.observe(
                {"value": session.catalog.metrics.get("operations.folded_energy")},
                during=session.time_scope(start="2026-07-01", end="2026-10-01"),
                by=targets,
            ),
        }
        saved = {name: frame.persist() for name, frame in frames.items()}
        saved["shared_ratio"] = reused
        return saved, shared
    elif scenario == "a07":
        region = session.catalog.fields.get("operations.device.zone")
        counts = session.observe(
            {"value": count},
            during=session.time_scope(start="2026-08-01", end="2026-09-01"),
            by=targets,
        )
        groups = session.observe(
            {"value": count},
            during=session.time_scope(start="2026-08-01", end="2026-09-01"),
            by=targets.classify(by=(region,)),
        )
        totals = [
            session.observe(
                {"value": metric},
                during=session.time_scope(start=start, end=end),
            )
            for start, end in (("2026-08-01", "2026-09-01"), ("2026-07-01", "2026-08-01"))
        ]
        difference = totals[0].compare(totals[1])
        frames = {
            "counts": counts,
            "region": session.read(targets, {"value": region}),
            "current": current,
            "groups": groups,
            "difference": difference,
        }
        readings = session.members(ms.ref.entity("operations.reading"))
        basis = session.read(
            readings,
            {
                "member": session.catalog.fields.get("operations.reading.device_id"),
                "channel": session.catalog.fields.get("operations.reading.sensor"),
                "amount": session.catalog.fields.get("operations.reading.energy"),
                "time": session.catalog.fields.get("operations.reading.recorded_at"),
            },
        )
        for month, name in ((8, "current"), (7, "baseline")):
            frames["basis_" + name] = basis.where(
                (basis["time"] >= datetime(2026, month, 1, tzinfo=timezone.utc))
                & (basis["time"] < datetime(2026, month + 1, 1, tzinfo=timezone.utc))
            )
        shared["three_attribution_views"] = 1
    else:
        temporal = session.time_scope(start="2026-07-01", end="2026-10-01").by(mv.grain("month"))
        controlled = session.time_scope(start="2026-06-01", end="2026-10-01").by(mv.grain("month"))
        frames = {
            "targets": session.frame(targets),
            "values": session.observe({"value": count}, during=temporal, by=targets),
            "controlled_values": session.observe({"value": count}, during=controlled, by=targets),
        }
    return {name: frame.persist() for name, frame in frames.items()}, shared


def _derived(exports: dict[str, pd.DataFrame], scenario: Scenario) -> dict[str, Json]:
    """Pandas owns the recipes over the already-collected fixed data."""
    result: dict[str, Json] = {}
    if scenario == "a02":
        change = _frame(exports["difference"])
        selected = change[change.value < 0]
        current, baseline = _frame(exports["current"]), _frame(exports["baseline"])
        assert current.key_0.tolist() == baseline.key_0.tolist()
        result = {
            "where": _records(selected),
            "members": checked(selected.key_0.tolist()),
            "sum": checked(int(change.value.sum())),
            "compare": checked(
                dict(
                    zip(
                        current.key_0,
                        (
                            int(left) - int(right)
                            for left, right in zip(current.value, baseline.value, strict=True)
                        ),
                        strict=True,
                    )
                )
            ),
        }
    elif scenario == "a06":
        for name in ("relative", "nested", "ratio", "shared_ratio", "negative_relative"):
            frame = _frame(exports[name])
            result[name] = _records(frame[frame.value.notna() & (frame.value >= -1000)])
        result["union"] = _records(_frame(exports["union"]))
        result["period"] = _records(_frame(exports["period"]))
        result["branch_ratio"] = _records(_frame(exports["branch_total"]))
        result["running"] = _records(_frame(exports["running_total"]))
        result["folded"] = _records(_frame(exports["folded_total"]))
    elif scenario == "a07":
        shares = _frame(exports["current"])
        total = sum((Fraction(value) for value in shares.value), Fraction())
        shares["ratio"] = shares.value.map(lambda value: float(Fraction(value) / total))
        ranking = shares.sort_values(["ratio", "key_0"], ascending=[False, True]).reset_index(
            drop=True
        )
        ranking["rank"] = ranking["ratio"].rank(method="min", ascending=False).astype(int)
        groups = _frame(exports["groups"]).set_index("key_0")
        assert groups.index.is_unique
        denominator = sum((Fraction(value) for value in groups.value), Fraction())
        weights = groups.value.map(lambda value: Fraction(value) / denominator)
        standardized = sum(
            (
                Fraction(value) * Fraction(weight)
                for value, weight in zip(groups.value, weights, strict=True)
            ),
            Fraction(),
        )
        counts = _frame(exports["counts"])
        current, baseline = (
            _frame(exports["basis_" + side]).drop(columns="key_0")
            for side in ("current", "baseline")
        )
        # One external allocation supplies current/baseline/contribution table views.
        allocated = allocation(current, baseline, parents=(), top_k=1)
        mix = allocation(current, baseline, method="mean", parents=(), top_k=1)
        result = {
            "ratios": _records(shares),
            "weights": checked({str(key): float(weight) for key, weight in weights.items()}),
            "ranking": _records(ranking),
            "limited": _records(ranking.iloc[:1]),
            "standardized": float(standardized),
            "penetration": len(counts.loc[counts.value > 0, "key_0"].unique())
            / len(counts.key_0.unique()),
            "attribution": _records(allocated),
            "selected": _records(allocated[allocated.contribution > 0]),
            "table": _records(
                allocated[["channel", "other_flags", "current", "baseline", "contribution"]]
            ),
            "component_mix": _records(mix),
            "component_mix_where": _records(mix[mix.contribution > 0]),
            "component_mix_table": _records(
                mix[["channel", "other_flags", "current", "baseline", "contribution"]]
            ),
        }
    else:
        values = _frame(exports["values"])
        targets = _frame(exports["targets"])
        hits = {
            str(member): int(value)
            for member, value in values.value.gt(0).groupby(values.key_1).sum().items()
        }
        opportunities = {
            str(member): int(value) for member, value in values.groupby("key_1").size().items()
        }
        assert all(value == 3 for value in opportunities.values()) and set(hits) == set(
            targets.key_0
        )
        result = {
            "cohort": checked(sorted(member for member, value in hits.items() if value >= 3)),
            "any": checked(sorted(member for member, value in hits.items() if value > 0)),
            "all": checked(
                sorted(member for member, value in hits.items() if value == opportunities[member])
            ),
            "where_members": checked(
                sorted(
                    values.loc[values.value.notna() & (values.value >= 0), "key_1"]
                    .unique()
                    .tolist()
                )
            ),
        }
        filtered = values.loc[values.value > 0]
        result["coverage_incomplete"] = len(filtered) != len(values)
        controlled = _frame(exports["controlled_values"])
        assert controlled.groupby("key_1").size().eq(4).all()
        result["controlled_all"] = checked(
            sorted(
                controlled.groupby("key_1").value.min().loc[lambda value: value > 0].index.tolist()
            )
        )
    return result


def _assert_oracles(
    saved: dict[str, mv.AnalysisFrame],
    exports: dict[str, pd.DataFrame],
    scenario: Scenario,
    derived: dict[str, Json],
) -> None:
    facts = _facts()
    members = tuple(str(member) for member, _ in facts.customers)
    months = ("2026-07", "2026-08", "2026-09")

    def amount(member: str, month: str) -> int:
        return sum(
            int(value)
            for _, buyer, _, _, day, value in facts.orders
            if buyer == member and day.startswith(month)
        )

    def count(member: str, month: str, channel: str | None = None) -> int:
        return sum(
            buyer == member and day.startswith(month) and (channel is None or sensor == channel)
            for _, buyer, sensor, _, day, _ in facts.orders
        )

    def cells(name: str, expected: dict[tuple[str, ...], object], value: str = "value") -> None:
        frame = _frame(exports[name])
        schema = saved[name]._schema
        assert isinstance(schema, DataSchema)
        keys = [f"key_{index}" for index, _ in enumerate(saved[name].axes)]
        labels: dict[str, dict[object, str]] = {}
        for index, axis in enumerate(schema.axes):
            if isinstance(axis, CalendarDataAxis):
                assert axis.frozen is not None and len(axis.columns) == 1
                directory = _frozen_tables(exports[name])[axis.frozen.directory_name]
                labels[keys[index]] = {
                    key: str(start)[:7]
                    for key, start in zip(
                        directory[axis.columns[0].slot_id], directory.nominal_start, strict=True
                    )
                }
        actual = {
            tuple(
                labels[key][row[key]]
                if key in labels
                else str(row[key])[:7]
                if isinstance(row[key], (pd.Timestamp, datetime))
                else str(row[key])
                for key in keys
            ): row[value]
            for row in frame.to_dict(orient="records")
        }
        assert len(actual) == len(frame) and actual.keys() == expected.keys(), name
        for key, wanted in expected.items():
            if wanted is None:
                assert pd.isna(actual[key]), (name, key, actual[key])
            else:
                assert actual[key] == wanted, (name, key, actual[key], wanted)

    if scenario in ("a02", "a06"):
        for name, month in (("current", months[1]), ("baseline", months[0])):
            cells(name, {(m,): amount(m, month) for m in members})
    if scenario == "a02":
        cells("difference", {(m,): amount(m, months[1]) - amount(m, months[0]) for m in members})
        selected = {m for m in members if amount(m, months[1]) < amount(m, months[0])}
        assert set(_frame(exports["members"]).key_0) == selected
        cells("next", {(m,): amount(m, months[2]) for m in selected})
        assert derived["members"] == sorted(selected)
        assert derived["compare"] == _oracle()["difference"]
        assert derived["sum"] == sum(amount(m, months[1]) - amount(m, months[0]) for m in members)
    elif scenario == "a06":
        cells(
            "relative",
            {
                (m,): float(
                    Fraction(amount(m, months[1]) - amount(m, months[0]), abs(amount(m, months[0])))
                )
                if amount(m, months[0])
                else None
                for m in members
            },
        )
        cells(
            "union",
            {
                (m,): amount(m, months[1]) - amount(m, months[0])
                if amount(m, months[0]) > 80
                else None
                for m in members
            },
            "absolute.value",
        )
        union = _frame(exports["union"]).set_index("key_0")
        # Presence is independent of all value masks and complete identity keys.
        exported = exports["union"]
        assert exported[("presence", "left_present")].all()
        assert exported[("presence", "right_present")].tolist() == [True, True, False, False]
        assert union.index.tolist() == _frame(exports["current"]).key_0.tolist()
        assert len(union) == 4
        cells(
            "nested",
            {
                (m,): count(m, months[1]) - 2 * count(m, months[0]) + count(m, "2026-06")
                for m in members
            },
        )
        cells(
            "ratio",
            {(m,): float(Fraction(amount(m, months[1]), count(m, months[1]))) for m in members},
        )
        cells(
            "branch_ratio",
            {
                (m,): float(Fraction(count(m, months[1], "web"), count(m, months[1])))
                for m in members
            },
        )
        cells("shared_ratio", {(m,): 1.0 if amount(m, months[1]) else None for m in members})
        cells(
            "negative_relative",
            {
                (m,): float(
                    Fraction(
                        2 * (amount(m, months[1]) - amount(m, months[0])),
                        abs(amount(m, months[0]) - amount(m, months[1])),
                    )
                )
                if amount(m, months[0]) != amount(m, months[1])
                else None
                for m in members
            },
        )
        cells(
            "period",
            {
                (str(index), m): count(m, left) - count(m, right)
                for m in members
                for index, (left, right) in enumerate(
                    (("2026-09", "2026-07"), ("2026-10", "2026-08"))
                )
            },
            "absolute.value",
        )
        period = saved["period"]._schema
        assert isinstance(period, DataSchema)
        paired = period.axes[0]
        assert isinstance(paired, PairedDataAxis)
        assert paired.left.frozen is not None and paired.right.frozen is not None
        tables = _frozen_tables(exports["period"])
        mapping = tables[paired.mapping_name]
        endpoint_months = []
        for side, endpoint in (("left", paired.left), ("right", paired.right)):
            assert endpoint.frozen is not None and len(endpoint.columns) == 1
            directory = tables[endpoint.frozen.directory_name]
            lookup = dict(
                zip(directory[endpoint.columns[0].slot_id], directory.nominal_start, strict=True)
            )
            endpoint_months.append([str(lookup[key])[:7] for key in mapping[f"{side}.0"]])
        assert list(zip(*endpoint_months, strict=True)) == [
            ("2026-09", "2026-07"),
            ("2026-10", "2026-08"),
        ]
        cells(
            "running",
            {
                (month, m): sum(amount(m, earlier) for earlier in months[: index + 1])
                for m in members
                for index, month in enumerate(months)
            },
        )
        cells(
            "folded",
            {
                (month, m): amount(m, month) if count(m, month) else None
                for m in members
                for month in months
            },
        )
        cells(
            "running_total",
            {
                (month,): sum(
                    amount(m, earlier) for m in members for earlier in months[: index + 1]
                )
                for index, month in enumerate(months)
            },
        )
        cells(
            "folded_total",
            {(m,): max(amount(m, month) for month in months if count(m, month)) for m in members},
        )
        cells(
            "branch_total",
            {
                (): float(
                    Fraction(
                        sum(count(m, months[1], "web") for m in members),
                        sum(count(m, months[1]) for m in members),
                    )
                )
            },
        )
    elif scenario == "a07":
        total = sum(amount(m, months[1]) for m in members)
        cells("counts", {(m,): count(m, months[1]) for m in members})
        cells(
            "difference",
            {(): sum(amount(m, months[1]) - amount(m, months[0]) for m in members)},
            "absolute.value",
        )
        cells("current", {(m,): amount(m, months[1]) for m in members})
        ratios = arr(derived["ratios"])
        assert {obj(row)["key_0"]: obj(row)["ratio"] for row in ratios} == {
            m: float(Fraction(amount(m, months[1]), total)) for m in members
        }
        ordered = sorted(members, key=lambda m: (-amount(m, months[1]), m))
        ranking = arr(derived["ranking"])
        assert [obj(row)["key_0"] for row in ranking] == ordered
        assert [obj(row)["rank"] for row in ranking] == [
            1 + sum(amount(other, months[1]) > amount(m, months[1]) for other in members)
            for m in ordered
        ]
        assert [obj(row)["key_0"] for row in arr(derived["limited"])] == ordered[:1]
        grouped = {
            str(region): sum(
                count(str(m), months[1]) for m, label in facts.customers if label == region
            )
            for _, region in facts.customers
        }
        denominator = sum(grouped.values())
        cells("groups", {(group,): value for group, value in grouped.items()})
        assert derived["weights"] == {
            group: float(Fraction(value, denominator)) for group, value in grouped.items()
        }
        assert derived["standardized"] == float(
            sum((Fraction(value * value, denominator) for value in grouped.values()), Fraction())
        )
        assert derived["penetration"] == float(
            Fraction(sum(count(m, months[1]) > 0 for m in members), len(members))
        )
        sides = {
            month: {
                sensor: sum(
                    int(value)
                    for _, _, channel, _, day, value in facts.orders
                    if channel == sensor and day.startswith(month)
                )
                for _, _, sensor, _, _, _ in facts.orders
                if sensor is not None
            }
            for month in months[:2]
        }
        channels = sorted(sides[months[0]])
        channels.sort(
            key=lambda channel: abs(sides[months[0]][channel]) + abs(sides[months[1]][channel]),
            reverse=True,
        )
        first = channels[0]
        expected = {
            (first, 0): (sides[months[1]][first], sides[months[0]][first]),
            (None, 1): tuple(
                sum(sides[month][channel] for channel in channels[1:])
                for month in (months[1], months[0])
            ),
        }
        for name in ("attribution", "table", "selected"):
            rows = arr(derived[name])
            admitted = {
                key: pair
                for key, pair in expected.items()
                if name != "selected" or pair[0] - pair[1] > 0
            }
            assert len(rows) == len(admitted)
            for row in rows:
                record = obj(row)
                channel, flags = record["channel"], record["other_flags"]
                assert channel is None or isinstance(channel, str)
                assert isinstance(flags, int)
                current, baseline = admitted[(channel, flags)]
                assert (record["current"], record["baseline"], record["contribution"]) == (
                    current,
                    baseline,
                    current - baseline,
                )
        mix_channels = sorted(channels)
        mix_channels.sort(
            key=lambda sensor: sum(
                day.startswith(month) and channel == sensor
                for _, _, channel, _, day, _ in facts.orders
                for month in months[:2]
            ),
            reverse=True,
        )
        first_mix = mix_channels[0]
        mix_expected = {
            (first_mix, 0): tuple(
                Fraction(sides[month][first_mix], sum(count(m, month) for m in members))
                for month in (months[1], months[0])
            ),
            (None, 1): tuple(
                Fraction(
                    sum(sides[month][channel] for channel in mix_channels[1:]),
                    sum(count(m, month) for m in members),
                )
                for month in (months[1], months[0])
            ),
        }
        for row in arr(derived["component_mix"]):
            record = obj(row)
            channel, flags = record["channel"], record["other_flags"]
            assert channel is None or isinstance(channel, str)
            assert isinstance(flags, int)
            mix_current, mix_baseline = mix_expected[(channel, flags)]
            assert (record["current"], record["baseline"], record["contribution"]) == (
                float(mix_current),
                float(mix_baseline),
                float(mix_current - mix_baseline),
            )
        assert len(arr(derived["component_mix_where"])) == sum(
            current - baseline > 0 for current, baseline in mix_expected.values()
        )
    else:
        cells("values", {(month, m): count(m, month) for m in members for month in months})
        cells(
            "controlled_values",
            {(month, m): count(m, month) for m in members for month in ("2026-06", *months)},
        )
        for name, threshold in (("cohort", 3), ("any", 1), ("all", 3)):
            expected_members = sorted(
                m for m in members if sum(count(m, month) > 0 for month in months) >= threshold
            )
            assert derived[name] == expected_members
        assert derived["coverage_incomplete"] is True
        assert derived["controlled_all"] == []
        assert derived["where_members"] == sorted(members)


def journey(phase: str, project: Path, scenario_name: str, source: str) -> dict[str, object]:
    """Run one real producer or source-blocked stored Frame process phase."""
    scenarios: tuple[Scenario, ...] = ("a02", "a06", "a07", "a08")
    assert phase in ("produce", "fixed", "cold") and scenario_name in scenarios
    assert source in ("table", "parquet")
    scenario = next(value for value in scenarios if value == scenario_name)
    project = project.resolve()
    os.environ["MARIVO_PROJECT_ROOT"] = str(project)
    state_path = project / "relation-journey.json"
    with pytest.MonkeyPatch.context() as patch:
        if phase == "produce":
            session, case = _create(project, source)
            saved, shared_nodes = _produce(session, scenario, patch)
            state = obj(
                checked(
                    {
                        "session": session.id,
                        "store": session._runtime.store.store_id,
                        "artifacts": {name: frame.artifact.id for name, frame in saved.items()},
                        "shared_nodes": shared_nodes,
                    }
                )
            )
            state_path.write_bytes(encode(checked(state)))
            case.database_path.unlink()
            shutil.rmtree(project / "models")
            if (project / "source_files").exists():
                shutil.rmtree(project / "source_files")
        else:
            assert not (project / "warehouse.duckdb").exists()
            assert not (project / "models").exists() and not (project / "source_files").exists()
            state = read(state_path)
            identity = state["session"]
            assert isinstance(identity, str)
            session = mv.session.resume(identity, by="id")
            assert session._runtime.store.store_id == state["store"]
            saved = {}
            for name, reference in obj(state["artifacts"]).items():
                assert isinstance(reference, str)
                saved[name] = session.persisted_frame(reference)
            shared_nodes = obj(state["shared_nodes"])
        for owner, name in (
            (duckdb, "connect"),
            (ibis.duckdb, "connect"),
            (ms, "load"),
            (SemanticProject, "load"),
            (SourceSession, "__init__"),
            (SourceSession, "execute"),
            (_temporal, "ZoneInfo"),
        ):
            patch.setattr(owner, name, forbidden)
        patch.setattr(mv.Session, "catalog", property(forbidden))
        before = len(session.runs().items)
        exports = {name: frame.to_pandas() for name, frame in saved.items()}
        snapshots = {name: snapshot(frame, frame=exports[name]) for name, frame in saved.items()}
        derived = _derived(exports, scenario)
        _assert_oracles(saved, exports, scenario, derived)
        assert len(session.runs().items) == before
        assert session._runtime.store.resources(session.id) == ()
        assert all(isinstance(frame, mv.AnalysisFrame) for frame in saved.values())
        assert all(isinstance(frame.artifact, mv.Artifact) for frame in saved.values())
        assert all(
            not hasattr(frame, name)
            for frame in saved.values()
            for name in ("execute", "rollup", "drill", "share", "population", "_node", "_dataset")
        )
        assert all(
            not hasattr(frame.artifact, name)
            for frame in saved.values()
            for name in ("to_pandas", "where", "compare", "rollup", "_node", "_dataset")
        )
        edge_checks = {
            "fixed_frame_and_audit_only_artifact": True,
            "no_read_runs": True,
            "independent_presence": scenario == "a06",
            "complete_identity_keys": True,
            "external_recipes": scenario in ("a07", "a08"),
        }
        report: dict[str, object] = {
            "phase": phase,
            "pid": os.getpid(),
            "session": session.id,
            "store": session._runtime.store.store_id,
            "snapshots": snapshots,
            "oracle": _oracle(),
            "facts_sha256": hashlib.sha256(
                json.dumps(asdict(_facts()), sort_keys=True).encode()
            ).hexdigest(),
            "edge_checks": edge_checks,
            "shared_nodes": shared_nodes,
            "derived": derived,
            "run_count": before,
            "source_offline": not (project / "warehouse.duckdb").exists()
            and not (project / "models").exists()
            and not (project / "source_files").exists(),
        }
        if phase == "produce":
            (project / "relation-report.json").write_bytes(encode(checked(report)))
        else:
            original = read(project / "relation-report.json")
            for field in (
                "session",
                "store",
                "snapshots",
                "oracle",
                "facts_sha256",
                "edge_checks",
                "shared_nodes",
                "derived",
                "run_count",
                "source_offline",
            ):
                assert checked(report[field]) == original[field], field
        return report


if __name__ == "__main__":
    result = journey(sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4])
    Path(sys.argv[5]).write_bytes(encode(checked(result)))
