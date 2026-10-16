"""Independent producer and source-offline public terminal reading processes."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import marivo.analysis as mv
from marivo.analysis.materialization import graph_protocol, graph_store
from marivo.analysis.session.core import Session


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
        assert artifact.to_pandas()[("value", "total")].tolist() == [9007199254740993, 5]
        print(artifact.id)
        return
    Session.catalog = property(forbidden)
    Session.sources = property(forbidden)
    graph_protocol.decode = forbidden
    graph_store.artifact = forbidden
    session = mv.session.resume(name, by="name")
    artifact = session.artifact(sys.argv[4])
    assert artifact.id in repr(artifact)
    artifact.show(n=1)
    frame = artifact.to_pandas()
    assert frame[("value", "total")].tolist() == [9007199254740993, 5]
    assert frame.attrs["marivo"]["quantities"][0]["unit"] == "CNY"
    assert artifact.findings().items == ()
    facts = artifact.evidence_digest()
    assert any(item.kind == "read" for item in facts.items)
    facts.show()
    assert len(session.graph().artifacts) == 1
    print("public-cold-ok")


if __name__ == "__main__":
    main()
