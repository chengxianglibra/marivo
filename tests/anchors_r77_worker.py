"""Three-process public Anchor production, offline continuation and exact recovery."""

import json
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import duckdb
import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.anchors_r77_fixtures import build_anchors, event_anchors, journey, observations
from tests.anchors_r77_oracle import assert_result, expected


def forbidden(*args, **kwargs):
    raise AssertionError("offline Anchor continuation opened source, loaded semantics or rematched")


def artifact(value):
    return value.state.artifact_ref.ref


def produce(root):
    config = json.loads((root / "config.json").read_text())
    all_k = config.pop("all_K", False)
    session, members, window, claims, values = build_anchors(root, **config)
    anchors = event_anchors(session, members, window)
    source = {"P17": artifact(anchors.execute())}
    journeys = {}
    for profile, shared in (("P18", False), ("P48", True)):
        logical = journey(session, members, window, claims, shared=shared)
        fixed = logical.execute()
        journeys[profile] = artifact(fixed)
        result = session.anchors(logical, population=members, during=window).execute()
        source[profile] = artifact(result)
    for calendar, start in ((False, 25), (True, 37)):
        for i, operation in enumerate(observations(anchors, calendar=calendar)):
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
        "all_K": all_k,
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    (root / "source.duckdb").unlink()
    for path in root.glob("*.parquet"):
        path.unlink()
    print(json.dumps({"phase": "produce", "accepted": True, "outputs": len(source)}))


def offline(root, cold):
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
        session = Session._from_runtime(
            DatasetRuntime(SessionStore._graph_store(root), data["session"])
        )
        for profile, reference in data["source"].items():
            result = session.artifact(reference)
            assert artifact(result) == reference
            if profile in ("P17", "P18", "P48"):
                assert isinstance(result, mv.MaterializedAnchorDomain)
                assert len(result.to_pandas()) == len(
                    expected(data["rows"], data["config"]["subject"], data["config"]["occurrence"])
                )
                result.subjects(
                    ms.participant_role(event=ms.ref.event("commerce.started"), name="subject")
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
            for action in continued.contract().actions if data.get("all_K", False) else ():
                if "summarize(mv." in action.call:
                    method = action.call.split("summarize(mv.")[1].split("(")[0]
                    statistic = (
                        continued.where(continued.value.is_defined())
                        .summarize(getattr(mv, method)())
                        .execute()
                    )
                    name = profile + "/" + method
                    executed.append(name)
                    if cold:
                        assert artifact(statistic) == data["fixed"][name], name
                    else:
                        data["fixed"][name] = artifact(statistic)
                elif action.call == "relation.members()":
                    image = continued.members().execute()
                    name = profile + "/members"
                    executed.append(name)
                    if cold:
                        assert artifact(image) == data["fixed"][name], name
                    else:
                        data["fixed"][name] = artifact(image)
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
                "exact_hit": cold,
            }
        )
    )


if __name__ == "__main__":
    phase, path = sys.argv[1:]
    root = Path(path)
    produce(root) if phase == "produce" else offline(root, phase == "cold")
