"""Independent fact oracles and three-process fixed Frame journey verification."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path

import duckdb
import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.analysis.materialization.publication_fixtures import publication_counts
from tests.analysis.statistics.pandas_workflows import plain
from tests.shared_fixtures import (
    DslCase,
    DslNames,
    DslScenario,
    analysis_dsl_project_files,
    analysis_dsl_rows,
    export_dsl_parquet_models,
    seed_analysis_dsl_database,
    terminal_runs,
)
from tests.support.json import Json, checked, obj, read
from tests.support.source_trace import capture_source

# Deliberately not the ecommerce names used by the ordinary journey tests.
NAMES = DslNames(
    domain="operations",
    customer="device",
    order="reading",
    order_line="sample",
    customer_id="device_id",
    order_id="reading_id",
    line_id="sample_id",
    region="zone",
    channel="sensor",
    status="quality",
    ordered_at="recorded_at",
    amount="energy",
    line_amount="sample_energy",
    buyer="reading_device",
    line_order="sample_reading",
    revenue="energy_total",
    order_count="reading_count",
    line_revenue="sample_total",
    aov="energy_per_reading",
)


def _create(project: Path, scenario: DslScenario, source: str) -> mv.Session:
    project.mkdir(parents=True, exist_ok=True)
    os.environ["MARIVO_PROJECT_ROOT"] = str(project)
    os.chdir(project)
    database = project / "warehouse.duckdb"
    seed_analysis_dsl_database(
        database, NAMES, analysis_dsl_rows(scenario), float_amount=scenario in ("j4", "j4_ties")
    )
    (project / "marivo.toml").write_text('[project]\nname = "installed-graph"\n')
    for relative, content in analysis_dsl_project_files(
        NAMES, database, revenue_unit="kWh"
    ).items():
        path = (
            project
            / "models"
            / (relative if relative.startswith("datasources/") else f"semantic/{relative}")
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.replace("unit='CNY'", "unit='kWh'"))
    catalog = ms.load(workspace_dir=project)
    session = mv.session.get_or_create("installed", timezone="UTC")
    if source == "parquet":
        export_dsl_parquet_models(
            DslCase(scenario, NAMES, project, database, catalog, session), project
        )
        ms.load(workspace_dir=project)
    return session


def _frame(result: mv.AnalysisFrame, *, frame: pd.DataFrame | None = None) -> pd.DataFrame:
    if frame is None:
        return plain(result)
    exported = frame.copy()
    labels: list[str] = []
    key = 0
    for label in exported.columns:
        assert isinstance(label, tuple) and len(label) == 2
        kind, name = label
        if kind == "value":
            assert isinstance(name, str)
            labels.append(name)
        elif kind == "presence":
            assert isinstance(name, str)
            labels.append("presence." + name)
        else:
            assert kind == "coordinate"
            labels.append(f"key_{key}")
            key += 1
    exported.columns = labels
    return exported


def _ordinary_keys(frame: pd.DataFrame) -> tuple[str, ...]:
    """Locate ordinary coordinate columns without dropping the exported time keys."""
    return tuple(
        f"key_{index}"
        for index, axis in enumerate(frame.attrs["axes"])
        if axis["role"] in ("field", "identity", "data")
    )


def _remove_sources(project: Path) -> None:
    for path in project.glob("*.duckdb"):
        path.unlink()
    for name in ("models", "source_files"):
        path = project / name
        if path.exists():
            shutil.rmtree(path)


@contextmanager
def _offline(project: Path) -> Iterator[None]:
    assert not tuple(project.glob("*.duckdb"))
    assert not (project / "models").exists() and not (project / "source_files").exists()
    with pytest.MonkeyPatch.context() as patch:
        for owner, name in (
            (duckdb, "connect"),
            (ibis.duckdb, "connect"),
            (ms, "load"),
            (SemanticProject, "load"),
            (SourceSession, "__init__"),
            (SourceSession, "__enter__"),
            (SourceSession, "execute"),
            (DatasourceConnectionService, "use_backend"),
        ):
            patch.setattr(owner, name, forbidden)
        patch.setattr(mv.Session, "catalog", property(forbidden))
        yield


def _observations(
    session: mv.Session,
) -> tuple[mv.Population, mv.AnalysisFrame, mv.AnalysisFrame, mv.AnalysisFrame]:
    members = session.members(session.catalog.entities.get("operations.device"))
    metric = session.catalog.metrics.get("operations.energy_total")
    current = session.observe(
        {"value": metric},
        by=mv.product(session.time_scope(start="2026-08-01", end="2026-09-01"), members),
    )
    baseline = session.observe(
        {"value": metric},
        by=mv.product(session.time_scope(start="2026-07-01", end="2026-08-01"), members),
    )
    count = session.observe(
        {"value": session.catalog.metrics.get("operations.reading_count")},
        by=mv.product(session.time_scope(start="2026-08-01", end="2026-09-01"), members),
    )
    return members, current, baseline, count


def _association(left: pd.DataFrame, right: pd.DataFrame) -> dict[str, Json]:
    keys = [name for name in left.columns if name.startswith("key_")]
    assert keys and keys == [name for name in right.columns if name.startswith("key_")]
    lhs, rhs = left.set_index(keys), right.set_index(keys)
    if not lhs.index.equals(rhs.index):
        raise ValueError("different complete composite key sets")
    pairs = pd.DataFrame({"left": lhs.value, "right": rhs.value})
    complete = pairs.dropna()
    coefficient = float(
        complete.left.rank(method="average").corr(complete.right.rank(method="average"))
    )
    return {
        "coefficient": coefficient,
        "pairs": len(complete),
        "null_pairs": len(pairs) - len(complete),
    }


def _produce(session: mv.Session, scenario: DslScenario) -> dict[str, mv.AnalysisFrame]:
    members, current, baseline, count = _observations(session)
    before_runs = len(terminal_runs(session))
    before = publication_counts(session)
    with pytest.MonkeyPatch.context() as patch:
        trace = capture_source(patch)
        if scenario == "j1":
            amount = session.catalog.fields.get("operations.reading.energy")
            time = session.catalog.fields.get("operations.reading.recorded_at")
            current = session.observe(
                {"value": amount.sum(time=time)},
                by=mv.product(session.time_scope(start="2026-08-01", end="2026-09-01"), members),
            ).compute()
            queries = {
                "value": current,
                "rollup": current.reduce({"value": current["value"].sum()}),
                "count": current.reduce({"value": mv.row_count()}),
            }
        elif scenario == "j2":
            current, baseline = current.compute(), baseline.compute()
            comparison = current.compare(
                {"reference": baseline},
                on={"reference": tuple(zip(current.axes, baseline.axes, strict=True))},
                match={"reference": mv.ordinal()},
            )
            difference = comparison.select(
                {"value": comparison["reference.absolute.value"]}
            ).compute()
            selected = difference.where(difference["value"] < 0).compute()
            chosen = session.population_from(
                selected,
                on=(
                    (
                        next(axis for axis in selected.axes if axis.role == "identity"),
                        session.catalog.entities.get("operations.device"),
                    ),
                ),
            )
            queries = {
                "value": difference,
                "current": current,
                "baseline": baseline,
                "sum": difference.reduce({"value": difference["value"].sum()}),
                "where": selected,
                "members": session.frame(chosen),
                "selected_sum": selected.reduce({"value": selected["value"].sum()}),
            }
        elif scenario == "j3":
            sensor = session.catalog.fields.get("operations.reading.sensor")
            device = session.catalog.fields.get("operations.reading.device_id")
            metric = session.catalog.metrics.get("operations.energy_per_reading")
            scope = session.time_scope(start="2026-08-01", end="2026-09-01")
            ratio = session.observe(
                {"value": metric}, by=(scope, device, sensor), where=mv.in_population(members)
            ).compute()
            queries = {
                "value": ratio,
                "rollup": session.observe(
                    {"value": metric}, by=(scope,), where=mv.in_population(members)
                ),
                "group_by": session.observe(
                    {"value": metric}, by=(scope, sensor), where=mv.in_population(members)
                ),
                "mean": ratio.reduce({"value": ratio["value"].mean()}),
            }
        else:
            queries = {"left": current, "right": count}
        completed = {name: query.compute() for name, query in queries.items()}
        if scenario in ("j4", "j4_ties"):
            association = _association(_frame(completed["left"]), _frame(completed["right"]))
            coefficient, pairs = association["coefficient"], association["pairs"]
            assert isinstance(coefficient, float) and isinstance(pairs, int)
            completed["value"] = session.values(
                by=(),
                columns={"coefficient": None, "pairs": None},
                rows=(((), {"coefficient": coefficient, "pairs": pairs}),),
            ).compute()
        assert len(terminal_runs(session)) == before_runs and publication_counts(session) == before
        assert trace.native_sql and all(owner._closed for owner in trace.owners)
        source_reads = tuple(trace.native_sql)
        saved = {name: frame.persist() for name, frame in completed.items()}
        assert tuple(trace.native_sql) == source_reads
        return saved


def _oracle(scenario: DslScenario) -> dict[str, float]:
    facts = analysis_dsl_rows(scenario)
    august = [row for row in facts.orders if "2026-08-01" <= row[4] < "2026-09-01"]
    amounts = {
        key: sum(Fraction(row[-1]) for row in august if row[1] == key) for key, _ in facts.customers
    }
    if scenario == "j1":
        return {
            "rollup": float(sum(amounts.values())),
            "count": len(amounts),
        }
    if scenario == "j2":
        baseline = {
            key: sum(
                Fraction(row[-1])
                for row in facts.orders
                if row[1] == key and "2026-07-01" <= row[4] < "2026-08-01"
            )
            for key in amounts
        }
        differences = {key: value - baseline[key] for key, value in amounts.items()}
        return {
            "sum": float(sum(differences.values())),
            "selected_sum": float(sum(value for value in differences.values() if value < 0)),
            "selected_count": sum(value < 0 for value in differences.values()),
        }
    if scenario == "j3":
        groups = {(row[1], row[2]) for row in august}
        values = []
        for key in sorted(groups):
            orders = [row[0] for row in august if (row[1], row[2]) == key]
            numerator = sum(Fraction(row[2]) for row in facts.lines if row[1] in orders)
            values.append(numerator / len(orders))
        return {
            "rollup": float(sum(Fraction(row[2]) for row in facts.lines) / len(august)),
            "mean": float(sum(values) / len(values)),
        }
    counts = {key: sum(row[1] == key for row in august) for key in amounts}

    def ranks(values: Sequence[Fraction | int]) -> list[Fraction]:
        return [
            Fraction(1 + sum(other < value for other in values))
            + Fraction(sum(other == value for other in values) - 1, 2)
            for value in values
        ]

    left, right = ranks(list(amounts.values())), ranks(list(counts.values()))
    center = Fraction(len(left) + 1, 2)
    covariance = sum((x - center) * (y - center) for x, y in zip(left, right, strict=True))
    variance = sum((x - center) ** 2 for x in left) * sum((y - center) ** 2 for y in right)
    with localcontext() as context:
        context.prec = 180
        expected = float(
            (Decimal(covariance.numerator) / Decimal(covariance.denominator))
            / (Decimal(variance.numerator) / Decimal(variance.denominator)).sqrt()
        )
    return {"coefficient": expected, "pairs": len(left)}


def _verify(
    artifacts: dict[str, mv.AnalysisFrame],
    scenario: DslScenario,
    *,
    exports: dict[str, pd.DataFrame],
) -> dict[str, Json]:
    frames = {name: _frame(value, frame=exports[name]) for name, value in artifacts.items()}
    frame = frames["value"]
    facts = analysis_dsl_rows(scenario)
    oracle = _oracle(scenario)
    derived: dict[str, Json]
    if scenario in ("j1", "j2"):
        (member_key,) = _ordinary_keys(frame)
        indexed = frame.set_index(member_key)
        assert set(indexed.index) == {key for key, _ in facts.customers}
        for key, _ in facts.customers:
            assert isinstance(key, str)
            current = [
                Fraction(row[-1])
                for row in facts.orders
                if row[1] == key and "2026-08-01" <= row[4] < "2026-09-01"
            ]
            if scenario == "j1" and not current:
                assert pd.isna(indexed.loc[key, "value"])
                continue
            expected = sum(current, Fraction())
            if scenario == "j2":
                expected -= sum(
                    (
                        Fraction(row[-1])
                        for row in facts.orders
                        if row[1] == key and "2026-07-01" <= row[4] < "2026-08-01"
                    ),
                    Fraction(),
                )
            assert indexed.loc[key, "value"] == float(expected)
        if scenario == "j1":
            derived = {"rollup": float(frame.value.sum()), "count": len(frame)}
            assert frames["rollup"].value.tolist() == [oracle["rollup"]]
            assert frames["count"].value.tolist() == [oracle["count"]]
        else:
            left, right = (
                frames["current"].set_index(_ordinary_keys(frames["current"])[0]),
                frames["baseline"].set_index(_ordinary_keys(frames["baseline"])[0]),
            )
            pd.testing.assert_series_equal(
                indexed.value, left.value - right.value, check_names=False
            )
            selected = frame.loc[frame.value < 0]
            derived = {
                "sum": float(frame.value.sum()),
                "selected_sum": float(selected.value.sum()),
                "selected_count": len(selected),
            }
            assert frames["sum"].value.tolist() == [oracle["sum"]]
            assert frames["selected_sum"].value.tolist() == [oracle["selected_sum"]]
            assert len(frames["members"]) == oracle["selected_count"]
            pd.testing.assert_frame_equal(frames["where"], selected.reset_index(drop=True))
    elif scenario == "j3":
        indexed = frame.set_index(list(_ordinary_keys(frame)))
        keys = {(row[1], row[2]) for row in facts.orders}
        assert set(indexed.index) == keys
        for coordinate in keys:
            member, sensor = coordinate
            assert isinstance(member, str) and isinstance(sensor, str)
            orders = [row[0] for row in facts.orders if (row[1], row[2]) == coordinate]
            ratio = sum(
                (Fraction(row[2]) for row in facts.lines if row[1] in orders), Fraction()
            ) / len(orders)
            assert indexed["value"][(member, sensor)] == float(ratio)
        assert frames["rollup"].value.tolist() == [oracle["rollup"]]
        assert frames["mean"].value.tolist() == [oracle["mean"]]
        derived = {
            "rollup": float(frames["rollup"].value.iloc[0]),
            "mean": float(frame.value.mean()),
        }
        sensor_values = {}
        sensors: set[str] = set()
        for row in facts.orders:
            assert isinstance(row[2], str)
            sensors.add(row[2])
        for sensor in sorted(sensors):
            order_ids = {row[0] for row in facts.orders if row[2] == sensor}
            sensor_values[sensor] = float(
                sum((Fraction(row[2]) for row in facts.lines if row[1] in order_ids), Fraction())
                / len(order_ids)
            )
        assert (
            frames["group_by"].set_index(_ordinary_keys(frames["group_by"])[0]).value.to_dict()
            == sensor_values
        )
    else:
        derived = _association(frames["left"], frames["right"])
        assert derived["coefficient"] == pytest.approx(oracle["coefficient"], abs=1e-15, rel=0)
        assert derived["pairs"] == oracle["pairs"]
        assert frame.coefficient.tolist() == [derived["coefficient"]]
        assert frame.pairs.tolist() == [derived["pairs"]]
        return derived
    assert derived == oracle
    return obj(checked(derived))


def journey(phase: str, project: Path, scenario_name: str, source: str) -> dict[str, object]:
    scenarios: tuple[DslScenario, ...] = ("j1", "j2", "j3", "j4", "j4_ties")
    assert scenario_name in scenarios and phase in ("produce", "fixed", "cold")
    scenario = next(value for value in scenarios if value == scenario_name)
    assert source in ("table", "parquet")
    project = project.resolve()
    os.environ["MARIVO_PROJECT_ROOT"] = str(project)
    state_path = project / "journey.json"
    with pytest.MonkeyPatch.context() as patch:
        original = ibis.duckdb.connect
        patch.setattr(
            ibis.duckdb,
            "connect",
            lambda *args, **kwargs: original(*args, **{**kwargs, "threads": 1}),
        )
        if phase == "produce":
            session = _create(project, scenario, source)
            artifacts = _produce(session, scenario)
            exports = {name: value.to_pandas() for name, value in artifacts.items()}
            derived = _verify(artifacts, scenario, exports=exports)
            state: dict[str, Json] = {
                "session": session.id,
                "artifacts": {name: value.artifact.id for name, value in artifacts.items()},
                "snapshots": {
                    name: snapshot(value, frame=exports[name]) for name, value in artifacts.items()
                },
                "derived": derived,
            }
            state_path.write_text(json.dumps(state, sort_keys=True))
            _remove_sources(project)
        else:
            state = read(state_path)
            with _offline(project):
                identity = state["session"]
                assert isinstance(identity, str)
                session = mv.session.resume(identity)
                before = len(terminal_runs(session))
                artifacts = {}
                for name, reference in obj(state["artifacts"]).items():
                    assert isinstance(reference, str)
                    artifact = session.persisted_frame(reference)
                    artifacts[name] = artifact
                exports = {name: value.to_pandas() for name, value in artifacts.items()}
                for name, value in artifacts.items():
                    assert snapshot(value, frame=exports[name]) == obj(state["snapshots"])[name]
                derived = _verify(artifacts, scenario, exports=exports)
                assert derived == state["derived"]
                assert len(terminal_runs(session)) == before
        saved = artifacts["value"]
        saved.show(n=2)
        assert all(callable(getattr(saved, name)) for name in ("where", "compare", "reduce"))
        assert not hasattr(saved.artifact, "to_pandas")
        before = len(terminal_runs(session))
        if "value" in saved.output_names:
            continued = saved.select({"value": saved["value"]}).compute()
            pd.testing.assert_frame_equal(continued.to_pandas(), exports["value"])
        assert len(terminal_runs(session)) == before
        assert session._runtime.store.resources(session.id) == ()
        return {
            "phase": phase,
            "pid": os.getpid(),
            "session": session.id,
            "store": session._runtime.store.store_id,
            "source_offline": not (project / "models").exists()
            and not (project / "source_files").exists(),
            "artifact": saved.artifact.id,
            "run": saved.artifact._manifest.run_id,
            "rows": obj(obj(state["snapshots"])["value"])["rows"],
            "snapshots": state["snapshots"],
            "derived": derived,
            "run_count": len(terminal_runs(session)),
            "oracle": _oracle(scenario),
            "facts_sha256": hashlib.sha256(
                json.dumps(asdict(analysis_dsl_rows(scenario)), sort_keys=True).encode()
            ).hexdigest(),
        }
