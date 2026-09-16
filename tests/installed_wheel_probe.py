"""Installed-origin assertions and a public three-process Dataset journey."""

from __future__ import annotations

import importlib.metadata
import inspect
import json
import os
import sys
import sysconfig
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from marivo.analysis import LogicalDeltaDataset, Session


def assert_installed_origin() -> dict[str, object]:
    import marivo

    site = Path(sysconfig.get_path("purelib")).resolve()
    assert site.is_relative_to(Path(sys.prefix)), "expected an isolated virtual environment"
    package = site / "marivo"
    assert Path(marivo.__file__).resolve() == package / "__init__.py", "foreign Marivo import"
    origins: dict[str, str] = {}
    for name, module in tuple(sys.modules.items()):
        if name == "marivo" or name.startswith("marivo."):
            location = getattr(module, "__file__", None)
            if location is not None:
                assert Path(location).resolve().is_relative_to(package), (name, location)
                origins[name] = str(Path(location).resolve())
            for path in getattr(module, "__path__", ()):
                assert Path(path).resolve().is_relative_to(package), (name, path)
    distributions = [
        item for item in importlib.metadata.distributions() if item.metadata["Name"] == "marivo"
    ]
    assert len(distributions) == 1
    distribution = distributions[0]
    assert (
        Path(str(distribution.locate_file("marivo/__init__.py"))).resolve()
        == package / "__init__.py"
    )
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    assert "archive_info" in direct and "dir_info" not in direct, direct
    assert direct["archive_info"]["hashes"]["sha256"] == os.environ["MARIVO_WHEEL_SHA256"]
    return {
        "python": sys.executable,
        "package": str(package),
        "direct_url": direct,
        "modules": origins,
    }


def pytest_sessionstart(session: pytest.Session) -> None:
    assert_installed_origin()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    result = assert_installed_origin()
    Path(os.environ["MARIVO_INSTALLED_ORIGIN_REPORT"]).write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )


def surface_snapshot() -> list[dict[str, object]]:
    import marivo.analysis as mv
    from marivo._help.render import render_help_text
    from marivo.analysis._capabilities.registry import REGISTRY

    exports = {entry.name: entry for provider in REGISTRY.providers for entry in provider.exports}
    result: list[dict[str, object]] = []
    public_names = mv.__all__
    assert isinstance(public_names, list)
    names = tuple(str(name) for name in public_names)
    assert list(names) == public_names
    assert len(names) == len(exports) == 100
    for family in (
        "Population",
        "Metric",
        "Delta",
        "Attribution",
        "Association",
        "Forecast",
        "Candidate",
        "Event",
        "Lifecycle",
    ):
        logical = getattr(mv, f"Logical{family}Dataset")
        materialized = getattr(mv, f"Materialized{family}Dataset")
        assert issubclass(logical, mv.LogicalDataset)
        assert issubclass(materialized, mv.MaterializedDataset)
        assert not any(hasattr(logical, member) for member in ("render", "show", "to_pandas"))
        assert not hasattr(materialized, "render")
        assert all(callable(getattr(materialized, member)) for member in ("show", "to_pandas"))
    for stale in ("RunRecord", "FrameRefNotFound", "JobNotFoundError"):
        assert not hasattr(mv, stale)
    for name in names:
        value = getattr(mv, name)
        entry = exports[name]
        assert value is entry.implementation
        signatures = {"__call__": str(inspect.signature(value))} if callable(value) else {}
        if inspect.isclass(value):
            for member_name, member in inspect.getmembers(value, inspect.isfunction):
                if not member_name.startswith("_"):
                    signatures[member_name] = str(inspect.signature(member))
        result.append(
            {
                "name": name,
                "target": entry.target,
                "signatures": signatures,
                "help": render_help_text("analysis." + entry.target)[0],
            }
        )
    return result


def _definition(session: Session) -> LogicalDeltaDataset:
    import marivo.analysis as mv
    import marivo.semantic as ms

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


