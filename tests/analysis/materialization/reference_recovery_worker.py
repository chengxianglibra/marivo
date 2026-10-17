"""Source-free terminal reads retain reference weights and complete opportunities."""

from __future__ import annotations

import math
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.analysis.statistics.pandas_workflows import plain
from tests.support.json import Json, encode, obj, read

KEYS = (("a", 9007199254740992, 1), ("a", 9007199254740993, 2), ("b", 9007199254740993, 1))


def recipes(outputs: Mapping[str, mv.Artifact]) -> dict[str, Json]:
    """Compute explicit pandas reference, ranking and cohort recipes from saved facts."""
    current = plain(outputs["current"])
    assert current.key_0.tolist() == list(KEYS)
    assert current.value.tolist() == [2, 0, 4]
    bucket = plain(outputs["bucket"])
    assert bucket.key_0.tolist() == list(KEYS) and bucket.bucket.tolist() == ["a", "a", "b"]
    raw = plain(outputs["basis"])
    assert raw.key_0.tolist() == list(KEYS)
    assert (
        raw.amount.tolist()[0] == 2
        and pd.isna(raw.amount.tolist()[1])
        and raw.amount.tolist()[2] == 4
    )
    groups = plain(outputs["groups"])
    assert dict(zip(groups.key_0, groups.value, strict=True)) == {"a": 2, "b": 4}
    assert raw.groupby("bucket")["amount"].sum().to_dict() == {"a": 2, "b": 4}
    weights = plain(outputs["weights"])
    fractions = dict(zip(weights.key_0, weights["value.share"], strict=True))
    assert math.isclose(fractions["a"], 1 / 3) and math.isclose(fractions["b"], 2 / 3)
    assert math.isclose(sum(fractions.values()), 1)
    shares = plain(outputs["share"])
    assert shares.key_0.tolist() == list(KEYS)
    assert shares["value.share"].tolist() == [1 / 3, 0, 2 / 3]
    ranks = current.value.rank(method="dense", ascending=False).astype("int64")
    assert ranks.tolist() == [2, 3, 1]
    standardized = sum(row.value * fractions[row.key_0] for row in groups.itertuples())
    assert math.isclose(standardized, 2 * (1 / 3) + 4 * (2 / 3))
    opportunities = plain(outputs["opportunities"])
    assert len(opportunities) == 6
    assert opportunities.groupby("key_1").size().to_dict() == dict.fromkeys(KEYS, 2)
    by_day = opportunities.groupby("key_0")["value"].sum().to_dict()
    assert by_day == {
        pd.Timestamp("2026-08-01T00:00:00Z"): 6,
        pd.Timestamp("2026-08-02T00:00:00Z"): 0,
    }
    positive = (
        opportunities.assign(positive=opportunities.value > 0).groupby("key_1")["positive"].sum()
    )
    assert positive.to_dict() == {KEYS[0]: 1, KEYS[1]: 0, KEYS[2]: 1}
    cohort = positive[positive >= 1].index.tolist()
    cohort_two = positive[positive >= 2].index.tolist()
    assert cohort == [KEYS[0], KEYS[2]] and cohort_two == []
    selected = plain(outputs["selected"])
    assert selected.key_0.tolist() == cohort and selected.bucket.tolist() == ["a", "b"]
    empty = plain(outputs["empty"])
    assert empty.empty
    penetration = len(cohort) / len(current)
    assert math.isclose(penetration, 2 / 3)
    empty_penetration = None if empty.empty else len(empty) / len(empty)
    assert empty_penetration is None
    return {
        "ranks": ranks.tolist(),
        "cohort": [list(key) for key in cohort],
        "cohort_two": [],
        "penetration": penetration,
        "empty_penetration": empty_penetration,
        "standardized": standardized,
        "weights": fractions,
        "opportunity_count": len(opportunities),
    }


def run(root: Path, phase: str, output: Path) -> None:
    os.chdir(root)
    state = read(root / "r94-reference.json")
    identity = state["session"]
    assert isinstance(identity, str)
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__enter__", forbidden),
        patch.object(SourceSession, "execute", forbidden),
    ):
        session = mv.session.resume(identity, by="id")
        before = len(session.runs().items)
        outputs: dict[str, mv.Artifact] = {}
        for name, saved in obj(state["outputs"]).items():
            item = obj(saved)
            artifact = item["artifact"]
            assert isinstance(artifact, str)
            value = session.artifact(artifact)
            assert snapshot(value) == item["snapshot"]
            outputs[name] = value
        derived = recipes(outputs)
        assert derived == obj(state["derived"])
        assert len(session.runs().items) == before
        assert all(
            not hasattr(value, "standardize") and not hasattr(value, "execute")
            for value in outputs.values()
        )
    output.write_bytes(
        encode({"pid": os.getpid(), "phase": phase, "derived": derived, "run_count": before})
    )


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
