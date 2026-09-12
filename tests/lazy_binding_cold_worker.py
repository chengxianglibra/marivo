"""Fresh-process captured-source execution and source-free binding recovery."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from marivo._compat import Never
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.targets import (
    LocalTarget,
    ObjectTarget,
    S3Access,
)
from marivo.analysis.observation.source_bindings import SourceBindingScopes
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry

ALPHA = "private-binding-cold-alpha-6c8f3b"
BETA = "private-binding-cold-beta-7d2e91"


def forbidden(*args: object, **kwargs: object) -> Never:
    raise AssertionError("execution attempted ambient binding or cold source work")


class ForbiddenAmbient:
    def get(self) -> Never:
        forbidden()


def snapshot(runtime: DatasetRuntime) -> dict[str, int]:
    with sqlite3.connect(runtime.store.db_path.as_uri() + "?mode=ro", uri=True) as connection:
        return {
            name: int(connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0])
            for name in (
                "analysis_action_runs",
                "analysis_action_run_terminals",
                "dataset_artifacts",
                "dataset_evidence",
                "action_resource_journal",
            )
        }


def run(mode: str, kind: str, project: Path, url: str, session: str) -> dict[str, object]:
    registry, sidecar = make_execution_registry(project / "warehouse.duckdb", api_url=url)
    target = (
        LocalTarget()
        if kind == "engine"
        else ObjectTarget("fixture")
        if kind == "object"
        else LocalTarget()
    )
    bindings = (
        (
            S3Access(
                "fixture",
                os.environ["MARIVO_TEST_S3_ENDPOINT"],
                os.environ["MARIVO_TEST_S3_BUCKET"],
                "minioadmin",
                "minioadmin",
            ),
        )
        if kind == "object"
        else ()
    )
    runtime = (
        DatasetRuntime.create(project, "binding-cold", target=target, object_bindings=bindings)
        if mode == "produce"
        else DatasetRuntime.open(project, session, object_bindings=bindings)
    )
    sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
    logicals = []
    for value in (ALPHA, BETA):
        with sources.source_bindings({ref.entity("sales.api"): {"tenant": value}}):
            logicals.append(sources.observe(ref.metric("sales.api_value")))
    before = snapshot(runtime)
    results: list[dict[str, object]] = []
    with ExitStack() as checks:
        checks.enter_context(patch.object(SourceBindingScopes, "capture", forbidden))
        checks.enter_context(
            patch.object(sources._owner.binding_scopes, "_active", ForbiddenAmbient())
        )
        if mode == "cold":
            for name in ("place", "compile_dataset", "_build_backend_from_effective", "supervise"):
                checks.enter_context(patch.object(admission, name, forbidden))
        for logical in logicals:
            materialized = logical.execute()
            metric_value: object = materialized.to_pandas().loc[0, "api_value"]
            assert isinstance(metric_value, int | float)
            results.append(
                {
                    "definition": logical.definition_fingerprint,
                    "artifact": materialized.state.artifact_ref.ref,
                    "run": materialized.state.producing_run_ref,
                    "value": float(metric_value),
                    "statistics": asdict(runtime.statistics),
                }
            )
    with sqlite3.connect(runtime.store.db_path.as_uri() + "?mode=ro", uri=True) as connection:
        persisted = "\n".join(connection.iterdump())
    diagnostic = (
        repr(logicals)
        + repr(
            [{key: value for key, value in item.items() if key != "statistics"} for item in results]
        )
        + persisted
    )
    assert all(value not in diagnostic for value in (ALPHA, BETA))
    return {
        "pid": os.getpid(),
        "session": runtime.session_ref,
        "kind": kind,
        "before": before,
        "after": snapshot(runtime),
        "results": results,
        "ambient_lookup_forbidden": True,
        "raw_values_absent_from_metadata": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "cold"))
    parser.add_argument("kind", choices=("local", "engine", "object"))
    parser.add_argument("project", type=Path)
    parser.add_argument("url")
    parser.add_argument("--session", default="")
    args = parser.parse_args()
    print(
        json.dumps(run(args.mode, args.kind, args.project, args.url, args.session), sort_keys=True)
    )
