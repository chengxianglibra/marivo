"""Public, source-offline reads with real Findings in independent interpreters."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.writer_guard import session_writer_guard
from tests.lazy_materialization_crash_worker import snapshot


def definition(session: mv.Session) -> mv.LogicalDeltaDataset:
    """Describe two independently scoped, dimensioned observations."""
    metric = ms.ref.metric("sales.revenue")
    region = ms.ref.dimension("sales.orders.region")
    current = (
        session.observe(metric, time_scope=mv.time_scope(start="2026-07-01", end="2026-08-01"))
        .with_dimensions(region)
        .aggregate()
    )
    baseline = (
        session.observe(metric, time_scope=mv.time_scope(start="2026-06-01", end="2026-07-01"))
        .with_dimensions(region)
        .aggregate()
    )
    return current.compare(baseline)


def _forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("public retained read attempted source work or reconciliation")


def _digest(session: mv.Session) -> str:
    return hashlib.sha256(
        json.dumps(snapshot(session._runtime), sort_keys=True).encode()
    ).hexdigest()


def _read(session: mv.Session, reference: str) -> dict[str, object]:
    before = _digest(session)
    result = session.artifact(reference)
    assert isinstance(result, mv.MaterializedDeltaDataset)
    rows = result.to_pandas()
    assert sorted(rows["delta"].tolist()) == [5.0, 13.0]
    page = result.findings(limit=1)
    assert len(page.items) == 1 and page.has_more and page.next_cursor is not None
    following = result.findings(limit=1, cursor=page.next_cursor)
    assert len(following.items) == 1 and not following.has_more
    items = (*page.items, *following.items)
    assert len({item.finding_id for item in items}) == 2
    assert result.evidence_digest.finding_count == 2
    assert all(result.finding(item.finding_id) == item for item in items)
    audit = session.revalidate(reference)
    assert (audit.artifact_integrity, audit.storage_authority, audit.evidence_integrity) == (
        "valid",
        "readable",
        "valid",
    )
    result.show()
    result.contract().render()
    assert _digest(session) == before
    return {
        "artifact": reference,
        "evidence": result.evidence_digest.evidence_digest,
        "findings": [item.finding_id for item in items],
        "finding_owners": [item.session_id for item in items],
        "deltas": sorted(rows["delta"].tolist()),
        "snapshot": before,
    }


def _corrupt_unselected_finding(session: mv.Session, reference: str) -> dict[str, object]:
    before = _digest(session)
    result = session.artifact(reference)
    page = result.findings(limit=1)
    other = result.findings(limit=1, cursor=page.next_cursor).items[0]
    store = session._runtime.store
    with store._read() as connection:
        original = connection.execute(
            "SELECT finding_body_payload FROM findings WHERE finding_ref=?", (other.finding_id,)
        ).fetchone()[0]
    with store._write() as connection:
        connection.execute(
            "UPDATE findings SET finding_body_payload=? WHERE finding_ref=?",
            ("private-corruption-canary", other.finding_id),
        )
    try:
        damaged = _digest(session)
        with session_writer_guard(store.layout.lock_path(session.id)):
            reopened = session.artifact(reference)
            assert reopened.findings(limit=1) == page
            assert reopened.finding(page.items[0].finding_id) == page.items[0]
            reopened.show()
            assert len(reopened.to_pandas()) == 2
            for select in (
                lambda: reopened.findings(limit=1, cursor=page.next_cursor),
                lambda: reopened.finding(other.finding_id),
            ):
                with pytest.raises(IntegrityError) as error:
                    select()
                assert "private-corruption-canary" not in str(error.value)
                assert error.value.__cause__ is None and error.value.__context__ is None
            checked = session.revalidate(reference)
            axes = [
                checked.artifact_integrity,
                checked.storage_authority,
                checked.evidence_integrity,
            ]
            assert axes == ["valid", "readable", "invalid"]
            assert "private-corruption-canary" not in checked.render()
        assert _digest(session) == damaged
    finally:
        with store._write() as connection:
            connection.execute(
                "UPDATE findings SET finding_body_payload=? WHERE finding_ref=?",
                (original, other.finding_id),
            )
    assert _digest(session) == before
    return {"axes": axes, "selected_rejected": True, "unselected_readable": True}


def seed_public_project(project: Path) -> None:
    """Prepare two real Delta rows and their production Findings."""
    database = project / "warehouse.duckdb"
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        for identifier, region, day, amount in (
            (1, "north", "20260701", 10),
            (2, "south", "20260702", 20),
            (3, "north", "20260601", 5),
            (4, "south", "20260602", 7),
        ):
            connection.execute(
                "UPDATE orders SET region=?, log_date=?, amount=? WHERE query_id=?",
                [region, day, amount, identifier],
            )


def journey(phase: str, project: Path) -> dict[str, object]:
    os.chdir(project)
    database = project / "warehouse.duckdb"
    state = project / "public-read-state.json"
    if phase == "produce":
        seed_public_project(project)
        owner = mv.session.get_or_create("public-owner", report_timezone="UTC")
        logical = definition(owner)
        assert owner.runs().items == () and not owner._runtime.statistics.statements
        result = logical.execute()
        saved = {"owner": owner.id, "artifact": str(result.state.artifact_ref)}
        state.write_text(json.dumps(saved))
        report = _read(owner, saved["artifact"])
        database.rename(database.with_suffix(".offline"))
        return {"phase": phase, "pid": os.getpid(), "owner": owner.id, **report}

    assert not database.exists()
    saved = json.loads(state.read_text())
    with pytest.MonkeyPatch.context() as patch:
        for name in (
            "_build_backend_from_effective",
            "_effective_kwargs",
            "require_profile_for_backend_type",
        ):
            patch.setattr(admission, name, _forbidden)
        owner = mv.session.resume(saved["owner"], by="id")
        if phase == "continue":
            consumer = mv.session.get_or_create("public-consumer", report_timezone="UTC")
            original_graph = owner.graph()
            original_runs = owner.runs()
            report = _read(consumer, saved["artifact"])
            assert consumer.runs().items == () and consumer.graph().artifacts == ()
            loaded = consumer.artifact(saved["artifact"])
            assert isinstance(loaded, mv.MaterializedDeltaDataset)
            continued = loaded.rank(loaded.fields.get("delta")).limit(1).execute()
            assert continued.to_pandas()["delta"].tolist() == [13.0]
            assert owner.graph() == original_graph and owner.runs() == original_runs
            graph = consumer.graph()
            assert graph.boundary_artifact_refs == (loaded.state.artifact_ref,)
            assert graph.head_artifact_refs == (continued.state.artifact_ref,)
            assert len(graph.runs) == 1
            assert (
                next(
                    a for a in graph.artifacts if a.artifact_ref == loaded.state.artifact_ref
                ).artifact_session_ref
                == owner.id
            )
            saved.update(consumer=consumer.id, downstream=str(continued.state.artifact_ref))
            state.write_text(json.dumps(saved))
            report.update(
                consumer=consumer.id, downstream=saved["downstream"], graph=graph.render()
            )
        else:
            assert phase == "cold"
            consumer = mv.session.resume(saved["consumer"], by="id")
            assert str(definition(owner).execute().state.artifact_ref) == saved["artifact"]
            loaded = consumer.artifact(saved["artifact"])
            assert isinstance(loaded, mv.MaterializedDeltaDataset)
            assert (
                str(loaded.rank(loaded.fields.get("delta")).limit(1).execute().state.artifact_ref)
                == saved["downstream"]
            )
            assert (
                not owner._runtime.statistics.statements
                and not consumer._runtime.statistics.statements
            )
            patch.setattr(admission, "reconcile_session", _forbidden)
            with session_writer_guard(owner._runtime.store.layout.lock_path(owner.id)):
                report = _read(owner, saved["artifact"])
                assert len(mv.session.recent().items) == 2
                assert mv.session.inspect("public-owner").runs.items == owner.runs().items
                assert len(owner.runs().items) == len(consumer.runs().items) == 1
                for session in (owner, consumer):
                    run = session.runs().items[0]
                    assert isinstance(run, mv.SucceededRun)
                    assert session.get_run(run.run_id) == run
                    session.graph().render()
            report["corruption"] = _corrupt_unselected_finding(consumer, saved["artifact"])
            report["consumer"] = saved["consumer"]
            report["downstream"] = saved["downstream"]
        assert not database.exists()
        report["run_ids"] = [s.runs().items[0].run_id for s in (owner, consumer)]
        report["source_fences"] = [s._runtime.statistics.source_fences for s in (owner, consumer)]
    return {"phase": phase, "pid": os.getpid(), "owner": owner.id, **report}


if __name__ == "__main__":
    result = journey(sys.argv[1], Path(sys.argv[2]))
    Path(sys.argv[3]).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
