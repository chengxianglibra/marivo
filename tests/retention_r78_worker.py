"""Independent source production, offline continuation and cold exact recovery."""

import json
import shutil
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import duckdb
import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.retention_r78_fixtures import build_retention


def forbidden(*args, **kwargs):
    raise AssertionError("offline retention opened source, loaded semantics or rematched")


def reference(result):
    return result.state.artifact_ref.ref


def snapshot(result):
    table = result.to_pandas()
    keys = [name for name in table.columns if name not in ("value", "cell_tag", "cell_reason")]
    values = [
        [*[row[k] for k in keys], row["value"], row["cell_tag"], row["cell_reason"]]
        for row in table.to_dict("records")
    ]
    facts = dict(result.contract()._facts)
    return {
        "rows": values,
        "facts": {
            k: facts[k]
            for k in (
                "omega_count",
                "known_true_count",
                "known_false_count",
                "unknown_count",
                "deterministic_bounds",
            )
        },
    }


def assert_source(result, subject, occurrence, profile):
    # Full keys come directly from raw fixture identities, without product projection helpers.
    sid = lambda i: (
        (f"sid{i}",)
        if subject == "s"
        else (9007199254740993 + i,)
        if subject == "i"
        else (f"sid{i}", 9007199254740993 + i)
    )
    oid = lambda i: (
        (f"oid{i}",)
        if occurrence == "s"
        else (9007199254740993 + i,)
        if occurrence == "i"
        else (f"oid{i}", 9007199254740993 + i)
    )
    expected = {
        (*sid(s), "commerce.started", *oid(i)): v
        for i, s, v in ((0, 0, True), (2, 0, None), (3, 1, False), (4, 1, None))
    }
    if profile == "P20":
        expected = {
            k: True if k == (*sid(0), "commerce.started", *oid(0)) else None for k in expected
        }
    if profile == "P21":
        expected = {sid(0): True, sid(1): None}
    if profile == "P22":
        expected = {sid(0): None, sid(1): False}
    rows = snapshot(result)["rows"]
    assert {tuple(r[:-3]): r[-3] for r in rows} == expected
    return snapshot(result)


def produce(root):
    config = json.loads((root / "config.json").read_text())
    session, anchors, returning, claims = build_retention(root, **config)
    elapsed = anchors.retention(
        returning, within=mv.elapsed(mv.duration(seconds=10)), completeness=claims
    )
    calendar = anchors.retention(
        returning, within=mv.calendar_days(1, ZoneInfo("America/New_York")), completeness=claims
    )
    values = {
        "P19": elapsed,
        "P20": calendar,
        "P21": elapsed.by_subject(rule=mv.any_anchor()),
        "P22": elapsed.by_subject(rule=mv.every_anchor()),
    }
    data = {"session": session.id, "source": {}, "expected": {}, "fixed": {}, "config": config}
    routes = {}
    for profile, logical in values.items():
        fixed = logical.execute()
        data["source"][profile] = reference(fixed)
        data["expected"][profile] = assert_source(
            fixed, config["subject"], config["occurrence"], profile
        )
        routes[profile] = [
            {"method": str(binding.method), "implementation": binding.implementation_id}
            for binding in fixed._dataset.artifact.descriptor.method_bindings
        ]
    (root / "manifest.json").write_text(json.dumps(data))
    (root / "source.duckdb").unlink()
    shutil.rmtree(root / "models")
    for file in root.glob("*.parquet"):
        file.unlink()
    print(
        json.dumps(
            {
                "phase": "produce",
                "status": "passed",
                "kernels": ["P19", "P20", "P21", "P22"],
                "bindings": routes,
            }
        )
    )


def offline(root, cold):
    data = json.loads((root / "manifest.json").read_text())
    with ExitStack() as guards:
        for owner, name in (
            (SemanticProject, "load"),
            (SourceSession, "__enter__"),
            (SourceSession, "batches"),
            (duckdb, "connect"),
            (ibis.duckdb, "connect"),
            (ms, "load"),
        ):
            guards.enter_context(patch.object(owner, name, forbidden))
        guards.enter_context(patch("marivo.analysis.methods.journey_matching.match", forbidden))
        guards.enter_context(
            patch("marivo.analysis.materialization.journey_execution.execute", forbidden)
        )
        if cold:
            guards.enter_context(
                patch("marivo.analysis.materialization.retention_execution.execute", forbidden)
            )
        session = Session._from_runtime(DatasetRuntime(SessionStore(root), data["session"]))
        for profile, ref in data["source"].items():
            value = session.artifact(ref)
            assert snapshot(value) == data["expected"][profile]
            operations = {
                "true": value.known_true(),
                "false": value.known_false(),
                "unknown": value.unknown(),
                "status": value.status,
            }
            if profile == "P19":
                operations.update(
                    any=value.by_subject(rule=mv.any_anchor()),
                    every=value.by_subject(rule=mv.every_anchor()),
                )
            for name, operation in operations.items():
                key = profile + "." + name
                result = operation.execute()
                if cold:
                    assert reference(result) == data["fixed"][key]["ref"]
                    assert snapshot(result) == data["fixed"][key]["snapshot"]
                else:
                    data["fixed"][key] = {"ref": reference(result), "snapshot": snapshot(result)}
                if name in ("true", "false", "unknown", "status"):
                    assert snapshot(result)["facts"] == data["expected"][profile]["facts"]
                if name == "true":
                    members = (
                        result.members(through=result.subject_binding)
                        if profile in ("P19", "P20")
                        else result.members()
                    )
                    image = members.execute()
                    image_key = key + ".members"
                    if cold:
                        assert reference(image) == data["fixed"][image_key]["ref"]
                    else:
                        data["fixed"][image_key] = {"ref": reference(image)}
        if not cold:
            (root / "manifest.json").write_text(json.dumps(data))
        print(
            json.dumps(
                {
                    "phase": "cold" if cold else "continue",
                    "status": "passed",
                    "kernels": [] if cold else ["P21", "P22"],
                    "source_free": True,
                    "exact_hit": cold,
                    "transports": len(data["fixed"]),
                }
            )
        )


if __name__ == "__main__":
    phase, folder = sys.argv[1:]
    root = Path(folder)
    produce(root) if phase == "produce" else offline(root, phase == "cold")
