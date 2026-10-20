"""Exact historical membership, attribute clocks and subsequent fact observation."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path

import ibis
import pyarrow as pa

import marivo.analysis as mv
import marivo.semantic as ms
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.packaging.graph_journeys import _frame, _offline, _ordinary_keys, _remove_sources
from tests.shared_fixtures import run_ids
from tests.support.json import Json, checked, encode, obj, read

FACTS = (
    (9007199254740992, "a", "2026-08-01", "2026-09-01", 10),
    (9007199254740993, "a", "2026-08-01", None, 20),
    (9007199254740993, "b", "2026-08-01", None, 30),
    (9007199254740992, "a", "2026-09-01", None, 40),
)


def author(root: Path) -> None:
    (root / "models/datasources").mkdir(parents=True)
    semantic = root / "models/semantic/sales"
    semantic.mkdir(parents=True)
    (root / "marivo.toml").write_text('[project]\nname="installed-versions"\n')
    database = root / "source.duckdb"
    connection = ibis.duckdb.connect(database, threads=1)
    try:
        connection.create_table(
            "subjects",
            pa.table(
                {
                    "id": [row[0] for row in FACTS[:3]],
                    "tenant": [row[1] for row in FACTS[:3]],
                }
            ),
        )
        connection.create_table(
            "history",
            pa.table(
                {
                    "id": pa.array([row[0] for row in FACTS], type=pa.int64()),
                    "tenant": [row[1] for row in FACTS],
                    "beginning": pa.array(
                        [date.fromisoformat(row[2]) for row in FACTS], type=pa.date32()
                    ),
                    "ending": pa.array(
                        [None if row[3] is None else date.fromisoformat(row[3]) for row in FACTS],
                        type=pa.date32(),
                    ),
                    "amount": [row[4] for row in FACTS],
                    "enabled": [row[4] > 15 for row in FACTS],
                }
            ),
        )
        connection.create_table(
            "readings",
            pa.table(
                {
                    "reading_id": ["r1", "r2", "r3"],
                    "id": [row[0] for row in FACTS[:3]],
                    "tenant": [row[1] for row in FACTS[:3]],
                    "energy": [7, 11, 13],
                    "recorded_at": pa.array(
                        [datetime(2026, 9, 5, tzinfo=timezone.utc)] * 3,
                        type=pa.timestamp("us", tz="UTC"),
                    ),
                    "settled_at": pa.array(
                        [datetime(2026, 10, 5, tzinfo=timezone.utc)] * 3,
                        type=pa.timestamp("us", tz="UTC"),
                    ),
                }
            ),
        )
    finally:
        connection.disconnect()
    (root / "models/datasources/warehouse.py").write_text(
        f"import marivo.datasource as md\nmd.duckdb(name='warehouse',path={str(database)!r})\n"
    )
    (semantic / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales',owner='Analytics',default=True)\n"
    )
    code = "import marivo.datasource as md\nimport marivo.semantic as ms\n"
    for kind in ("snapshot", "validity"):
        name = "subjects_" + kind
        version = (
            f"ms.snapshot(partition_field=ms.ref.time_dimension('sales.{name}.beginning'),grain='day',timezone='UTC')"
            if kind == "snapshot"
            else f"ms.validity(valid_from=ms.ref.time_dimension('sales.{name}.beginning'),valid_to=ms.ref.time_dimension('sales.{name}.ending'),interval='closed_open',open_end=(None,),timezone='UTC')"
        )
        code += f"{name}=ms.entity(name={name!r},datasource=ms.ref.datasource('warehouse'),source=md.table('history'),primary_key=['id','tenant'],versioning={version})\n"
        for field in ("id", "tenant", "enabled"):
            code += f"{name}_{field}=ms.dimension_column(name={field!r},entity={name},column={field!r})\n"
        for field in ("beginning", "ending"):
            code += f"{name}_{field}=ms.time_dimension_column(name={field!r},entity={name},column={field!r},granularity='day')\n"
        code += f"{name}_amount=ms.measure_column(name='amount',entity={name},column='amount',additivity=ms.additive_all())\n"
    code += """subjects=ms.entity(name='subjects',datasource=ms.ref.datasource('warehouse'),source=md.table('subjects'),primary_key=['id','tenant'])
subject_id=ms.dimension_column(name='id',entity=subjects,column='id')
subject_tenant=ms.dimension_column(name='tenant',entity=subjects,column='tenant')
subject_profiles=ms.relationship(name='subject_profiles',from_entity=subjects,to_entity=subjects_snapshot,keys=[ms.join_on(subject_id,subjects_snapshot_id),ms.join_on(subject_tenant,subjects_snapshot_tenant)])
readings=ms.entity(name='readings',datasource=ms.ref.datasource('warehouse'),source=md.table('readings'),primary_key=['reading_id'])
reading_id=ms.dimension_column(name='reading_id',entity=readings,column='reading_id')
reading_subject=ms.dimension_column(name='id',entity=readings,column='id')
reading_tenant=ms.dimension_column(name='tenant',entity=readings,column='tenant')
energy=ms.measure_column(name='energy',entity=readings,column='energy',additivity=ms.additive_all(),unit='kWh')
recorded_at=ms.time_dimension_column(name='recorded_at',entity=readings,column='recorded_at',granularity='day',parse=ms.timestamp(timezone='UTC'))
settled_at=ms.time_dimension_column(name='settled_at',entity=readings,column='settled_at',granularity='day',parse=ms.timestamp(timezone='UTC'))
reading_subjects=ms.relationship(name='reading_subjects',from_entity=readings,to_entity=subjects,keys=[ms.join_on(reading_subject,subject_id),ms.join_on(reading_tenant,subject_tenant)])
reading_validity=ms.relationship(name='reading_validity',from_entity=readings,to_entity=subjects_validity,keys=[ms.join_on(reading_subject,subjects_validity_id),ms.join_on(reading_tenant,subjects_validity_tenant)])
finalized=ms.finalize()
energy_value=finalized.resolve(energy)
recorded_value=finalized.resolve(recorded_at)
settled_value=finalized.resolve(settled_at)
@ms.metric()
def recorded_energy():
    return energy_value.sum(time=recorded_value)
