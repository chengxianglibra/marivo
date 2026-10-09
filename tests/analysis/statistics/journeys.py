"""Shared public inputs and independent nine-method assertions."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Literal, TypeAlias

import pandas as pd

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.graph_protocol import descriptor_plan, receipt_digest
from marivo.analysis.methods.physical import Qualified
from tests.analysis.statistics.deviation_fixture import prepare_profiles
from tests.analysis.statistics.deviation_oracle import expected
from tests.shared_fixtures import (
    DSL_NAMES,
    DslCase,
    analysis_dsl_project_files,
    analysis_dsl_rows,
    seed_analysis_dsl_database,
)
from tests.support.json import Json, key_json

METHODS = (
    "deviation.zscore@v1",
    "deviation.mad@v1",
    "time.runs@v1",
    "association.pearson@v1",
    "association.spearman@v1",
    "association.kendall@v1",
    "forecast.naive@v1",
    "forecast.drift@v1",
    "forecast.seasonal_naive@v1",
)
Numeric: TypeAlias = (
    mv.LogicalNumericRelation
    | mv.MaterializedNumericRelation
    | mv.LogicalRolledNumericRelation
    | mv.MaterializedRolledNumericRelation
    | mv.MaterializedGroupedNumericRelation
)
Logical: TypeAlias = (
    mv.LogicalDeviationResult
    | mv.LogicalTimeRunResult
    | mv.LogicalAssociationResult
    | mv.LogicalForecastResult
)
Result: TypeAlias = (
    mv.MaterializedDeviationResult
    | mv.MaterializedTimeRunResult
    | mv.MaterializedAssociationResult
    | mv.MaterializedForecastResult
)


def prepare(case: DslCase, form: str) -> mv.Session:
    prepare_profiles(case, "KC", form, "us", "UTC", False, followup=True)
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text()
        + "\ncopy_0 = ms.measure_column(name='copy_0', entity=orders, column='profile_0', "
        "additivity=ms.additive_all(), unit='CNY')\n"
    )
    ms.load(workspace_dir=case.root)
    return mv.session.get_or_create("r86", report_timezone="UTC")


def create(root: Path, form: str) -> mv.Session:
    root.mkdir(parents=True)
    database = root / "warehouse.duckdb"
    seed_analysis_dsl_database(database, DSL_NAMES, analysis_dsl_rows("j2"), float_amount=False)
    (root / "marivo.toml").write_text('[project]\nname = "r86"\n')
    for relative, content in analysis_dsl_project_files(DSL_NAMES, database).items():
        path = (
            root
            / "models"
            / (relative if relative.startswith("datasources/") else f"semantic/{relative}")
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    catalog = ms.load(workspace_dir=root)
    session = mv.session.get_or_create("bootstrap-r86", report_timezone="UTC")
    return prepare(DslCase("j2", DSL_NAMES, root, database, catalog, session), form)


def inputs(
    session: mv.Session,
) -> tuple[mv.LogicalNumericRelation, mv.LogicalNumericRelation, mv.LogicalRolledNumericRelation]:
    members = session.members(ms.ref.entity("sales.order"))
    entity = members.read(ms.ref.measure("sales.order.profile_0"))
    other = members.read(ms.ref.measure("sales.order.copy_0"))
    assert isinstance(entity, mv.LogicalNumericRelation)
    assert isinstance(other, mv.LogicalNumericRelation)
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-04"), grain=mv.grain("day")
    )
    timed = (
        members.observe(ms.ref.metric("sales.total_0"), during=grid, by=(mv.member(),))
        .group_by(grid)
        .rollup()
    )
    assert isinstance(timed, mv.LogicalRolledNumericRelation)
    return entity, other, timed


def graphs(originals: tuple[Numeric, Numeric, Numeric]) -> dict[str, Logical]:
    entity, other, timed = originals
    return {
        "deviation.zscore@v1": entity.deviation(method="zscore"),
        "deviation.mad@v1": entity.deviation(method="mad"),
        "time.runs@v1": timed.runs(where=timed.value.gt(1)),
        "association.pearson@v1": entity.correlate(other, method="pearson"),
        "association.spearman@v1": entity.correlate(other, method="spearman"),
        "association.kendall@v1": entity.correlate(other, method="kendall"),
        "forecast.naive@v1": timed.forecast(horizon=mv.periods(2), model=mv.naive()),
        "forecast.drift@v1": timed.forecast(horizon=mv.periods(2), model=mv.drift()),
        "forecast.seasonal_naive@v1": timed.forecast(
            horizon=mv.periods(2), model=mv.seasonal_naive(periods=2)
        ),
    }


def check(result: Result, method: str) -> None:
    digest = result.evidence_digest()
    if isinstance(result, mv.MaterializedDeviationResult):
        algorithm: Literal["zscore", "mad"] = "zscore" if method == "deviation.zscore@v1" else "mad"
        _, _, _, scores = expected((1, 2, 7), algorithm)
        assert result.observed.to_pandas().value.tolist() == [1, 2, 7]
        assert result.score.to_pandas().value.tolist() == list(scores)
        assert digest.finding_count == 0
    elif isinstance(result, mv.MaterializedTimeRunResult):
        assert result.count.to_pandas().value.tolist() == [2]
        assert result.duration.to_pandas().value.tolist() == [pd.Timedelta(days=2)]
        assert str(result.start.to_pandas().value.iloc[0]).startswith("2026-08-02")
        assert str(result.end.to_pandas().value.iloc[0]).startswith("2026-08-04")
        assert digest.finding_count == 0
    elif isinstance(result, mv.MaterializedAssociationResult):
        assert result.coefficient.to_pandas().value.tolist() == [1.0]
        assert result.selected.to_pandas().value.tolist() == [True]
        assert digest.finding_count == 1
    else:
        assert result.prediction.to_pandas().value.tolist() == (
            [7.0, 7.0]
            if method == "forecast.naive@v1"
            else [10.0, 13.0]
            if method == "forecast.drift@v1"
            else [2.0, 7.0]
        )
        assert digest.finding_count == 2
    page = result.findings(limit=100)
    assert len(page.items) == digest.finding_count
    if page.items:
        assert result.finding(page.items[0].finding_id) == page.items[0]
    assert "\n" not in repr(result) and ".show()" in repr(result)


def snapshot(result: Result) -> dict[str, Json]:
    assert result._dataset is not None
    descriptor = result._dataset.artifact.descriptor
    return {
        "artifact": result.state.artifact_ref.ref,
        "rows": json.loads(result.to_pandas().to_json(orient="table", index=False)),
        "contract": json.loads(json.dumps(asdict(result.contract()), default=str)),
        "findings": json.loads(json.dumps(asdict(result.findings(limit=100)), default=str)),
        "primary_receipt_digest": receipt_digest(descriptor.primary_receipt),
        "part_receipt_digests": {p.role: receipt_digest(p) for p in descriptor.parts},
    }


def proof(
    result: Result, method: str, phase: str, form: str, saved: dict[str, Json] | None = None
) -> dict[str, Json]:
    assert result._dataset is not None
    dataset = result._dataset
    retained = dataset.verified()
    physical = next(
        p
        for p in descriptor_plan(
            dataset.artifact.descriptor, result._node.definition
        ).physical_requirements
        if str(p.key.method) == method
    )
    assert isinstance(physical.implementation.qualification, Qualified)
    entity = method.startswith(("deviation.", "association."))
    return {
        "method": method,
        "kernel_proof_class": {
            "produce": "source_kernel",
            "fixed": "fixed_kernel",
            "cold": "fresh_process_source_offline_kernel",
        }[phase],
        "qualification_key": key_json(physical.key),
        "implementation_id": physical.implementation.qualification.implementation_id,
        "implementation_contract_version": "v1",
        "precision_contract": physical.implementation.precision,
        "state_version": "v1",
        "numeric_policy": "r8_numeric_v1",
        "domain": "entity" if entity else "time",
        "key_profile": "composite(string,int64)" if entity else "time_cell:string",
        "origin_profile": ("TABLE" if form == "table" else "PARQUET")
        + ("-NONE" if entity else "-US-UTC-builtin_day"),
        "time_profile": "none" if entity else "builtin_day:grid_us:UTC",
        "retained_parts": [str(p.role) for p in sorted(retained.parts, key=lambda p: p.role)],
        "source_offline": phase != "produce",
        "snapshot": snapshot(result) if saved is None else saved,
        "oracle": "independent rational signed scores; raw 1,2,7 identity/count/endpoints; identical-vector complete-pair coefficient; normative three-model point vectors",
    }
