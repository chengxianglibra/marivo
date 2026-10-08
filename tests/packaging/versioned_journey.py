"""Exact historical membership, attribute clocks and subsequent fact observation."""

from __future__ import annotations

import os
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import ibis
import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import SourceSession
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.analysis.materialization.versioned_recovery_worker import run as recover
from tests.support.json import Json, encode

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
recorded_energy=ms.aggregate(name='recorded_energy',measure=energy,agg='sum',time=recorded_at,empty=ms.empty.zero())
settled_energy=ms.aggregate(name='settled_energy',measure=energy,agg='sum',time=settled_at,empty=ms.empty.zero())
reading_subjects=ms.relationship(name='reading_subjects',from_entity=readings,to_entity=subjects,keys=[ms.join_on(reading_subject,subject_id),ms.join_on(reading_tenant,subject_tenant)])
"""
    (semantic / "models.py").write_text(code)


def run(root: Path, phase: str) -> dict[str, Json]:
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    if phase != "produce":
        return recover(root, phase)
    author(root)
    ms.load(workspace_dir=root)
    session = mv.session.get_or_create("installed-versions", report_timezone="UTC")
    august = datetime(2026, 8, 1, tzinfo=timezone.utc)
    september = datetime(2026, 9, 1, tzinfo=timezone.utc)
    originals: dict[str, Json] = {}
    for kind in ("snapshot", "validity"):
        entity = ms.ref.entity("sales.subjects_" + kind)
        with pytest.MonkeyPatch.context() as guards:
            guards.setattr(SourceSession, "batches", forbidden)
            guards.setattr(SourceSession, "compile", forbidden)
            with pytest.raises(AnalysisError):
                session.members(entity)
            current = session.members(entity, at=august)
            with pytest.raises(AnalysisError):
                current.read(ms.ref.measure("sales.subjects_" + kind + ".amount"))
        for label, point in (
            ("august", august),
            ("september", september),
            ("before_september", mv.time_scope(start="2026-08-01", end="2026-09-01").before_end),
            ("before_second_day", mv.time_scope(start="2026-08-01", end="2026-08-02").before_end),
        ):
            value = session.members(entity, at=point).execute()
            frame = value.to_pandas()
            expected = (
                []
                if kind == "snapshot" and label == "before_september"
                else [(FACTS[0][0], "a")]
                if kind == "snapshot" and label == "september"
                else [(row[0], row[1]) for row in FACTS[:3]]
            )
            assert list(zip(frame.member, frame.coord_0, strict=True)) == expected
            originals[kind + ":" + label] = snapshot(value)
        attributes = {
            "numeric": current.read(
                ms.ref.measure("sales.subjects_" + kind + ".amount"), at=august
            ),
            "category": current.read(
                ms.ref.dimension("sales.subjects_" + kind + ".tenant"), at=august
            ),
            "boolean": current.read(
                ms.ref.dimension("sales.subjects_" + kind + ".enabled"), at=august
            ),
            "temporal": current.read(
                ms.ref.time_dimension("sales.subjects_" + kind + ".beginning"), at=august
            ),
        }
        for label, logical in attributes.items():
            attribute = logical.execute()
            assert (
                attribute.to_pandas().value.tolist()
                == {
                    "numeric": [row[4] for row in FACTS[:3]],
                    "category": [row[1] for row in FACTS[:3]],
                    "boolean": [row[4] > 15 for row in FACTS[:3]],
                    "temporal": [date.fromisoformat(row[2]) for row in FACTS[:3]],
                }[label]
            )
            originals[kind + ":" + label] = snapshot(attribute)
    subjects = session.members(ms.ref.entity("sales.subjects"))
    historical = subjects.read(
        ms.ref.measure("sales.subjects_snapshot.amount"),
        at=august,
        via=ms.ref.relationship("sales.subject_profiles"),
    )
    members = historical.where(historical.value.gt(15)).members()
    assert isinstance(members, mv.LogicalAnalysisDomain)
    originals["historical:selection"] = snapshot(historical.execute())
    extra_oracles: dict[str, Json] = {}
    for role, month, expected_values in (
        ("recorded", "09", [11, 13]),
        ("settled", "09", [0, 0]),
        ("settled", "10", [11, 13]),
    ):
        following = members.observe(
            ms.ref.metric("sales." + role + "_energy"),
            during=mv.time_scope(start=f"2026-{month}-01", end=f"2026-{int(month) + 1:02}-01"),
            via=ms.ref.relationship("sales.reading_subjects"),
        ).execute()
        assert following.to_pandas().value.tolist() == expected_values
        originals[role + ":" + month] = snapshot(following)
        extra_oracles[role + ":" + month] = sum(expected_values)
    state: dict[str, Json] = {
        "session": session.id,
        "originals": originals,
        "extra_oracles": extra_oracles,
    }
    (root / "r94-versions.json").write_bytes(encode(state))
    (root / "source.duckdb").unlink()
    shutil.rmtree(root / "models")
    return {
        "pid": os.getpid(),
        "originals": originals,
        "resources": 0,
        "oracle": "Exact raw composite identities, historical values and independent recorded/settled clocks.",
    }