@ms.metric()
def settled_energy():
    return energy_value.sum(time=settled_value)
"""
    (semantic / "models.py").write_text(code)


def _verify(values: dict[str, mv.AnalysisFrame]) -> dict[str, Json]:
    first, second = 9007199254740992, 9007199254740993
    keys = [(first, "a"), (second, "a"), (second, "b")]
    for name in ("snapshot_aug", "validity_aug", "validity_sep", "attributes_aug"):
        frame = _frame(values[name])
        assert frame[_ordinary_keys(frame)[0]].tolist() == keys
        assert frame.value.tolist() == ([40, 20, 30] if name == "validity_sep" else [10, 20, 30])
    for name, expected in (("snapshot_sep", [40]), ("attribute_sep", [40])):
        frame = _frame(values[name])
        assert frame[_ordinary_keys(frame)[0]].tolist() == [(first, "a")]
        assert frame.value.tolist() == expected
    expected_selected = [(second, "a"), (second, "b")]
    for kind in ("snapshot", "validity"):
        chosen = _frame(values[kind + "_chosen"])
        assert chosen[_ordinary_keys(chosen)[0]].tolist() == expected_selected
        for clock, wanted in (
            ("recorded", [11, 13]),
            ("settled_early", [0, 0]),
            ("settled", [11, 13]),
        ):
            frame = _frame(values[kind + "_" + clock])
            assert frame[_ordinary_keys(frame)[0]].tolist() == expected_selected
            assert frame.value.tolist() == wanted
    return obj(
        checked(
            {
                "complete_keys": [list(key) for key in keys],
                "selected": [list(key) for key in expected_selected],
                "august_values": [10, 20, 30],
                "september_validity_values": [40, 20, 30],
                "snapshot_september_keys": [list(keys[0])],
                "attribute_clock": [10, 40],
                "recorded": [11, 13],
                "settled_early": [0, 0],
                "settled": [11, 13],
                "raw_facts": [list(row) for row in FACTS],
            }
        )
    )


def run(root: Path, phase: str) -> dict[str, Json]:
    assert phase in ("produce", "fixed", "cold")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    os.chdir(root)
    state_path = root / "versioned.json"
    if phase == "produce":
        author(root)
        session = mv.session.create("versioned", timezone="UTC")
        august = session.instant("2026-08-01")
        september = session.instant("2026-09-01")
        queries: dict[str, mv.AnalysisFrame] = {}
        for kind in ("snapshot", "validity"):
            entity = f"sales.subjects_{kind}"
            members = session.members(session.catalog.require(ms.ref.entity(entity)), at=august)
            amount = session.catalog.fields.get(entity + ".amount")
            enabled = session.catalog.fields.get(entity + ".enabled")
            queries[kind + "_aug"] = session.read(members, {"value": amount.at(august)})
            queries[kind + "_sep"] = session.read(
                session.members(session.catalog.require(ms.ref.entity(entity)), at=september),
                {"value": amount.at(september)},
            )
            classification = session.read(members, {"enabled": enabled.at(august)})
            enabled_value = True
            fixed = classification.where(classification["enabled"] == enabled_value).compute()
            chosen = session.population_from(
                fixed,
                on=(
                    (
                        next(axis for axis in fixed.axes if axis.role == "identity"),
                        session.catalog.entities.get(entity),
                    ),
                ),
            )
            queries[kind + "_chosen"] = session.frame(chosen)
            for clock, start, end in (
                ("recorded", "2026-09-01", "2026-10-01"),
                ("settled_early", "2026-09-01", "2026-10-01"),
                ("settled", "2026-10-01", "2026-11-01"),
            ):
                energy = session.catalog.fields.get("sales.readings.energy")
                time = session.catalog.fields.get(
                    "sales.readings." + ("recorded_at" if clock == "recorded" else "settled_at")
                )
                metric = energy.sum(time=time, empty="zero")
                queries[kind + "_" + clock] = session.observe(
                    {"value": metric},
                    by=mv.product(session.time_scope(start=start, end=end), chosen),
                )
        stable = session.members(session.catalog.require(ms.ref.entity("sales.subjects")))
        profile = session.catalog.fields.get("sales.subjects_snapshot.amount").via(
            (session.catalog.relationships.get("sales.subject_profiles"),)
        )
        queries["attributes_aug"] = session.read(stable, {"value": profile.at(august)})
        first = mv.coordinates(
            by=(mv.identity(session.catalog.entities.get("sales.subjects")),),
            keys=(((9007199254740992, "a"),),),
        )
        queries["attribute_sep"] = session.read(first, {"value": profile.at(september)})
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
            session = mv.session.resume(identity)
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
        "resources": 0,
    }
