"""Isolated offline continuation and cold recovery for History qualification."""

import json
import sys
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
from tests.history_r76_oracle import assert_view, views
from tests.lifecycle_r75_fixtures import END, START


def forbidden(*args, **kwargs):
    raise AssertionError("offline History continuation opened source or replayed")


def run(root):
    manifest = json.loads((root / "r76.json").read_text())
    with (
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__enter__", forbidden),
        patch.object(duckdb, "connect", forbidden),
        patch.object(ms, "load", forbidden),
        patch.object(ibis.duckdb, "connect", forbidden),
        patch.object(SourceSession, "batches", forbidden),
        patch("marivo.analysis.materialization.history_execution.execute", forbidden),
    ):
        session = Session._from_runtime(DatasetRuntime(SessionStore(root), manifest["session"]))
        history = session.artifact(manifest["history"])
        expected = views(manifest["rows"], manifest["subject"], manifest["occurrence"])
        for name, artifact in manifest["views"].items():
            result = session.artifact(artifact)
            assert_view(result, name, expected)
        operations = {
            "in_state": lambda: history.read(
                mv.in_state(
                    ms.model_state(model=ms.ref.state_model("commerce.model"), name="done"), at=END
                )
            ),
            "distribution": lambda: history.distribution(at=(START, END)),
            "transitions": history.transitions,
            "violations": history.violations,
            "intervals": history.intervals,
            "dwell": history.dwell,
        }
        for name, operation in operations.items():
            result = operation().execute()
            assert_view(result, name, expected)
            assert result._dataset.state.artifact_ref.ref == manifest["fixed"][name]
    print(json.dumps({"accepted": True, "cells": manifest["cells"]}))


if __name__ == "__main__":
    run(Path(sys.argv[1]))
