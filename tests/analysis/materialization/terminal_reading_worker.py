"""Independent producer and source-offline public terminal reading processes."""

from __future__ import annotations

import os
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import marivo.analysis as mv
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("cold output reading must not load current semantics, sources or a graph")


def main() -> None:
    assert Path(mv.__file__).resolve().parents[2] == Path(__file__).resolve().parents[3], (
        "worker imported a different checkout"
    )
    root, name, mode = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    os.chdir(root)
    if mode == "produce":
        session = mv.session.get_or_create(name, report_timezone="UTC")
        amount = session.catalog.fields.get("sales.orders.amount")
        region = session.catalog.fields.get("sales.orders.region")
        artifact = session.observe(
            {"total": amount.sum(), "mean": amount.mean()}, by=(region,)
        ).persist()
        rows = artifact.to_pandas()
        assert {
            (None if key is None or key is pd.NA else key): value
            for key, value in zip(
                rows[("coordinate", "axis.0")], rows[("value", "total")], strict=True
            )
        } == {None: 5, "A": 9007199254740993}
        print(artifact.artifact.id)
        return
    with ExitStack() as patches:
        for owner, attribute, replacement in (
            (Session, "catalog", property(forbidden)),
            (SourceSession, "__init__", forbidden),
            (SourceSession, "execute", forbidden),
        ):
            patches.enter_context(patch.object(owner, attribute, replacement))
        session = mv.session.resume(name, by="name")
        before = len(session.runs().items)
        artifact = session.persisted_frame(sys.argv[4])
        assert artifact.artifact.id in repr(artifact.artifact)
        assert "backing=stored" in repr(artifact) and ".show()" in repr(artifact)
        artifact.show(n=1)
        frame = artifact.to_pandas()
        assert {
            (None if key is None or key is pd.NA else key): value
            for key, value in zip(
                frame[("coordinate", "axis.0")], frame[("value", "total")], strict=True
            )
        } == {None: 5, "A": 9007199254740993}
        assert dict(frame.attrs["units"])["total"] == "CNY"
        assert artifact.artifact.findings().items == ()
        facts = artifact.artifact.evidence_digest()
        assert any(item.kind == "read" for item in facts.items)
        facts.show()
        assert len(session.graph().artifacts) == 1
        assert len(session.runs().items) == before
        assert session._runtime.store.resources(session.id) == ()
        fixed = artifact.compute()
        positive = artifact.where(artifact["total"] > 0).compute()
        assert len(positive.to_pandas()) == 2
        comparison = artifact.compare(
            artifact, on=((artifact.axes[0], artifact.axes[0]),)
        ).compute()
        assert comparison.to_pandas()[("value", "absolute.total")].tolist() == [0, 0]
        reduced = artifact.reduce({"rows": mv.row_count()}).compute()
        assert reduced.to_pandas()[("value", "rows")].tolist() == [2]
        assert len(session.runs().items) == before
        copied = fixed.persist()
        assert copied._backing.metadata == fixed._backing.metadata
        assert len(session.runs().items) == before + 1
        print("public-cold-ok")


if __name__ == "__main__":
    main()
