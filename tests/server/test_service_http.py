"""Exercise the installed CLI protocol over actual loopback sockets."""

from __future__ import annotations

import http.client
import json
import os
import select
import sqlite3
import ssl
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol, TypeAlias
from urllib.parse import urlencode

import pytest
from jsonschema import Draft202012Validator

from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.layout import MaterializationLayout
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.terminal_store import manifest
from tests.analysis.materialization.terminal_recovery_worker import method_result

_TOKEN = "test-admin-token"
_ORIGIN = "https://analysis.example.test"
_CLI = [sys.executable, "-m", "marivo"]


# Independent JSON client. Tests validate decoded fields against the published
# schema and their own expected values instead of reusing server DTOs.
Document: TypeAlias = dict[str, Any]
Reply: TypeAlias = tuple[int, dict[str, str], Document | None]


class Request(Protocol):
    def __call__(
        self,
        path: str,
        *,
        method: str = "GET",
        bearer: str | None = _TOKEN,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> Reply: ...


@contextmanager
def server(
    root: Path,
    *args: str,
    token: str = _TOKEN,
    context: ssl.SSLContext | None = None,
    command: Sequence[str] = _CLI,
) -> Iterator[Request]:
    process = subprocess.Popen(
        [*command, "serve", "--project-root", str(root), "--port", "0", *args],
        env={**os.environ, "MARIVO_API_TOKEN": token},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None and process.stderr is not None
    try:
        assert select.select([process.stdout], [], [], 30)[0], "Service did not become ready"
        line = process.stdout.readline()
        assert line, process.stderr.read()
        assert token not in line
        ready = json.loads(line)
        assert ready == {
            "host": "127.0.0.1",
            "port": ready["port"],
            "protocol": "https" if context else "http",
            "pid": process.pid,
            "marivo_version": ready["marivo_version"],
            "api_version": 2,
        }
        assert ready["port"] > 0

        def request(
            path: str,
            *,
            method: str = "GET",
            bearer: str | None = token,
            headers: Mapping[str, str] | None = None,
            body: bytes | None = None,
        ) -> Reply:
            if context is None:
                connection = http.client.HTTPConnection(ready["host"], ready["port"], timeout=35)
            else:
                connection = http.client.HTTPSConnection(
                    ready["host"],
                    ready["port"],
                    timeout=35,
                    context=context,
                )
            try:
                selected = dict(headers or {})
                if bearer is not None:
                    selected["Authorization"] = "Bearer " + bearer
                connection.request(method, path, body=body, headers=selected)
                response = connection.getresponse()
                payload = response.read()
                assert token.encode() not in payload
                decoded: object = json.loads(payload) if payload else None
                assert decoded is None or isinstance(decoded, dict)
                return (
                    response.status,
                    {key.lower(): value for key, value in response.getheaders()},
                    decoded,
                )
            finally:
                connection.close()

        yield request
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            pytest.fail("Read service did not shut down")
        assert not stdout
        assert token not in stderr
        assert process.returncode in (0, -15)


def _produce(root: Path, *args: str) -> dict[str, str]:
    script = Path(__file__).with_name("producer.py")
    decoded: object = json.loads(
        subprocess.check_output([sys.executable, str(script), str(root), *args])
    )
    assert isinstance(decoded, dict)
    return {str(key): str(value) for key, value in decoded.items()}


def _path(ids: Mapping[str, str], key: str = "artifact_id") -> str:
    return f"/v2/stores/{ids['store_id']}/sessions/{ids['session_id']}/frames/{ids[key]}"


def _ok(reply: Reply) -> Document:
    status, _, value = reply
    assert status == 200 and value is not None, value
    return value


def _error(reply: Reply, status: int, code: str) -> Document:
    actual, _, value = reply
    assert actual == status and value is not None, value
    error: object = value["error"]
    assert isinstance(error, dict)
    assert set(error) == {"code", "message", "location", "repair", "target"}
    assert error["code"] == code
    assert all(error[key] for key in ("location", "message", "repair"))
    return error


def _schema(spec: Document, name: str, payload: object) -> None:
    schema = {"$ref": f"#/components/schemas/{name}", "components": spec["components"]}
    Draft202012Validator(schema).validate(payload)


def test_complete_reports_restart_and_budget(tmp_path: Path) -> None:
    ids = _produce(tmp_path, "--extended")
    path = _path(ids)
    with server(tmp_path, "--max-response-mib", "1") as request:
        report = _ok(request(path))
        assert report["ref"] == {
            "schema": "marivo.frame_ref/v2",
            "store_id": ids["store_id"],
            "session_id": ids["session_id"],
            "artifact_id": ids["artifact_id"],
        }
        assert report["description"] == "service fixture"
        assert report["data"]["rows"][1][-1] is True and report["data"]["rows"][1][1] is None
        assert report["data"]["rows"][2][-1] is False and report["data"]["rows"][2][1] is None
        assert report["data"]["row_count"] == 3
        # Arrow's valid minimum nanosecond timestamp must not become pandas NaT/null.
        assert report["schema"]["values"]["event_time"]["nullable"] is False
        assert report["data"]["rows"][2][2] == "1677-09-21T00:12:43.145224192+00:00"
        assert report["periods"] == []
        assert "catalog" not in _ok(request("/v2/info"))
        spec = _ok(request("/v2/openapi.json"))
        assert len(spec["paths"]) == 6
        assert spec["paths"]["/v2/stores/{store_id}/sessions/{session_id}/frames/{artifact_id}"][
            "get"
        ]["parameters"] == [
            {"name": "store_id", "in": "path", "required": True, "schema": {"type": "string"}},
            {"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}},
            {"name": "artifact_id", "in": "path", "required": True, "schema": {"type": "string"}},
        ]
        _schema(spec, "FrameReport", report)
        _error(request("/v2/openapi.json", bearer=None), 401, "unauthorized")
        for suffix, status, code in (
            ("?limit=1", 400, "invalid_parameter"),
            ("?include=rows", 400, "invalid_parameter"),
            ("/data", 404, "not_found"),
            ("/periods", 404, "not_found"),
        ):
            _error(request(path + suffix), status, code)

        sessions = _ok(request("/v2/sessions"))
        assert sessions["items"][0]["session_id"] == ids["session_id"]
        assert sessions["items"][0]["artifact_count"] == len(ids) - 2
        _schema(spec, "SessionDirectory", sessions)
        frames = f"/v2/sessions/{ids['session_id']}/frames"
        page = _ok(request(frames + "?limit=1"))
        _schema(spec, "FrameDirectory", page)
        assert page["items"][0]["ref"]["artifact_id"] == ids["paired_id"]
        assert (
            _ok(request(frames + "?" + urlencode({"limit": 1, "cursor": page["next_cursor"]})))[
                "items"
            ][0]["ref"]["artifact_id"]
            == ids["compare_id"]
        )
        _error(
            request(frames + "?" + urlencode({"limit": 2, "cursor": page["next_cursor"]})),
            409,
            "invalid_cursor",
        )

        oversized = request(_path(ids, "large_id"))
        error = _error(oversized, 413, "response_too_large")
        assert "1 MiB" in error["message"] and "--max-response-mib" in error["repair"]
        _schema(spec, "Error413", oversized[2])
        for key, count in (("empty_id", 0), ("unit_id", 1), ("empty_time_id", 0)):
            value = _ok(request(_path(ids, key)))
            _schema(spec, "FrameReport", value)
            assert value["data"]["row_count"] == count
            assert value["data"]["rows"] == ([[]] if count else [])
            if key == "empty_time_id":
                assert [item["ordinal"] for item in value["periods"][0]["items"]] == [0, 1]
        comparison = _ok(request(_path(ids, "compare_id")))
        _schema(spec, "FrameReport", comparison)
        matched, absent = (
            comparison["periods"][1]["items"][0],
            comparison["periods"][1]["items"][-1],
        )
        assert matched["current"]["key"] == matched["baseline"]["key"]
        assert absent["current"]["key"] == [
            {
                "dtype": {"kind": "timestamp", "unit": "us", "timezone": "UTC"},
                "value": "2000-01-04T00:00:00+00:00",
            }
        ]
        assert absent["baseline"] is None and absent["absent_reason"] == "unmatched"
        paired = _ok(request(_path(ids, "paired_id")))
        _schema(spec, "FrameReport", paired)
        assert paired["periods"][0]["items"][-1]["right"] is None
        collision = _ok(request(_path(ids, "collision_id")))
        _schema(spec, "FrameReport", collision)
        assert [item["name"] for item in collision["schema"]["layout"]] == [
            "region",
            "region",
            "region",
            "region__2",
        ]
        assert [item["name"] for item in collision["data"]["layout"]] == [
            "region__3",
            "region__4",
            "region",
            "region__2",
        ]
        assert collision["data"]["rows"] == [[["CN", "7"], "SH", "4", "5"]]
        assert collision["data"]["coordinates"] == {
            "country": ["CN"],
            "region_id": ["7"],
            "shipping": ["SH"],
        }

    with server(tmp_path) as request:
        assert _ok(request(path)) == report
        assert _ok(request(_path(ids, "collision_id"))) == collision
        status, headers, large = request(_path(ids, "large_id"))
        assert status == 200 and large is not None
        assert 1024 * 1024 < int(headers["content-length"]) < 128 * 1024 * 1024
        items = large["periods"][0]["items"]
        assert len(items) == len(large["data"]["rows"]) == large["data"]["row_count"] == 10_001
        assert items[-1]["ordinal"] == 10_000
        day = (date(2000, 1, 1) + timedelta(days=10_000)).isoformat()
        assert items[-1]["key"][0]["value"] == day + "T00:00:00+00:00"
        assert large["data"]["rows"][-1] == [
            day + "T00:00:00+00:00",
            {"dtype": {"kind": "string"}, "value": day},
            "10000",
        ]

    with server(tmp_path, token="replacement-token") as request:
        _error(request(path, bearer=_TOKEN), 401, "unauthorized")
        assert _ok(request(path)) == report


def test_missing_store_wrong_identity_and_corruption(tmp_path: Path) -> None:
    project = tmp_path / "producer"
    project.mkdir()
    ids = _produce(project)
    empty = tmp_path / "empty"
    empty.mkdir()
    with server(empty) as request:
        assert _ok(request("/v2/info"))["store_id"] is None
        assert _ok(request("/v2/sessions")) == {
            "items": [],
            "limit": 20,
            "has_more": False,
            "next_cursor": None,
        }
        _error(request(_path(ids)), 404, "not_found")
        assert not (empty / ".marivo").exists()
    store_path = MaterializationLayout(empty).store_db
    store_path.parent.mkdir(parents=True)
    with sqlite3.connect(store_path) as connection:
        connection.execute("PRAGMA user_version=27")
    connection.close()
    with server(empty) as request:
        _error(request(_path(ids)), 422, "unsupported_store_format")
    with server(project) as request:
        _error(request(_path({**ids, "artifact_id": "artifact_missing"})), 404, "not_found")
        store = SessionStore.open_existing(project)
        with store._read() as conn:
            artifact = manifest(store, conn, ids["artifact_id"])
        assert artifact is not None
        receipt_path = artifact.data.rows.receipt.project_relative_path
        file = next((project / receipt_path).rglob("*.parquet"))
        file.unlink()
        _error(request(_path(ids)), 422, "corrupt_artifact")
        assert not file.exists()
        assert _ok(request(_path(ids, "empty_id")))["data"]["rows"] == []


def test_tls_cors_and_cli_validation(tmp_path: Path) -> None:
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
    )
    context = ssl.create_default_context(cafile=str(cert))
    with server(
        tmp_path,
        "--ssl-certfile",
        str(cert),
        "--ssl-keyfile",
        str(key),
        "--allow-origin",
        _ORIGIN,
        context=context,
    ) as request:
        assert _ok(request("/v2/info"))["api_version"] == 2
        headers = {
            "Origin": _ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization, Content-Type",
        }
        status, cors, body = request(
            "/v2/semantic/reload", method="OPTIONS", bearer=None, headers=headers
        )
        assert status == 204 and body is None
        assert cors["access-control-allow-origin"] == _ORIGIN
        assert "access-control-allow-credentials" not in cors
        assert cors["access-control-allow-methods"] == "GET, OPTIONS"
        assert cors["access-control-allow-headers"] == "Authorization, Content-Type"
        for override in (
            {"Origin": "https://other.example.test"},
            {"Access-Control-Request-Method": "DELETE"},
            {"Access-Control-Request-Headers": "X-Unknown"},
        ):
            _error(
                request("/v2/info", method="OPTIONS", bearer=None, headers={**headers, **override}),
                403,
                "origin_not_allowed",
            )
        failure = request(
            "/v2/semantic/reload", method="POST", bearer="wrong", headers={"Origin": _ORIGIN}
        )
        _error(failure, 401, "unauthorized")
        assert failure[1]["access-control-allow-origin"] == _ORIGIN
        assert failure[1]["vary"] == "Origin"
        assert "catalog" not in _ok(request("/v2/info"))

    no_token = {k: v for k, v in os.environ.items() if k != "MARIVO_API_TOKEN"}
    for opts, env, expected in (
        ([], no_token, "MARIVO_API_TOKEN"),
        (["--ssl-certfile", str(cert)], {**os.environ, "MARIVO_API_TOKEN": _TOKEN}, "together"),
        (
            ["--ssl-certfile", str(key), "--ssl-keyfile", str(key)],
            {**os.environ, "MARIVO_API_TOKEN": _TOKEN},
            "PEM",
        ),
        (["--max-response-mib", "0"], no_token, "positive integers"),
        (["--read-timeout-seconds", "-1"], no_token, "positive integers"),
        (
            ["--allow-origin", "https://example.test/path"],
            {**os.environ, "MARIVO_API_TOKEN": _TOKEN},
            "exact HTTP(S) Origin",
        ),
    ):
        failed = subprocess.run(
            [*_CLI, "serve", "--project-root", str(tmp_path), "--port", "0", *opts],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert failed.returncode != 0 and failed.stdout == ""
        assert expected in failed.stderr and _TOKEN not in failed.stderr


def test_read_timeout_covers_complete_report(tmp_path: Path) -> None:
    ids = _produce(tmp_path)
    # Hold a real Frame read past the deadline at its file-read boundary.
    script = """
from time import sleep
from marivo.analysis.materialization.graph_reading import FrameReader
from marivo.cli import main

original = FrameReader.range
def delayed(self, *args, **kwargs):
    sleep(3)
    return original(self, *args, **kwargs)
FrameReader.range = delayed
main()
"""
    with server(
        tmp_path, "--read-timeout-seconds", "1", command=[sys.executable, "-c", script]
    ) as request:
        error = _error(request(_path(ids)), 503, "read_timeout")
        assert "1 second" in error["message"] and "--read-timeout-seconds" in error["repair"]
        assert "catalog" not in _ok(request("/v2/info"))
        assert len(_ok(request("/v2/sessions"))["items"]) == 1


def test_in_progress_publication_does_not_block_or_enter_committed_directory(
    tmp_path: Path,
) -> None:
    ids = _produce(tmp_path)
    script = """
import sys
from pathlib import Path
from marivo.analysis.materialization import terminal_publication
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.layout import MaterializationLayout
from marivo.analysis.materialization.store import SessionStore

root, session_id, artifact_id = sys.argv[1:]
local = LocalFrameServices(SessionStore.open_existing(Path(root)), session_id)
source = local.reopen(artifact_id)
def hold(event):
    if event == "terminal_files_published":
        print("staged", flush=True)
        assert sys.stdin.readline() == "commit\\n"
terminal_publication._event = hold
print(source.compute().persist(description="next commit").artifact_id, flush=True)
"""
    with (
        server(tmp_path) as request,
        subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path), ids["session_id"], ids["artifact_id"]],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as producer,
    ):
        assert producer.stdout is not None
        try:
            assert select.select([producer.stdout], [], [], 20)[0]
            assert producer.stdout.readline().strip() == "staged"
            directory = f"/v2/sessions/{ids['session_id']}/frames"
            previous = _ok(request(directory))
            assert {item["ref"]["artifact_id"] for item in previous["items"]} == {
                ids["artifact_id"],
                ids["empty_id"],
            }
            report = _ok(request(_path(ids)))
            assert report["data"]["row_count"] == 3
            stdout, stderr = producer.communicate("commit\n", timeout=15)
            assert producer.returncode == 0, stderr
            new_id = stdout.strip()
            assert _ok(request(directory))["items"][0]["ref"]["artifact_id"] == new_id
            new = _ok(request(_path({**ids, "artifact_id": new_id})))
            assert new["data"] == report["data"] and new["description"] == "next commit"
        finally:
            if producer.poll() is None:
                producer.kill()


def test_read_report_with_missing_method_payload(tmp_path: Path) -> None:
    local = DatasetRuntime.create(tmp_path, "method", timezone="UTC").local
    saved = AnalysisFrame._from_memory(local, method_result(current=240, baseline=200)).persist()
    with local.store._read() as conn:
        artifact = manifest(local.store, conn, saved.artifact_id)
    assert artifact is not None
    directory = tmp_path / artifact.method_facts[0].payload.rows.receipt.project_relative_path
    file = next(directory.rglob("*.parquet"))
    file.unlink()
    with server(tmp_path) as request:
        report = _ok(
            request(
                _path(
                    {
                        "store_id": local.store.store_id,
                        "session_id": saved.session_id,
                        "artifact_id": saved.artifact_id,
                    }
                )
            )
        )
        fields = [item["name"] for item in report["data"]["layout"]]
        assert report["data"]["rows"][0][fields.index("contribution.q")] == "40"
        assert "method_facts" not in report
        assert not file.exists()


def test_plain_table_publication_and_retired_routes(tmp_path: Path) -> None:
    import pandas as pd

    import marivo.analysis as mv

    (tmp_path / "endpoints").mkdir()
    (tmp_path / "endpoints" / "bad.py").write_text("raise AssertionError('must not execute')")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "bad.py").write_text("raise AssertionError('must not load')")
    ref = mv.persist_dataframe(pd.DataFrame({"orders": [9007199254740993]}), project_root=tmp_path)
    path = "/v2/dataframes/" + ref.result_id
    with server(tmp_path) as request:
        spec = _ok(request("/v2/openapi.json"))
        result = _ok(request(path))
        assert result["ref"] == ref.to_dict()
        assert result["data"]["rows"] == [["9007199254740993"]]
        assert "session_id" not in result
        assert _ok(request("/v2/info"))["store_id"] is None
        _schema(spec, "DataFrameReport", result)
        for retired in (
            "/v1/info",
            "/v2/data/sales/total",
            "/v2/data/sales/total/refresh",
            "/v2/semantic/objects",
            "/v2/semantic/reload",
        ):
            _error(request(retired), 404, "not_found")
            _error(request(retired, method="POST"), 404, "not_found")
        _error(request(path, method="POST"), 405, "method_not_allowed")
    assert not (tmp_path / ".marivo/analysis").exists()


def test_frame_wrong_store_is_rejected(tmp_path: Path) -> None:
    ids = _produce(tmp_path)
    with server(tmp_path) as request:
        _error(request(_path({**ids, "store_id": "store_other"})), 409, "invalid_result_reference")


def test_old_reference_against_replacement_store_reports_identity_conflict(tmp_path: Path) -> None:
    original = tmp_path / "original"
    replacement = tmp_path / "replacement"
    ids = _produce(original)
    other = _produce(replacement)
    assert ids["store_id"] != other["store_id"]
    assert ids["session_id"] != other["session_id"]
    with server(replacement) as request:
        _error(request(_path(ids)), 409, "invalid_result_reference")
