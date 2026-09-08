"""Actual producer death, native adapter state and fresh-process cold recovery."""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from marivo.analysis.materialization.object_storage import client, decode_locator
from marivo.analysis.materialization.targets import S3Access
from tests.test_lazy_adapter_runtime_acceptance import _manifest


def _start(
    mode: str,
    kind: str,
    project: Path,
    access: S3Access | None,
    point: str,
    occurrence: int = 1,
    *,
    sampled: bool = False,
) -> subprocess.Popen[str]:
    environment = {**os.environ, "MARIVO_TELEMETRY": "off"}
    if access is not None:
        environment["MARIVO_TEST_S3_ENDPOINT"] = access.endpoint_url
        environment["MARIVO_TEST_S3_BUCKET"] = access.bucket
    return subprocess.Popen(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.lazy_adapter_crash_worker",
            mode,
            kind,
            str(project),
            "--point",
            point,
            "--occurrence",
            str(occurrence),
            *(["--sampled"] if sampled else []),
        ],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _finish(process: subprocess.Popen[str], expected: int) -> str:
    try:
        stdout, stderr = process.communicate(timeout=60)
    except BaseException:
        process.kill()
        process.communicate(timeout=10)
        raise
    assert process.returncode == expected, stdout + stderr
    return stdout


def _recover(project: Path, kind: str, access: S3Access | None) -> dict[str, object]:
    value: object = json.loads(_finish(_start("recover", kind, project, access, ""), 0))
    assert isinstance(value, dict)
    return value


def _evidence(
    project: Path,
    name: str,
    value: dict[str, object],
    candidate_before: dict[str, object],
) -> None:
    candidate_after = _manifest()
    assert candidate_before == candidate_after
    value.update(
        schema="marivo.slice4c.adapter_crash/v1",
        candidate_before=candidate_before,
        candidate_after=candidate_after,
    )
    destination = project / (name + ".json")
    destination.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    retained = os.environ.get("MARIVO_SLICE4C_EVIDENCE_DIR")
    if retained:
        output = Path(retained)
        output.mkdir(parents=True, exist_ok=True)
        (output / destination.name).write_bytes(destination.read_bytes())


@pytest.mark.parametrize(
    ("kind", "point", "occurrence", "sampled"),
    [
        ("engine", "engine_producer_reserved", 1, False),
        ("engine", "engine_payload_create", 1, False),
        ("engine", "engine_payload_create", 2, False),
        ("engine", "before_rename", 1, False),
        ("engine", "after_rename", 1, False),
        ("engine", "sampling_validated", 1, True),
        ("object", "object_reserved", 1, False),
        ("object", "object_after_put", 1, False),
        ("object", "object_after_put", 2, False),
        ("object", "object_after_put", 3, False),
        ("object", "object_after_put", 4, False),
        ("object", "quality", 1, True),
        *(
            (kind, point, 1, False)
            for kind in ("engine", "object")
            for point in (
                "insert_artifact",
                "insert_evidence",
                "insert_terminal",
                "before_commit",
                "after_commit",
                "readback_unavailable",
            )
        ),
    ],
)
def test_adapter_crash_reconciles_exact_uncommitted_outputs_or_preserves_commit(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    kind: str,
    point: str,
    occurrence: int,
    sampled: bool,
) -> None:
    access: S3Access | None = None
    if kind == "object":
        selected: object = request.getfixturevalue("lazy_s3_access")
        assert isinstance(selected, S3Access)
        access = selected
    candidate_before = _manifest()
    _finish(_start("produce", kind, tmp_path, access, point, occurrence, sampled=sampled), 73)
    producer: object = json.loads((tmp_path / "crash.json").read_text())
    assert isinstance(producer, dict)
    recovery = _recover(tmp_path, kind, access)
    assert recovery["pending"] is False
    recovered = recovery["recovered"]
    assert isinstance(recovered, dict)
    assert recovered["pid"] != producer["pid"]
    assert recovered["resources"] == []
    counts = recovered["counts"]
    assert isinstance(counts, dict)
    committed = point in ("after_commit", "readback_unavailable")
    assert producer["artifacts"] == recovered["artifacts"]
    assert counts == {
        "analysis_action_runs": 2,
        "analysis_action_run_terminals": 2,
        "dataset_artifacts": 2 if committed else 1,
        "dataset_evidence": 2 if committed else 1,
        "analysis_action_run_inputs": 0,
        "action_resource_journal": 0,
    }
    runs = recovered["runs"]
    assert isinstance(runs, list)
    assert sorted(item["lifecycle"] for item in runs) == (
        ["succeeded", "succeeded"] if committed else ["failed", "succeeded"]
    )
    stats = recovered["statistics"]
    assert isinstance(stats, dict)
    assert stats["events"] == {"reconciliation": 1}
    assert stats["primary_queries"] == 0 and stats["worker_pid"] is None
    if access is not None:
        with client(access) as s3:
            versions = s3.list_object_versions(Bucket=access.bucket).get("Versions", [])
        # Every remaining remote version belongs to one committed Artifact.
        artifacts = {
            item["output_artifact_ref"].removeprefix("artifact_")
            for item in runs
            if item["output_artifact_ref"]
        }
        assert versions
        assert all(any(artifact in item["Key"] for artifact in artifacts) for item in versions)
    _evidence(
        tmp_path,
        f"slice-4c-crash-{kind}-{point}-{occurrence}-{int(sampled)}",
        {"producer": producer, "recovery": recovery},
        candidate_before,
    )


