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
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Protocol, TypeAlias
from urllib.parse import urlencode

import pytest
from jsonschema import Draft202012Validator

from marivo._help.render import render_help_text
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
            "api_version": 1,
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
    return f"/v1/sessions/{ids['session_id']}/frames/{ids[key]}"


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
            "schema": "marivo.frame_ref/v1",
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
        assert _ok(request("/v1/info"))["catalog"] == {"loaded": False, "loaded_at": None}
        spec = _ok(request("/v1/openapi.json"))
        assert len(spec["paths"]) == 12
        assert spec["paths"]["/v1/sessions/{session_id}/frames/{artifact_id}"]["get"][
            "parameters"
        ] == [
            {"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}},
            {"name": "artifact_id", "in": "path", "required": True, "schema": {"type": "string"}},
        ]
        _schema(spec, "FrameReport", report)
        _error(request("/v1/openapi.json", bearer=None), 401, "unauthorized")
        for suffix, status, code in (
            ("?limit=1", 400, "invalid_parameter"),
            ("?include=rows", 400, "invalid_parameter"),
            ("/data", 404, "not_found"),
            ("/periods", 404, "not_found"),
        ):
            _error(request(path + suffix), status, code)

        sessions = _ok(request("/v1/sessions"))
        assert sessions["items"][0]["session_id"] == ids["session_id"]
        assert sessions["items"][0]["artifact_count"] == len(ids) - 1
        _schema(spec, "SessionDirectory", sessions)
        frames = path.rsplit("/", 1)[0]
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


def _models(root: Path, text: str, *, bad: bool = False, slow: bool = False) -> None:
    sales = root / "models" / "semantic" / "sales"
    datasources = root / "models" / "datasources"
    sales.mkdir(parents=True, exist_ok=True)
    datasources.mkdir(parents=True, exist_ok=True)
    (root / "marivo.toml").write_text('[project]\nname = "service-test"\n')
    (datasources / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.duckdb(name='warehouse', path='private-storage-name.duckdb')\n"
    )
    (sales / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Finance')\n"
    )
    events = sales / "events.py"
    events.write_text(
        "def broken(:\n"
        if bad
        else (
            ("import time\ntime.sleep(6)\n" if slow else "")
            + "import os\n"
            + "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            + "os.write(1, b'ordinary model output')\n"
            + "warehouse = ms.ref.datasource('warehouse')\n"
            + "events = ms.entity(name='events', datasource=warehouse, "
            + "source=md.json('https://data.example.test/events?secret=private-source-token', "
            + "columns={'amount': 'amount', 'created': 'created'}), "
            + "default_time=ms.ref.time_dimension('sales.events.created'))\n"
            + "created = ms.time_dimension_column(name='created', entity=events, column='created')\n"
            + "amount = ms.measure_column(name='amount', entity=events, column='amount', "
            + f"additivity=ms.additive_all(), unit='USD', ai_context=ms.ai_context(business_definition={text!r}))\n"
            + "@ms.metric()\ndef total():\n    return amount.sum()\n"
        )
    )


def test_current_catalog_reload_isolated_from_saved_report(tmp_path: Path) -> None:
    ids = _produce(tmp_path)
    _models(tmp_path, "before", bad=True)
    with server(tmp_path) as request:
        report = _ok(request(_path(ids)))
        _error(request("/v1/semantic/objects"), 422, "catalog_load_failed")
        assert _ok(request("/v1/info"))["catalog"]["loaded"] is False
        assert _ok(request(_path(ids))) == report
        _models(tmp_path, "before")
        spec = _ok(request("/v1/openapi.json"))
        first = _ok(request("/v1/semantic/objects?limit=1"))
        _schema(spec, "ObjectDirectory", first)
        assert "sales" in first["domains"] and "measure" in first["kinds"]
        continuation = "/v1/semantic/objects?" + urlencode(
            {"limit": 1, "cursor": first["next_cursor"]}
        )
        assert _ok(request(continuation))["items"][0]["id"] != first["items"][0]["id"]
        endpoint = "/v1/semantic/objects/measure%3Asales.events.amount"
        detail = _ok(request(endpoint))
        _schema(spec, "SemanticObject", detail)
        assert detail["business_definition"] == "before"
        assert detail["definition"]["unit"] == "USD"
        assert set(detail) == {
            "id",
            "kind",
            "path",
            "name",
            "domain",
            "business_definition",
            "definition",
            "provenance",
        }
        related = _ok(request(endpoint + "/related?relation=dependencies"))
        _schema(spec, "RelatedDirectory", related)
        assert "entity:sales.events" in {item["id"] for item in related["items"]}
        entity = _ok(request("/v1/semantic/objects/entity%3Asales.events"))
        _schema(spec, "SemanticObject", entity)
        assert entity["definition"]["source"] == {"kind": "json"}
        assert entity["definition"]["default_time"] == "time_dimension:sales.events.created"
        assert "private-source-token" not in json.dumps(entity)
        metric = _ok(request("/v1/semantic/objects/metric%3Asales.total"))
        _schema(spec, "SemanticObject", metric)
        (binding,) = metric["definition"]["time_bindings"]
        assert binding["source"] == "entity_default"
        assert binding["field"] == "time_dimension:sales.events.created"
        assert binding["anchor"] is None
        assert "time_parameters" not in metric["definition"]
        datasource = _ok(request("/v1/semantic/objects/datasource%3Awarehouse"))
        _schema(spec, "SemanticObject", datasource)
        assert datasource["definition"]["backend_type"] == "duckdb"
        assert "private-storage-name" not in json.dumps(datasource)
        _error(request(endpoint + "?as_of=yesterday"), 400, "invalid_parameter")

        _models(tmp_path, "after")
        reload = _ok(request("/v1/semantic/reload", method="POST"))
        assert reload["object_count"] >= 4 and reload["loaded_at"] != ""
        assert _ok(request(endpoint))["business_definition"] == "after"
        _error(request(continuation), 409, "invalid_cursor")
        _models(tmp_path, "bad", bad=True)
        _error(request("/v1/semantic/reload", method="POST"), 422, "catalog_load_failed")
        assert _ok(request("/v1/info"))["catalog"]["loaded_at"] == reload["loaded_at"]
        assert _ok(request(endpoint))["business_definition"] == "after"
        assert _ok(request(_path(ids))) == report


@pytest.mark.parametrize("initial_method", ["GET", "POST"])
def test_reload_rejects_active_load_then_reads_new_model(
    tmp_path: Path, initial_method: str
) -> None:
    _models(tmp_path, "before")
    events = tmp_path / "models/semantic/sales/events.py"
    ready, release = tmp_path / "ready", tmp_path / "release"
    with events.open("a") as model:
        model.write(
            "\nfrom pathlib import Path\nimport time\n"
            f"Path({str(ready)!r}).touch()\n"
            f"while not Path({str(release)!r}).exists():\n    time.sleep(0.01)\n"
        )
    with server(tmp_path) as request, ThreadPoolExecutor(max_workers=1) as pool:
        initial_path = "/v1/semantic/objects" if initial_method == "GET" else "/v1/semantic/reload"
        pending = pool.submit(request, initial_path, method=initial_method)
        try:
            deadline = monotonic() + 20
            while not ready.exists():
                assert monotonic() < deadline, "Catalog worker did not reach the model barrier"
                sleep(0.01)
            _models(tmp_path, "after")
            reply = request("/v1/semantic/reload", method="POST")
            _error(reply, 409, "catalog_load_in_progress")
            _schema(_ok(request("/v1/openapi.json")), "Error409", reply[2])
        finally:
            release.touch()
        _ok(pending.result(timeout=30))
        endpoint = "/v1/semantic/objects/measure%3Asales.events.amount"
        assert _ok(request(endpoint))["business_definition"] == "before"
        _ok(request("/v1/semantic/reload", method="POST"))
        assert _ok(request(endpoint))["business_definition"] == "after"


def test_missing_store_wrong_identity_and_corruption(tmp_path: Path) -> None:
    project = tmp_path / "producer"
    project.mkdir()
    ids = _produce(project)
    empty = tmp_path / "empty"
    empty.mkdir()
    with server(empty) as request:
        assert _ok(request("/v1/info"))["store_id"] is None
        assert _ok(request("/v1/sessions")) == {
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
        assert _ok(request("/v1/info"))["api_version"] == 1
        headers = {
            "Origin": _ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, Content-Type",
        }
        status, cors, body = request(
            "/v1/semantic/reload", method="OPTIONS", bearer=None, headers=headers
        )
        assert status == 204 and body is None
        assert cors["access-control-allow-origin"] == _ORIGIN
        assert "access-control-allow-credentials" not in cors
        assert cors["access-control-allow-methods"] == "GET, POST, OPTIONS"
        assert cors["access-control-allow-headers"] == "Authorization, Content-Type"
        for override in (
            {"Origin": "https://other.example.test"},
            {"Access-Control-Request-Method": "DELETE"},
            {"Access-Control-Request-Headers": "X-Unknown"},
        ):
            _error(
                request("/v1/info", method="OPTIONS", bearer=None, headers={**headers, **override}),
                403,
                "origin_not_allowed",
            )
        failure = request(
            "/v1/semantic/reload", method="POST", bearer="wrong", headers={"Origin": _ORIGIN}
        )
        _error(failure, 401, "unauthorized")
        assert failure[1]["access-control-allow-origin"] == _ORIGIN
        assert failure[1]["vary"] == "Origin"
        assert _ok(request("/v1/info"))["catalog"]["loaded"] is False

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
        (["--refresh-timeout-seconds", "0"], no_token, "positive integers"),
        (["--max-refreshes", "-1"], no_token, "positive integers"),
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


def test_read_timeout_covers_complete_report_and_catalog(tmp_path: Path) -> None:
    ids = _produce(tmp_path)
    _models(tmp_path, "slow", slow=True)
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
        _error(request("/v1/semantic/objects"), 503, "read_timeout")
        assert _ok(request("/v1/info"))["catalog"]["loaded"] is False
        assert len(_ok(request("/v1/sessions"))["items"]) == 1


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
            directory = _path(ids).rsplit("/", 1)[0]
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
            request(_path({"session_id": saved.session_id, "artifact_id": saved.artifact_id}))
        )
        fields = [item["name"] for item in report["data"]["layout"]]
        assert report["data"]["rows"][0][fields.index("contribution.q")] == "40"
        assert "method_facts" not in report
        assert not file.exists()


def _refresh_project(root: Path) -> Path:
    _models(root, "Endpoint input")
    facts = root / "facts.json"
    facts.write_text('[{"amount": 10}]')
    (root / "models/datasources/warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', extra={'threads': 1})\n"
    )
    (root / "models/semantic/sales/events.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "events = ms.entity(name='events', datasource=ms.ref.datasource('warehouse'), "
        f"source=md.json({str(facts)!r}, columns={{'amount': 'amount'}}))\n"
        "amount = ms.measure_column(name='amount', entity=events, column='amount', "
        "additivity=ms.additive_all(), unit='USD')\n"
        "@ms.metric()\ndef total():\n    return amount.sum()\n"
    )
    endpoints = root / "endpoints/sales"
    endpoints.mkdir(parents=True)
    for name, returned, output in (
        ("total", "mv.AnalysisFrame", "return frame"),
        (
            "table",
            "pd.DataFrame",
            "table = frame.compute().to_pandas()\n    return table.assign(double=table['total'] * 2)",
        ),
    ):
        (endpoints / f"{name}.py").write_text(
            "from pathlib import Path\nimport pandas as pd\nimport marivo.analysis as mv\n"
            "with Path('invocations').open('a') as log:\n    log.write('import\\n')\n"
            f"def main(session: mv.Session) -> {returned}:\n"
            "    metric = session.catalog.get('metric:sales.total')\n"
            "    assert metric.kind == 'metric'\n"
            "    frame = session.observe({'total': metric})\n"
            f"    {output}\n"
        )
    return facts


def test_script_example_from_live_help_runs_through_service(tmp_path: Path) -> None:
    _refresh_project(tmp_path)
    help_text = render_help_text("analysis.results.service.scripts")[0]
    example = help_text.split("\nExample:\n", 1)[1].split("\nExpected:", 1)[0]
    (tmp_path / "endpoints/sales/from_help.py").write_text(example + "\n")

    with server(tmp_path) as request:
        url = "/v1/data/sales/from_help"
        _error(request(url), 404, "result_not_found")
        report = _ok(request(url + "/refresh", method="POST"))
        assert report["kind"] == "analysis_frame"
        assert report["data"]["rows"] == [["10"]]
        assert _ok(request(url)) == report
        assert _ok(request(_path(report["ref"]))) == report


def test_refresh_source_updates_and_report_saved_refs(tmp_path: Path) -> None:
    facts = _refresh_project(tmp_path)
    frame_url, table_url = "/v1/data/sales/total", "/v1/data/sales/table"
    with server(tmp_path) as request:
        _error(request(frame_url), 404, "result_not_found")
        _error(request(table_url), 404, "result_not_found")
        _error(request(frame_url + "/refresh", method="POST", bearer=None), 401, "unauthorized")
        for query in (
            "refresh=true",
            "refresh=false",
            "refresh=",
            "refresh=1",
            "refresh=TRUE",
            "refresh=true&refresh=true",
            "x=1",
        ):
            _error(request(frame_url + "?" + query), 400, "invalid_parameter")
        _error(request(frame_url + "/refresh"), 404, "result_not_found")
        _error(request(frame_url, method="POST"), 404, "not_found")
        _error(
            request(frame_url + "/refresh?refresh=true", method="POST"), 400, "invalid_parameter"
        )
        _error(request(frame_url + "/refresh", method="POST", body=b"{}"), 400, "invalid_parameter")
        assert not (tmp_path / "invocations").exists()
        assert not (tmp_path / ".marivo").exists()
        spec = _ok(request("/v1/openapi.json"))
        data_operation = spec["paths"]["/v1/data/{endpoint}"]["get"]
        assert [parameter["in"] for parameter in data_operation["parameters"]] == ["path"]
        refresh_operation = spec["paths"]["/v1/data/{endpoint}/refresh"]["post"]
        assert "Do not automatically retry" in refresh_operation["description"]
        assert "read current state" in refresh_operation["description"]
        first_frame = _ok(request(frame_url + "/refresh", method="POST"))
        first_table = _ok(request(table_url + "/refresh", method="POST"))
        assert first_frame["kind"] == "analysis_frame"
        assert first_frame["data"]["rows"] == [["10"]]
        assert first_table["data"] == {
            "layout": ["total", "double"],
            "rows": [["10", "20"]],
            "row_count": 1,
        }
        for report in (first_frame, first_table):
            _schema(spec, "DataReport", report)
        assert first_table["session_id"] != first_frame["ref"]["session_id"]
        saved = tmp_path / "report.json"
        saved.write_text(json.dumps({"frame": first_frame["ref"], "table": first_table["ref"]}))
        facts.write_text('[{"amount": 20}]')
        assert _ok(request(frame_url)) == first_frame
        assert _ok(request(table_url)) == first_table
        assert (tmp_path / "invocations").read_text().splitlines() == ["import", "import"]
        newer_frame = _ok(request(frame_url + "/refresh", method="POST"))
        newer_table = _ok(request(table_url + "/refresh", method="POST"))
        assert newer_frame["data"]["rows"] == [["20"]]
        assert newer_table["data"]["rows"] == [["20", "40"]]
        assert newer_frame["ref"] != first_frame["ref"]
        assert newer_table["ref"] != first_table["ref"]
        restored = json.loads(saved.read_text())
        fixed_frame = _path(restored["frame"])
        fixed_table = "/v1/dataframes/" + restored["table"]["result_id"]
        assert _ok(request(fixed_frame)) == first_frame
        assert _ok(request(fixed_table)) == first_table
        _error(request(fixed_table + "?refresh=true"), 400, "invalid_parameter")
        assert len(list((tmp_path / ".marivo/server/dataframes").glob("*.parquet"))) == 2
        (tmp_path / "endpoints").rename(tmp_path / "removed_endpoints")
        (tmp_path / "models").rename(tmp_path / "removed_models")
        facts.unlink()
        assert _ok(request(frame_url)) == newer_frame
        assert _ok(request(table_url)) == newer_table
    with server(tmp_path) as request:
        assert _ok(request(fixed_frame)) == first_frame
        assert _ok(request(frame_url)) == newer_frame
        assert _ok(request(fixed_table)) == first_table
        _error(request(frame_url + "/refresh", method="POST"), 404, "not_found")
    (tmp_path / ".marivo/analysis").rename(tmp_path / "removed_store")
    with server(tmp_path) as request:
        assert _ok(request(fixed_table)) == first_table
        assert _ok(request(table_url)) == newer_table


def test_refresh_timeout_cross_process_lock_and_read_availability(tmp_path: Path) -> None:
    _refresh_project(tmp_path)
    url = "/v1/data/sales/table"
    path = tmp_path / "endpoints/sales/table.py"
    original = path.read_text()
    with server(tmp_path, "--refresh-timeout-seconds", "10") as first:
        saved = _ok(first(url + "/refresh", method="POST"))
        path.write_text(
            original.replace(
                "    metric =",
                "    Path('ready').touch()\n    import time\n    time.sleep(120)\n    metric =",
            )
        )
        with server(tmp_path) as second, ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(first, url + "/refresh", method="POST")
            deadline = monotonic() + 8
            while not (tmp_path / "ready").exists():
                assert monotonic() < deadline, "Worker did not enter the script"
                sleep(0.01)
            assert _ok(second(url)) == saved
            _error(second(url + "/refresh", method="POST"), 409, "refresh_in_progress")
            _error(first("/v1/data/sales/total/refresh", method="POST"), 503, "service_unavailable")
            _error(pending.result(timeout=15), 503, "refresh_timeout")
            assert _ok(first(url)) == saved
            path.write_text(original)
            assert _ok(second(url + "/refresh", method="POST"))["ref"] != saved["ref"]
