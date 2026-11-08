"""Public persistence owns durable values, not analysis execution or HTTP."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from threading import Event

import pandas as pd
import pyarrow.parquet as pq
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.result_files import ResultStorageError
from marivo.server.dataframes import report
from marivo.server.wire import Budget, Limits


def read(root: Path, result_id: str) -> dict[str, object]:
    limits = Limits()
    value: object = json.loads(
        report(root, result_id, Budget(limits, "dataframe", limits.deadline(), Event()))
    )
    assert isinstance(value, dict)
    return value


def test_base_public_api_is_source_and_server_independent(tmp_path: Path) -> None:
    script = """
import json, sys
from pathlib import Path
# An installed base package must work even when API dependencies are unavailable.
sys.modules['uvicorn'] = None
import pandas as pd
import marivo.analysis as mv
root = Path(sys.argv[1])
reference = mv.persist_dataframe(pd.DataFrame({'count': [9007199254740993]}), project_root=root)
print(json.dumps(reference.to_dict()))
"""
    wire = json.loads(subprocess.check_output([sys.executable, "-c", script, str(tmp_path)]))
    assert wire["schema"] == "marivo.dataframe_ref/v1"
    assert set(wire) == {"schema", "result_id"}
    assert read(tmp_path, wire["result_id"])["data"] == {
        "layout": ["count"],
        "rows": [["9007199254740993"]],
        "row_count": 1,
    }


def test_reference_and_snapshot_do_not_follow_mutations(tmp_path: Path) -> None:
    source = pd.DataFrame({"count": [12]})
    old = mv.persist_dataframe(source, project_root=tmp_path)
    wire = old.to_dict()
    wire["result_id"] = "changed"
    source.loc[0, "count"] = 99
    new = mv.persist_dataframe(source, project_root=tmp_path)
    assert old.result_id != new.result_id and old.kind == "dataframe"
    with pytest.raises(FrozenInstanceError):
        field = "result_id"
        setattr(old, field, "changed")
    assert read(tmp_path, old.result_id)["data"] == {
        "layout": ["count"],
        "rows": [["12"]],
        "row_count": 1,
    }
    assert read(tmp_path, new.result_id)["data"] == {
        "layout": ["count"],
        "rows": [["99"]],
        "row_count": 1,
    }


def test_frame_reference_requires_explicit_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create("reference", timezone="UTC")
    pending = session.values(columns={"budget": "CNY"}, rows=(((), {"budget": 100}),))
    with pytest.raises(AnalysisError):
        _ = pending.ref
    memory = pending.compute()
    with pytest.raises(AnalysisError):
        _ = memory.ref
    saved = memory.persist()
    reference = saved.ref
    assert reference.kind == "analysis_frame"
    assert reference.to_dict() == {
        "schema": "marivo.frame_ref/v2",
        "store_id": session._runtime.store.store_id,
        "session_id": saved.session_id,
        "artifact_id": saved.artifact_id,
    }
    assert saved.persist().ref == reference


def test_failed_publish_does_not_publish_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = mv.persist_dataframe(pd.DataFrame({"count": [12]}), project_root=tmp_path)
    directory = tmp_path / ".marivo/server/dataframes"
    previous = set(directory.iterdir())

    def fail_replace(source: Path, destination: Path) -> None:
        assert source.is_file()
        raise OSError("disk failure")

    monkeypatch.setattr("marivo.analysis.materialization.result_files.os.replace", fail_replace)
    with pytest.raises(ResultStorageError) as failure:
        mv.persist_dataframe(pd.DataFrame({"count": [99]}), project_root=tmp_path)
    assert failure.value.code == "result_publication_failed"
    assert read(tmp_path, old.result_id)["data"] == {
        "layout": ["count"],
        "rows": [["12"]],
        "row_count": 1,
    }


def test_retained_snapshot_background_session_is_not_required(tmp_path: Path) -> None:
    reference = mv.persist_dataframe(pd.DataFrame({"count": [12]}), project_root=tmp_path)
    path = tmp_path / ".marivo/server/dataframes" / (reference.result_id + ".parquet")
    table = pq.read_table(path)
    assert table.schema.metadata is not None
    metadata = json.loads(table.schema.metadata[b"marivo.dataframe"])
    metadata["session_id"] = "historical-session"
    pq.write_table(
        table.replace_schema_metadata({b"marivo.dataframe": json.dumps(metadata).encode()}), path
    )
    snapshot = read(tmp_path, reference.result_id)
    assert snapshot["data"] == {"layout": ["count"], "rows": [["12"]], "row_count": 1}


def test_explicit_root_does_not_search_ancestors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "marivo.toml").write_text('[project]\nname = "parent"\n')
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    reference = mv.persist_dataframe(pd.DataFrame({"count": [1]}), project_root=child)
    assert read(child, reference.result_id)["kind"] == "dataframe"
