"""Independent producer and source-offline public terminal reading processes."""

from __future__ import annotations

import os
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import marivo.analysis as mv
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("cold output reading must not load current semantics, sources or a graph")


def main() -> None:
    root, name, mode = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    os.chdir(root)
    if mode == "produce":
        session = mv.session.get_or_create(name, report_timezone="UTC")
        amount = session.catalog.fields.get("sales.orders.amount")
        region = session.catalog.fields.get("sales.orders.region")
        artifact = session.observe(
            {"total": amount.sum(), "mean": amount.mean()}, by=(region,)
        ).execute()
        assert artifact.to_pandas()[("value", "total")].tolist() == [5, 9007199254740993]
        print(artifact.id)
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
        artifact = session.artifact(sys.argv[4])
        assert artifact.id in repr(artifact)
        artifact.show(n=1)
        frame = artifact.to_pandas()
        assert frame[("value", "total")].tolist() == [5, 9007199254740993]
        assert frame.attrs["marivo"]["quantities"][0]["unit"] == "CNY"
        assert artifact.findings().items == ()
        facts = artifact.evidence_digest()
        assert any(item.kind == "read" for item in facts.items)
        facts.show()
        assert len(session.graph().artifacts) == 1
        assert len(session.runs().items) == before
        assert session._runtime.store.resources(session.id) == ()
        print("public-cold-ok")


if __name__ == "__main__":
    main()