def test_acknowledged_object_response_before_durable_discharge_remains_cold_unknown(
    tmp_path: Path, lazy_s3_access: S3Access
) -> None:
    candidate_before = _manifest()
    _finish(
        _start("produce", "object", tmp_path, lazy_s3_access, "object_response_before_discharge"),
        73,
    )
    first = _recover(tmp_path, "object", lazy_s3_access)
    recovered = first["recovered"]
    assert isinstance(recovered, dict)
    resources = recovered["resources"]
    assert isinstance(resources, list)
    request = next(item for item in resources if item["cleanup_capability_id"] == "s3_request@v1")
    _, key = decode_locator(request["safe_locator"])
    with client(lazy_s3_access) as s3:
        versions = s3.list_object_versions(Bucket=lazy_s3_access.bucket, Prefix=key).get(
            "Versions", []
        )
        assert len(versions) == 1 and versions[0]["Key"] == key
        s3.delete_object(Bucket=lazy_s3_access.bucket, Key=key, VersionId=versions[0]["VersionId"])
    # Even an externally removed request object supplies no termination proof.
    recoveries = [first, _recover(tmp_path, "object", lazy_s3_access)]
    for recovery in recoveries:
        assert recovery["pending"] is True
        before, after = recovery["before"], recovery["recovered"]
        assert isinstance(before, dict) and isinstance(after, dict)
        assert before["counts"] == after["counts"]
        assert before["resources"] == after["resources"]
        resources = after["resources"]
        assert isinstance(resources, list)
        assert any(item["cleanup_capability_id"] == "s3_request@v1" for item in resources)
    _evidence(
        tmp_path,
        "slice-4c-object-response-undischarged",
        {"recoveries": recoveries},
        candidate_before,
    )


@contextmanager
def _withheld_proxy(
    access: S3Access, project: Path
) -> Iterator[tuple[S3Access, threading.Event, list[dict[str, object]]]]:
    endpoint = urlsplit(access.endpoint_url)
    assert endpoint.scheme == "http" and endpoint.hostname is not None
    host, port = endpoint.hostname, endpoint.port or 80
    forwarded = threading.Event()
    release = threading.Event()
    requests: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            pass

        def forward(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            connection = http.client.HTTPConnection(host, port, timeout=20)
            try:
                connection.request(self.command, self.path, body, dict(self.headers.items()))
                response = connection.getresponse()
                data = response.read()
                headers = response.getheaders()
                selected = (
                    self.command == "PUT"
                    and urlsplit(self.path).path.endswith("/data.parquet")
                    and (project / "proxy-ready").exists()
                    and not forwarded.is_set()
                )
                if selected:
                    requests.append(
                        {
                            "method": self.command,
                            "path": self.path,
                            "status": response.status,
                            "version": response.getheader("x-amz-version-id"),
                            "bytes": len(body),
                        }
                    )
                    forwarded.set()
                    assert release.wait(45), "the producer was not terminated after the real PUT"
                try:
                    self.send_response(response.status)
                    for name, value in headers:
                        if name.lower() not in (
                            "transfer-encoding",
                            "connection",
                            "content-length",
                        ):
                            self.send_header(name, value)
                    self.send_header(
                        "Content-Length",
                        response.getheader("Content-Length", "0")
                        if self.command == "HEAD"
                        else str(len(data)),
                    )
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            finally:
                connection.close()

        # BaseHTTPRequestHandler dispatches through these exact method names.
        do_GET = forward  # noqa: N815
        do_HEAD = forward  # noqa: N815
        do_PUT = forward  # noqa: N815
        do_POST = forward  # noqa: N815
        do_DELETE = forward  # noqa: N815

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            replace(access, endpoint_url=f"http://127.0.0.1:{server.server_port}"),
            forwarded,
            requests,
        )
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize("outcome", ["caller_killed", "sdk_timeout"])
def test_remote_put_withheld_reply_survives_caller_death_and_isolates_pending_session(
    tmp_path: Path, lazy_s3_access: S3Access, outcome: str
) -> None:
    candidate_before = _manifest()
    with _withheld_proxy(lazy_s3_access, tmp_path) as (access, forwarded, requests):
        process = _start(
            "produce",
            "object",
            tmp_path,
            access,
            "proxy_timeout" if outcome == "sdk_timeout" else "proxy_wait",
        )
        try:
            if not forwarded.wait(45):
                process.kill()
                stdout, stderr = process.communicate(timeout=10)
                pytest.fail("no real forwarded PUT reached the backend: " + stdout + stderr)
            assert process.poll() is None
            assert requests[0]["status"] == 200 and requests[0]["version"]
            if outcome == "caller_killed":
                process.kill()
            _finish(process, 73 if outcome == "sdk_timeout" else -9)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
    # Recovery uses the direct healthy endpoint; neither a new healthy connection
    # nor caller exit supplies the absent request acknowledgement.
    recoveries = [_recover(tmp_path, "object", lazy_s3_access) for _ in range(2)]
    pids = {process.pid}
    for recovery in recoveries:
        assert recovery["pending"] is True
        before, after = recovery["before"], recovery["recovered"]
        assert isinstance(before, dict) and isinstance(after, dict)
        pids.add(after["pid"])
        assert before["counts"] == after["counts"]
        assert before["resources"] == after["resources"]
    assert len(pids) == 3
    with client(lazy_s3_access) as s3:
        versions = s3.list_object_versions(Bucket=lazy_s3_access.bucket).get("Versions", [])
        assert any(item["VersionId"] == requests[0]["version"] for item in versions)
    _evidence(
        tmp_path,
        "slice-4c-object-withheld-response-" + outcome,
        {
            "producer_pid": process.pid,
            "outcome": outcome,
            "requests": requests,
            "recoveries": recoveries,
        },
        candidate_before,
    )
