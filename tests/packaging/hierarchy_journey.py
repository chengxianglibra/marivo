"""Raw-fact hierarchy, current-row counts and retained classification."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Protocol
from unittest.mock import patch

import duckdb
import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.packaging.graph_journeys import _create
from tests.shared_fixtures import analysis_dsl_rows, run_ids
from tests.support.json import Json, checked, encode, obj, read


class Continuation(Protocol):
    def execute(self) -> _MaterializedRead: ...


def run(root: Path, phase: str) -> dict[str, Json]:
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    state_path = root / "hierarchy.json"
    facts = analysis_dsl_rows("j1")
    zone: dict[str, str] = {}
    for member, region in facts.customers:
        assert isinstance(region, str)
        assert isinstance(member, str)
        zone[member] = region
    region_oracle: dict[str, int | None] = dict.fromkeys(zone.values())
    channel_oracle: dict[str, int] = {}
    for _, member, channel, _, point, amount in facts.orders:
        if "2026-08-01" <= point < "2026-09-01":
            assert isinstance(amount, int)
            assert isinstance(channel, str)
            assert isinstance(member, str)
            region_oracle[zone[member]] = (region_oracle[zone[member]] or 0) + amount
            channel_oracle[channel] = channel_oracle.get(channel, 0) + amount
    if phase == "produce":
        session = _create(root, "j1", "parquet")
        members = session.members(ms.ref.entity("operations.device"))
        logical_category = members.read(ms.ref.dimension("operations.device.zone"))
        logical_base = members.observe(
            ms.ref.metric("operations.energy_total"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("operations.reading_device"),
            by=(mv.member(),),
        )
        logical_coordinates = members.observe(
            ms.ref.metric("operations.energy_total"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("operations.reading_device"),
            by=(mv.member(), ms.ref.dimension("operations.reading.sensor")),
        )
        direct_observation = members.observe(
            ms.ref.metric("operations.energy_total"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("operations.reading_device"),
            by=(ms.ref.dimension("operations.device.zone"),),
        )
        assert isinstance(direct_observation, mv.LogicalNumericRelation)
        values: dict[str, _MaterializedRead] = {
            "base": logical_base.execute(),
            "category": logical_category.execute(),
            "coordinates": logical_coordinates.execute(),
            "direct": direct_observation.execute(),
        }
        direct = values["direct"].to_pandas()
        assert {
            row.group: None if row.cell_tag == "null" else row.value for row in direct.itertuples()
        } == region_oracle
        state: dict[str, Json] = {
            "session": session.id,
            "inputs": {name: snapshot(value) for name, value in values.items()},
        }
        state_path.write_bytes(encode(state))
        (root / "warehouse.duckdb").unlink()
        shutil.rmtree(root / "models")
        shutil.rmtree(root / "source_files")
        return {
            "inputs": state["inputs"],
            "oracle": checked({"regions": region_oracle, "channels": channel_oracle}),
        }
    state = read(state_path)
    identity = state["session"]
    assert isinstance(identity, str)
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__init__", forbidden),
        patch.object(duckdb, "connect", forbidden),
        patch.object(ibis.duckdb, "connect", forbidden),
    ):
        session = mv.session.resume(identity, by="id")
        values = {}
        for name, raw in obj(state["inputs"]).items():
            saved = obj(raw)
            reference = saved["artifact"]
            assert isinstance(reference, str)
            restored = session.artifact(reference)
            assert isinstance(restored, _MaterializedRead)
            values[name] = restored
            assert snapshot(values[name]) == saved
        base, category, coordinates = values["base"], values["category"], values["coordinates"]
        assert isinstance(base, mv.MaterializedNumericRelation)
        assert isinstance(category, mv.MaterializedCategoryRelation)
        assert isinstance(coordinates, mv.MaterializedNumericRelation)
        operations: dict[str, Continuation] = {
            "regions": base.group_by(category).rollup(),
            "channels": coordinates.group_by(
                ms.ref.dimension("operations.reading.sensor")
            ).rollup(),
            "total": base.rollup(),
            "coordinate_count": coordinates.aggregate(mv.count()),
            "selected_count": category.where(category.value.eq("east")).aggregate(mv.count()),
        }
        before = run_ids(session)
        outputs = {name: operation.execute() for name, operation in operations.items()}
        assert {
            row.group: None if row.cell_tag == "null" else row.value
            for row in outputs["regions"].to_pandas().itertuples()
        } == region_oracle
        assert outputs["channels"].to_pandas().set_index("group").value.to_dict() == channel_oracle
        assert outputs["total"].to_pandas().value.tolist() == [sum(channel_oracle.values())]
        assert outputs["coordinate_count"].to_pandas().value.tolist() == [3]
        assert outputs["selected_count"].to_pandas().value.tolist() == [2]
        snapshots: dict[str, Json] = {name: snapshot(value) for name, value in outputs.items()}
        if phase == "fixed":
            assert len(run_ids(session) - before) == len(operations)
            state["outputs"] = snapshots
            state_path.write_bytes(encode(state))
        else:
            assert snapshots == state["outputs"] and run_ids(session) == before
        assert session._runtime.store.resources(session.id) == ()
        return {
            "outputs": snapshots,
            "new_runs": len(run_ids(session) - before),
            "resources": 0,
            "oracle": checked({"regions": region_oracle, "channels": channel_oracle}),
        }
