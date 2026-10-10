"""Three-process public Anchor production, offline continuation and exact recovery."""

import json
import os
import shutil
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import duckdb
import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.journey.anchors_fixtures import (
    build_anchors,
    event_anchors,
    journey,
    observations,
)
from tests.analysis.journey.anchors_oracle import assert_result, expected

# All eleven operations keep source/fixed and same-process recovery coverage in
# test_empty_anchor_domain_retains_exact_schema. Fresh-process recovery keeps
# one sample per distinct carrier/path shape instead of repeating each method.
ELAPSED_RECOVERY_INDICES = (9,)
CALENDAR_RECOVERY_INDICES = (0,)


def forbidden(*args, **kwargs):
    raise AssertionError("offline Anchor continuation opened source, loaded semantics or rematched")


def artifact(value):
    return value.state.artifact_ref.ref


def produce(root: Path) -> None:
    config = json.loads((root / "config.json").read_text())
    session, members, window, claims, values = build_anchors(root, **config)
    anchors = event_anchors(session, members, window)
    source = {"P17": artifact(anchors.execute())}
    journeys = {}
    # A single Journey-backed Anchor carries the recovery path. Shared-start
    # assignment is covered by source/fixed tests and has the same persisted type.
    for profile, shared in (("P18", False),):
        logical = journey(session, members, window, claims, shared=shared)
        fixed = logical.execute()
        journeys[profile] = artifact(fixed)
        result = session.anchors(logical, population=members, during=window).execute()
        source[profile] = artifact(result)
    for calendar, start in ((False, 25), (True, 37)):
        # Direct family tests own every numeric operation; cold recovery keeps each distinct shape.
        retained = CALENDAR_RECOVERY_INDICES if calendar else ELAPSED_RECOVERY_INDICES
        for i, operation in enumerate(observations(anchors, calendar=calendar)):
            if i not in retained:
                continue
            result = operation.execute()
            assert_result(
                result, values, config["subject"], config["occurrence"], i, calendar=calendar
            )
            source[f"P{start + i}"] = artifact(result)
    manifest = {
        "session": session.id,
        "source": source,
        "journeys": journeys,
        "members": artifact(members.execute()),
        "rows": values,
        "config": config,
        "fixed": {},
        "window": window.model_dump(mode="json"),
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    (root / "source.duckdb").unlink()
    shutil.rmtree(root / "models")
    for path in root.glob("*.parquet"):
        path.unlink()
    print(json.dumps({"phase": "produce", "accepted": True, "outputs": len(source)}))


def offline(root: Path, cold: bool) -> None:
    os.chdir(root)
    data = json.loads((root / "manifest.json").read_text())
    executed = []
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
        guards.enter_context(
            patch("marivo.analysis.materialization.journey_execution.execute", forbidden)
        )
        guards.enter_context(patch("marivo.analysis.methods.journey_matching.match", forbidden))
        session = mv.session.resume(data["session"], by="id")
        for profile, reference in data["source"].items():
            result = session.artifact(reference)
            assert artifact(result) == reference
            if profile in ("P17", "P18", "P48"):
                assert isinstance(result, mv.MaterializedAnchorDomain)
                assert len(result.to_pandas()) == len(
                    expected(data["rows"], data["config"]["subject"], data["config"]["occurrence"])
                )
                if profile == "P17":
                    continue
                operation = session.anchors(
                    session.artifact(data["journeys"][profile]),
                    population=session.artifact(data["members"]),
                    during=mv.time_scope(
                        **{k: v for k, v in data["window"].items() if k in ("start", "end")}
                    ),
                )
            else:
                index = int(profile[1:])
                calendar = index >= 37
                offset = index - (37 if calendar else 25)
                assert_result(
                    result,
                    data["rows"],
                    data["config"]["subject"],
                    data["config"]["occurrence"],
                    offset,
                    calendar=calendar,
                )
                operation = result.where(result.value.is_defined())
            continued = operation.execute()
            if cold:
                assert artifact(continued) == data["fixed"][profile], (
                    profile,
                    artifact(continued),
                    data["fixed"][profile],
                )
            else:
                data["fixed"][profile] = artifact(continued)
            executed.append(profile)
        if not cold:
            (root / "manifest.json").write_text(json.dumps(data))
    assert len(executed) == len(data["fixed"])
    print(
        json.dumps(
            {
                "phase": "cold" if cold else "continue",
                "accepted": True,
                "fixed_K": len(executed),
                "executed_K": executed,
            }
        )
    )


if __name__ == "__main__":
    phase, path = sys.argv[1:]
    root = Path(path)
    if phase == "produce_fixed":
        produce(root)
        offline(root, False)
    elif phase == "produce":
        produce(root)
    else:
        offline(root, phase == "cold")
