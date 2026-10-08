"""Independent fact oracles and three-process Store 8 journey verification."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Sequence
from dataclasses import asdict
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path

import duckdb
import ibis
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.public_dsl import _MaterializedValue
from tests.shared_fixtures import (
    DslCase,
    DslNames,
    DslScenario,
    analysis_dsl_project_files,
    analysis_dsl_rows,
    export_dsl_parquet_models,
    seed_analysis_dsl_database,
)

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
    project.mkdir(parents=True)
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
    session = mv.session.get_or_create("installed", report_timezone="UTC")
    if source == "parquet":
        export_dsl_parquet_models(
            DslCase(scenario, NAMES, project, database, catalog, session), project
        )
        ms.load(workspace_dir=project)
    return session


def _observations(
    session: mv.Session,
) -> tuple[
    mv.LogicalAnalysisDomain,
    mv.LogicalNumericRelation,
    mv.LogicalNumericRelation,
    mv.LogicalNumericRelation,
]:
    members = session.members(ms.ref.entity("operations.device"))
    metric = ms.ref.metric("operations.energy_total")
    via = ms.ref.relationship("operations.reading_device")
    current = members.observe(
        metric, during=mv.time_scope(start="2026-08-01", end="2026-09-01"), via=via
    )
    baseline = members.observe(
        metric, during=mv.time_scope(start="2026-07-01", end="2026-08-01"), via=via
    )
    count = members.observe(
        ms.ref.metric("operations.reading_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=via,
    )
    assert isinstance(current, mv.LogicalNumericRelation)
    assert isinstance(baseline, mv.LogicalNumericRelation)
    assert isinstance(count, mv.LogicalNumericRelation)
    return members, current, baseline, count


def _produce(
    session: mv.Session, scenario: DslScenario
) -> tuple[_MaterializedValue, list[mv.MaterializedNumericRelation]]:
    members, current, baseline, count = _observations(session)
    saved: _MaterializedValue
    extras: list[mv.MaterializedNumericRelation]
    if scenario == "j1":
        saved = current.execute()
        extras = []
    elif scenario == "j2":
        saved = current.compare(baseline).execute()
        extras = [current.execute(), baseline.execute()]
    elif scenario == "j3":
        saved = members.observe(
            ms.ref.metric("operations.energy_per_reading"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=mv.routes(
                mv.route(
                    ms.ref.entity("operations.sample"),
                    through=(
                        ms.ref.relationship("operations.sample_reading"),
                        ms.ref.relationship("operations.reading_device"),
                    ),
                ),
                mv.route(
                    ms.ref.entity("operations.reading"),
                    through=(ms.ref.relationship("operations.reading_device"),),
                ),
            ),
            coordinates=(ms.ref.dimension("operations.reading.sensor"),),
        ).execute()
        extras = []
    else:
        saved = current.correlate(count, method="spearman").execute()
        extras = []
    return saved, extras


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


def _continue(
    saved: _MaterializedValue,
    extras: list[mv.MaterializedNumericRelation],
    oracle: dict[str, float],
) -> dict[str, object]:
    outputs: dict[str, _MaterializedValue] = {}
    if isinstance(saved, mv.MaterializedNumericRelation):
        outputs["rollup"] = saved.rollup().execute()
        assert outputs["rollup"].to_pandas().iloc[0]["value"] == oracle["rollup"]
        outputs["count"] = saved.summarize(mv.count()).execute()
        assert outputs["count"].to_pandas().iloc[0]["value"] == oracle["count"]
    elif isinstance(saved, mv.MaterializedDifferenceRelation):
        outputs["compare"] = extras[0].compare(extras[1]).execute()
        assert outputs["compare"].to_pandas().equals(saved.to_pandas())
        outputs["sum"] = saved.summarize(mv.sum()).execute()
        assert outputs["sum"].to_pandas().iloc[0]["value"] == oracle["sum"]
        selected = saved.where(saved.value.lt(0)).execute()
        outputs["where"] = selected
        outputs["members"] = selected.members().execute()
        outputs["selected_sum"] = selected.summarize(mv.sum()).execute()
        assert outputs["selected_sum"].to_pandas().iloc[0]["value"] == oracle["selected_sum"]
        assert len(outputs["members"].to_pandas()) == oracle["selected_count"]
    elif isinstance(saved, mv.MaterializedRatioRelation):
        outputs["rollup"] = saved.rollup().execute()
        outputs["group_by"] = (
            saved.group_by(ms.ref.dimension("operations.reading.sensor")).rollup().execute()
        )
        outputs["mean"] = saved.summarize(mv.mean()).execute()
        assert outputs["rollup"].to_pandas().iloc[0]["value"] == oracle["rollup"]
        assert outputs["mean"].to_pandas().iloc[0]["value"] == oracle["mean"]
    else:
        assert isinstance(saved, mv.MaterializedAssociationResult)
        coefficient = saved.coefficient
        outputs["mean"] = coefficient.summarize(mv.mean()).execute()
        selected_coefficient = coefficient.where(
            coefficient.value.lt(0) if oracle["coefficient"] < 0 else coefficient.value.gt(0)
        ).execute()
        outputs["where"] = selected_coefficient
        outputs["selected_mean"] = selected_coefficient.summarize(mv.mean()).execute()
        assert outputs["selected_mean"].to_pandas().iloc[0]["value"] == oracle["coefficient"]
        assert saved.to_pandas().iloc[0]["complete_pair_count"] == oracle["pairs"]
    return {
        name: {
            "artifact": value.state.artifact_ref.ref,
            "run": value.state.producing_run_ref,
            "rows": json.loads(value.to_pandas().to_json(orient="table", index=False)),
        }
        for name, value in outputs.items()
    }


def _assert_primary(saved: _MaterializedValue, scenario: DslScenario) -> None:
    facts = analysis_dsl_rows(scenario)
    frame = saved.to_pandas()
    if scenario in ("j1", "j2"):
        indexed = frame.set_index("member")
        assert set(indexed.index) == {key for key, _ in facts.customers}
        for key, _ in facts.customers:
            assert isinstance(key, str)
            current = [
                Fraction(row[-1])
                for row in facts.orders
                if row[1] == key and "2026-08-01" <= row[4] < "2026-09-01"
            ]
            if scenario == "j1" and not current:
                assert indexed.loc[key, "cell_tag"] == "null"
                continue
            expected = sum(current, Fraction(0))
            if scenario == "j2":
                expected -= sum(
                    Fraction(row[-1])
                    for row in facts.orders
                    if row[1] == key and "2026-07-01" <= row[4] < "2026-08-01"
                )
            assert indexed.loc[key, "cell_tag"] == "defined"
            assert indexed.loc[key, "value"] == float(expected)
    elif scenario == "j3":
        indexed = frame.set_index(["member", "coord_0"])
        keys = {(row[1], row[2]) for row in facts.orders}
        assert set(indexed.index) == keys
        for coordinate in keys:
            orders = [row[0] for row in facts.orders if (row[1], row[2]) == coordinate]
            ratio = sum(
                (Fraction(row[2]) for row in facts.lines if row[1] in orders), Fraction(0)
            ) / len(orders)
            member, channel = coordinate
            assert isinstance(member, str) and isinstance(channel, str)
            assert indexed.loc[(member, channel), "value"] == float(ratio)
    else:
        assert frame.iloc[0]["coefficient"] == _oracle(scenario)["coefficient"]
        assert frame.iloc[0]["complete_pair_count"] == _oracle(scenario)["pairs"]


def journey(phase: str, project: Path, scenario_name: str, source: str) -> dict[str, object]:
    scenarios: tuple[DslScenario, ...] = ("j1", "j2", "j3", "j4", "j4_ties")
    assert scenario_name in scenarios
    scenario = next(value for value in scenarios if value == scenario_name)
    assert source in ("table", "parquet")
    os.environ["MARIVO_PROJECT_ROOT"] = str(project)
    # This process owns all these patches; fresh phases have no warm proof state.
    patch = pytest.MonkeyPatch()
    original = ibis.duckdb.connect
    patch.setattr(
        ibis.duckdb, "connect", lambda *args, **kwargs: original(*args, **{**kwargs, "threads": 1})
    )
    state_path = project / "journey.json"
    try:
        if phase == "produce":
            session = _create(project, scenario, source)
            saved, extras = _produce(session, scenario)
            state = {
                "session": session.id,
                "artifact": saved.state.artifact_ref.ref,
                "extras": [value.state.artifact_ref.ref for value in extras],
            }
            state_path.write_text(json.dumps(state))
            (project / "warehouse.duckdb").unlink()
            shutil.rmtree(project / "models")
            if (project / "source_files").exists():
                shutil.rmtree(project / "source_files")
            continuations = {}
        else:
            assert phase in ("continue", "recover")
            assert not (project / "warehouse.duckdb").exists()
            assert not (project / "models").exists()
            assert not (project / "source_files").exists()
            from marivo.datasource.adapters import SourceSession
            from marivo.datasource.runtime import DatasourceConnectionService

            def forbidden(*args: object, **kwargs: object) -> None:
                raise AssertionError("cold recovery consulted a source, DuckDB or current Semantic")

            for owner, name in (
                (duckdb, "connect"),
                (ibis.duckdb, "connect"),
                (ms, "load"),
                (SourceSession, "__init__"),
                (DatasourceConnectionService, "use_backend"),
            ):
                patch.setattr(owner, name, forbidden)
            state = json.loads(state_path.read_text())
            session = mv.session.resume(state["session"], by="id")
            loaded = session.artifact(state["artifact"])
            assert isinstance(loaded, _MaterializedValue)
            saved = loaded
            extras = []
            for reference in state["extras"]:
                extra = session.artifact(reference)
                assert isinstance(extra, mv.MaterializedNumericRelation)
                extras.append(extra)
            assert isinstance(saved, _MaterializedValue)
            continuations = _continue(saved, extras, _oracle(scenario))
        _assert_primary(saved, scenario)
        from marivo.analysis.materialization import graph_protocol, graph_store

        store = session._runtime.store
        with store._read() as connection:
            record = graph_store.artifact(store, connection, saved.state.artifact_ref.ref)
        assert record is not None
        descriptor = graph_protocol.encode(record.descriptor, graph_protocol.DESCRIPTOR)
        saved.show()
        contract = asdict(saved.contract())
        return {
            "phase": phase,
            "pid": os.getpid(),
            "session": session.id,
            "artifact": saved.state.artifact_ref.ref,
            "run": saved.state.producing_run_ref,
            "run_count": len(session.runs().items),
            "rows": json.loads(saved.to_pandas().to_json(orient="table", index=False)),
            "contract": contract,
            "descriptor": json.loads(descriptor),
            "continuations": continuations,
            "oracle": _oracle(scenario),
            "facts_sha256": hashlib.sha256(
                json.dumps(asdict(analysis_dsl_rows(scenario)), sort_keys=True).encode()
            ).hexdigest(),
        }
    finally:
        patch.undo()
