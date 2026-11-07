"""Endpoint discovery never imports code; current references publish atomically."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from marivo.server.endpoints import (
    DataFrameReference,
    EndpointScripts,
    FrameReference,
    current,
    endpoint_lock,
    endpoint_name,
    publish_current,
)
from marivo.server.wire import ServiceError


def test_discovery_is_static_and_repairs_do_not_require_restart(tmp_path: Path) -> None:
    directory = tmp_path / "endpoints" / "sales"
    directory.mkdir(parents=True)
    script = directory / "total.py"
    script.write_text("broken syntax!")
    (directory / "_helper.py").write_text("raise RuntimeError('not an endpoint')")
    registry = EndpointScripts(tmp_path)
    assert registry.names == {"sales/total"}
    with pytest.raises(ServiceError, match="syntax error"):
        registry.require("sales/total")
    sentinel = tmp_path / "imported"
    script.write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n"
        "def main(session: mv.Session) -> mv.AnalysisFrame:\n    return session\n"
    )
    registry.require("sales/total")
    assert not sentinel.exists()
    (directory / "new.py").write_text(script.read_text())
    with pytest.raises(ServiceError, match="not discovered"):
        registry.require("sales/new")


@pytest.mark.parametrize(
    "name", ["../bad", "sales/../bad", "a//b", "a\\b", "Upper", "_private", "a/", "a%2fb"]
)
def test_invalid_endpoint_names_cannot_address_state(name: str) -> None:
    with pytest.raises(ServiceError) as failure:
        endpoint_name(name)
    assert failure.value.code == "invalid_parameter"


def test_current_is_read_only_and_survives_script_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ServiceError) as failure:
        current(tmp_path, "total")
    assert failure.value.code == "result_not_found"
    assert not (tmp_path / ".marivo").exists()
    old = FrameReference("store_old", "session_old", "artifact_old")
    publish_current(tmp_path, "sales/total", old)
    record = tmp_path / ".marivo/server/endpoints/sales/total.json"
    assert json.loads(record.read_text()) == {
        "kind": "analysis_frame",
        "store_id": "store_old",
        "ref": {
            "schema": "marivo.frame_ref/v1",
            "session_id": "session_old",
            "artifact_id": "artifact_old",
        },
    }
    assert current(tmp_path, "sales/total") == old

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("injected publication failure")

    monkeypatch.setattr("marivo.server.endpoints.os.replace", fail_replace)
    with pytest.raises(ServiceError) as failure:
        publish_current(tmp_path, "sales/total", DataFrameReference("df_" + "1" * 32))
    assert failure.value.code == "result_publication_failed"
    assert current(tmp_path, "sales/total") == old
    assert not list(record.parent.glob(".publishing-*"))


def test_invalid_saved_reference_is_not_treated_as_missing(tmp_path: Path) -> None:
    publish_current(tmp_path, "total", DataFrameReference("df_" + "1" * 32))
    record = tmp_path / ".marivo/server/endpoints/total.json"
    record.write_text(
        '{"kind":"dataframe","ref":{"schema":"marivo.dataframe_ref/v1","result_id":"../outside"}}'
    )
    with pytest.raises(ServiceError) as failure:
        current(tmp_path, "total")
    assert failure.value.code == "invalid_result_reference"


def test_failure_after_pointer_replacement_requires_reading_current_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old, newer = DataFrameReference("df_" + "1" * 32), DataFrameReference("df_" + "2" * 32)
    publish_current(tmp_path, "total", old)
    sync = os.fsync

    def fail_directory_sync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("injected post-replacement failure")
        sync(descriptor)

    monkeypatch.setattr("marivo.server.endpoints.os.fsync", fail_directory_sync)
    with pytest.raises(ServiceError) as failure:
        publish_current(tmp_path, "total", newer)
    assert failure.value.code == "result_publication_failed"
    assert current(tmp_path, "total") == newer


def test_endpoint_lock_excludes_threads_and_other_processes(tmp_path: Path) -> None:
    code = """
import sys
from pathlib import Path
from marivo.server.endpoints import endpoint_lock
from marivo.server.wire import ServiceError
try:
    with endpoint_lock(Path(sys.argv[1]), 'total'):
        print('acquired')
except ServiceError as error:
    print(error.code)
"""
    with endpoint_lock(tmp_path, "total"):
        with pytest.raises(ServiceError) as failure, endpoint_lock(tmp_path, "total"):
            pytest.fail("second lock acquired")
        assert failure.value.code == "refresh_in_progress"
        worker = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, check=True
        )
        assert worker.stdout.strip() == "refresh_in_progress"
    worker = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, check=True
    )
    assert worker.stdout.strip() == "acquired"


def test_external_script_symlink_is_not_executed(tmp_path: Path) -> None:
    directory = tmp_path / "endpoints"
    directory.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("def main(session: mv.Session) -> mv.AnalysisFrame:\n    return session\n")
    (directory / "bad.py").symlink_to(outside)
    registry = EndpointScripts(tmp_path)
    with pytest.raises(ServiceError, match="outside"):
        registry.require("bad")
