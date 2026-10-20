"""Raw-fact hierarchy, current-row counts and terminal classification reads."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

import marivo.analysis as mv
import marivo.semantic as ms
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.packaging.graph_journeys import _create, _frame, _offline, _remove_sources
from tests.shared_fixtures import analysis_dsl_rows, run_ids
from tests.support.json import Json, checked, encode, obj, read


def _verify(values: dict[str, mv.AnalysisFrame]) -> dict[str, Json]:
    facts = analysis_dsl_rows("j1")
    zone: dict[str, str] = {}
    for member, region in facts.customers:
        assert isinstance(member, str) and isinstance(region, str)
        zone[member] = region
    region_oracle: dict[str, int | None] = dict.fromkeys(zone.values())
    channel_oracle: dict[str, int] = {}
    for _, member, channel, _, point, amount in facts.orders:
        assert isinstance(member, str)
        if "2026-08-01" <= point < "2026-09-01":
            assert isinstance(amount, int) and isinstance(channel, str)
            region_oracle[zone[member]] = (region_oracle[zone[member]] or 0) + amount
            channel_oracle[channel] = channel_oracle.get(channel, 0) + amount
    frames = {name: _frame(value) for name, value in values.items()}

    def regions(name: str) -> dict[str, object]:
        result: dict[str, object] = {}
        for row in frames[name].itertuples():
            assert isinstance(row.key_0, str)
            result[row.key_0] = None if pd.isna(row.value) else row.value
        return result

    assert regions("direct") == region_oracle and regions("regions") == region_oracle
    assert frames["channels"].set_index("key_0").value.to_dict() == channel_oracle
    assert frames["total"].value.tolist() == [sum(channel_oracle.values())]
    assert frames["coordinate_count"].value.tolist() == [3]
    assert frames["selected_count"].value.tolist() == [2]
    joined = frames["base"].merge(
        frames["category"], on="key_0", suffixes=("_base", "_category"), validate="one_to_one"
    )
    assert {
        key: None if pd.isna(value) else value
        for key, value in joined.groupby("value_category").value_base.sum(min_count=1).items()
    } == region_oracle
    assert frames["coordinates"].groupby("key_1").value.sum().to_dict() == channel_oracle
    assert len(frames["coordinates"]) == 3 and int((frames["category"].value == "east").sum()) == 2
    return obj(checked({"regions": region_oracle, "channels": channel_oracle}))


def run(root: Path, phase: str) -> dict[str, Json]:
    assert phase in ("produce", "fixed", "cold")
    root = root.resolve()
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    state_path = root / "hierarchy.json"
    if phase == "produce":
        session = _create(root, "j1", "parquet")
        members = session.members(ms.ref.entity("operations.device"))
        category = session.catalog.fields.get("operations.device.zone")
        amount = session.catalog.fields.get("operations.reading.energy")
        time = session.catalog.fields.get("operations.reading.recorded_at")
        device = session.catalog.fields.get("operations.reading.device_id")
        sensor = session.catalog.fields.get("operations.reading.sensor")
        scope = session.time_scope(start="2026-08-01", end="2026-09-01")
        metric = amount.sum(time=time, empty="null")
        base = session.observe({"value": metric}, during=scope, by=members, members=members)
        category_query = session.read(members, {"value": category})
        coordinates = session.observe(
            {"value": metric}, during=scope, by=(device, sensor), members=members
        )
        direct = session.observe(
            {"value": metric}, during=scope, by=members.classify(by=(category,)), members=members
        )
        classified = session.observe(
            {"value": metric},
            during=scope,
            by=members.classify(by=(members.axes[0], category)),
            members=members,
        )
        selected = category_query.where(category_query["value"] == "east")
        queries = {
            "base": base,
            "category": category_query,
            "coordinates": coordinates,
            "direct": direct,
            "regions": classified.reduce(
                {"value": classified["value"].sum()}, by=(classified.axes[1],)
            ),
            "channels": coordinates.reduce(
                {"value": coordinates["value"].sum()}, by=(coordinates.axes[1],)
            ),
            "total": base.reduce({"value": base["value"].sum()}),
            "coordinate_count": coordinates.reduce({"value": mv.row_count()}),
            "selected_count": selected.reduce({"value": mv.row_count()}),
        }
        values = {name: query.persist() for name, query in queries.items()}
        oracle = _verify(values)
        state: dict[str, Json] = {
            "session": session.id,
            "artifacts": {name: value.artifact.id for name, value in values.items()},
            "snapshots": {name: snapshot(value) for name, value in values.items()},
            "oracle": oracle,
        }
        state_path.write_bytes(encode(state))
        _remove_sources(root)
    else:
        state = read(state_path)
        identity = state["session"]
        assert isinstance(identity, str)
        with _offline(root):
            session = mv.session.resume(identity, by="id")
            before = run_ids(session)
            values = {}
            for name, reference in obj(state["artifacts"]).items():
                assert isinstance(reference, str)
                value = session.persisted_frame(reference)
                assert snapshot(value) == obj(state["snapshots"])[name]
                values[name] = value
            oracle = _verify(values)
            assert oracle == state["oracle"] and run_ids(session) == before
    assert session._runtime.store.resources(session.id) == ()
    return {
        "snapshots": state["snapshots"],
        "oracle": oracle,
        "run_count": len(run_ids(session)),
        "new_runs": 0 if phase != "produce" else len(run_ids(session)),
        "resources": 0,
    }