def journey(phase: str, project: Path) -> dict[str, object]:
    import duckdb

    import marivo.analysis as mv
    from marivo.render import AgentResult

    os.chdir(project)
    database = project / "warehouse.duckdb"
    saved_path = project / "installed-result.json"
    if phase == "produce":
        with duckdb.connect(str(database), config={"threads": 1}) as connection:
            connection.execute("UPDATE orders SET region='all'")
            identifiers = connection.execute(
                "SELECT query_id FROM orders ORDER BY query_id"
            ).fetchall()
            assert len(identifiers) == 4
            for identifier, day, amount in zip(
                identifiers,
                ("20260701", "20260702", "20260601", "20260602"),
                (10, 20, 5, 7),
                strict=True,
            ):
                connection.execute(
                    "UPDATE orders SET log_date=?, amount=? WHERE query_id=?",
                    [day, amount, identifier[0]],
                )
        session = mv.session.get_or_create("installed", report_timezone="UTC")
        logical = _definition(session)
        assert isinstance(logical, mv.LogicalDeltaDataset)
        assert session.runs().items == () and not session._runtime.statistics.statements
        assert not any(hasattr(logical, name) for name in ("show", "to_pandas", "render"))
        assert not isinstance(logical, AgentResult)
        assert isinstance(logical.contract(), AgentResult)
        materialized = logical.execute()
        saved_path.write_text(
            json.dumps({"session": session.id, "artifact": str(materialized.state.artifact_ref)})
        )
        assert len(session.runs().items) == 1
    else:
        assert not database.exists()
        from marivo.analysis.materialization import admission

        def forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("retained execution attempted origin datasource access")

        patch = pytest.MonkeyPatch()
        for name in (
            "_build_backend_from_effective",
            "_effective_kwargs",
            "require_profile_for_backend_type",
        ):
            patch.setattr(admission, name, forbidden)
        saved = json.loads(saved_path.read_text())
        session = mv.session.resume(saved["session"], by="id")
        count = len(session.runs().items)
        if phase == "recover":
            materialized = _definition(session).execute()
            assert str(materialized.state.artifact_ref) == saved["artifact"]
            assert len(session.runs().items) == count == 2
            assert not session._runtime.statistics.statements
        else:
            assert phase == "continue"
            loaded = session.artifact(saved["artifact"])
            assert isinstance(loaded, mv.MaterializedDeltaDataset)
            materialized = loaded
            assert count == 1
            downstream = materialized.rank(materialized.fields.get("delta")).limit(1)
            assert isinstance(downstream, mv.LogicalDeltaDataset)
            continued = downstream.execute()
            assert continued.to_pandas()["delta"].tolist() == [18.0]
            assert len(session.runs().items) == 2
        assert session._runtime.statistics.source_fences == 0
    assert isinstance(materialized, mv.MaterializedDeltaDataset)
    assert not isinstance(materialized, AgentResult) and not hasattr(materialized, "render")
    assert materialized.to_pandas()[["current_value", "baseline_value", "delta"]].to_dict(
        "records"
    ) == [{"current_value": 30.0, "baseline_value": 12.0, "delta": 18.0}]
    findings = materialized.findings()
    assert findings.items
    assert materialized.evidence_digest.finding_count == len(findings.items)
    finding = materialized.finding(findings.items[0].finding_id)
    assert finding == findings.items[0]
    artifact_ref = materialized.state.artifact_ref
    run = next(
        item
        for item in session.runs().items
        if isinstance(item, mv.SucceededRun) and item.output_artifact_ref == artifact_ref
    )
    assert session.get_run(run.run_id) == run
    assert session.artifact(artifact_ref).evidence_digest == materialized.evidence_digest
    graph = session.graph(artifact_ref=artifact_ref)
    assert isinstance(graph, mv.SessionGraph)
    if phase == "produce":
        assert graph.head_artifact_refs == (artifact_ref,)
        assert session.graph().head_artifact_refs == (artifact_ref,)
    else:
        assert graph.head_artifact_refs == ()
        heads = session.graph().head_artifact_refs
        assert len(heads) == 1 and artifact_ref not in heads
    graph.render()
    audit = session.revalidate(artifact_ref)
    assert (audit.artifact_integrity, audit.storage_authority, audit.evidence_integrity) == (
        "valid",
        "readable",
        "valid",
    )
    materialized.contract().render()
    materialized.show()
    assert not tuple((project / ".marivo").rglob("*.duckdb"))
    if phase == "produce":
        database.rename(database.with_suffix(".offline"))
    else:
        assert not database.exists()
        patch.undo()
    return {
        "phase": phase,
        "pid": os.getpid(),
        "session": session.id,
        "artifact": str(artifact_ref),
        "run": run.run_id,
        "run_count": len(session.runs().items),
        "evidence": materialized.evidence_digest.evidence_digest,
        "revalidation": {
            "artifact_integrity": audit.artifact_integrity,
            "storage_authority": audit.storage_authority,
            "evidence_integrity": audit.evidence_integrity,
        },
        "finding_ids": [item.finding_id for item in findings.items],
        "graph": graph.render(),
        "execution_statements": session._runtime.statistics.statements,
        "statistics": asdict(session._runtime.statistics),
        "origin": assert_installed_origin(),
    }


if __name__ == "__main__":
    assert_installed_origin()
    mode = sys.argv[1]
    if mode == "surface":
        report: object = surface_snapshot()
    else:
        report = assert_installed_origin() if mode == "guard" else journey(mode, Path(sys.argv[2]))
    Path(sys.argv[-1]).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
