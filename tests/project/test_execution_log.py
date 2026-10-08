"""Always-on diagnostics, context isolation, retention and failure behavior."""

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from subprocess import run

import pytest

from marivo import _execution_log as log
from marivo import _jsonl
from marivo._compat import UTC
from tests.support.execution_logs import execution_records


def test_sql_is_complete_and_independent_of_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARIVO_TELEMETRY", "off")
    sql = "SELECT 'quoted\\nvalue' AS marker\n" + "-- diagnostic\n" * 1000
    with (
        log.scope(tmp_path, operation_id="operation", session_id="session", run_id="run"),
        log.QueryLog(sql, backend="duckdb", purpose="test.rows") as query,
    ):
        query.rows = 2
        query.arrow_bytes = 16
    submitted, completed = execution_records(tmp_path)
    assert submitted["sql"] == sql
    assert submitted["query_id"] == completed["query_id"]
    assert submitted["operation_id"] == completed["operation_id"] == "operation"
    assert completed["state"] == "succeeded"
    assert completed["consumed_rows"] == 2
    assert completed["consumed_arrow_bytes"] == 16
    assert not (tmp_path / ".marivo" / "telemetry").exists()
    directory = tmp_path / ".marivo" / "logs"
    assert directory.stat().st_mode & 0o777 == 0o700
    assert next(directory.glob("*.jsonl")).stat().st_mode & 0o777 == 0o600


def test_errors_are_bounded_and_parameterized_failures_disclose_no_message(tmp_path: Path) -> None:
    with log.scope(tmp_path):
        for sensitive in (False, True):
            with (
                pytest.raises(RuntimeError),
                log.QueryLog(
                    "SELECT ?", backend="duckdb", purpose="test.error", sensitive=sensitive
                ),
            ):
                raise RuntimeError("authorization='canary-secret' " + "x" * 1000)
    records = execution_records(tmp_path)
    failure = records[1]
    assert failure["state"] == "failed" and failure["error_type"] == "RuntimeError"
    assert len(str(failure["error_message"])) <= 500
    assert "canary-secret" not in str(records)
    assert "error_message" not in records[3]


def test_project_context_isolated_across_threads_and_nested_roots(tmp_path: Path) -> None:
    roots = [tmp_path / str(index) for index in range(4)]

    def write(root: Path) -> None:
        with log.scope(root, session_id=root.name):
            for index in range(20):
                with log.QueryLog(str(index), backend="duckdb", purpose="thread.rows"):
                    pass

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, roots))
    for root in roots:
        records = execution_records(root)
        assert len(records) == 40
        assert {record["session_id"] for record in records} == {root.name}
    with (
        log.scope(roots[0], session_id="outer"),
        log.QueryLog("SELECT 1", backend="duckdb", purpose="nested", project_root=roots[1]),
    ):
        pass
    assert "session_id" not in execution_records(roots[1])[-1]


def test_concurrent_process_append_keeps_jsonl_parseable(tmp_path: Path) -> None:
    script = """
import sys
from pathlib import Path
from marivo import _execution_log as log
with log.scope(Path(sys.argv[1])):
    for i in range(20):
        with log.QueryLog('SELECT ' + str(i), backend='duckdb', purpose='process.rows'):
            pass
"""

    def write(_: int) -> None:
        run([sys.executable, "-c", script, str(tmp_path)], check=True, capture_output=True)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(write, range(3)))
    records = execution_records(tmp_path)
    assert len(records) == 120
    assert len({record["query_id"] for record in records}) == 60


def _set_time(monkeypatch: pytest.MonkeyPatch, instant: datetime) -> None:
    class Clock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            del tz
            return instant

    monkeypatch.setattr(log, "datetime", Clock)


def test_date_and_size_rolling_preserve_whole_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(log, "_MAX_FILE_BYTES", 1)
    with log.scope(tmp_path):
        _set_time(monkeypatch, datetime(2026, 10, 7, 23, 59, tzinfo=UTC))
        log.emit("first", sql="SELECT 1" * 1000)
        log.emit("second")
        _set_time(monkeypatch, datetime(2026, 10, 8, tzinfo=UTC))
        log.emit("third")
    assert sorted(path.name for path in (tmp_path / ".marivo" / "logs").glob("*.jsonl")) == [
        "execution-2026-10-07.000.jsonl",
        "execution-2026-10-07.001.jsonl",
        "execution-2026-10-08.000.jsonl",
    ]
    assert execution_records(tmp_path)[0]["sql"] == "SELECT 1" * 1000


def test_retention_and_size_cleanup_preserve_current_and_unmanaged_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / ".marivo" / "logs"
    directory.mkdir(parents=True)
    files = {
        "execution-2026-09-24.000.jsonl": "expired",
        "execution-2026-09-25.000.jsonl": "oldest-retained",
        "execution-2026-10-07.000.jsonl": "newest-retained",
        "execution-2026-10-08.000.jsonl": "current",
        "execution-2026-09-24.notes.jsonl": "unmanaged",
        "execution-2026-09-24.1.jsonl": "unmanaged-segment",
    }
    for name, content in files.items():
        (directory / name).write_text(content)
    _set_time(monkeypatch, datetime(2026, 10, 8, tzinfo=UTC))
    monkeypatch.setattr(log, "_MAX_FILE_BYTES", 1)
    monkeypatch.setattr(log, "_MAX_HISTORICAL_BYTES", len("newest-retained"))
    with log.scope(tmp_path):
        log.emit("cleanup")
    assert not (directory / "execution-2026-09-24.000.jsonl").exists()
    assert not (directory / "execution-2026-09-25.000.jsonl").exists()
    for name in list(files)[2:]:
        assert (directory / name).read_text() == files[name]


def test_write_failures_are_isolated_rate_limited_and_counted_per_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    original = _jsonl.append

    def fail(_path: Path, _payload: bytes) -> None:
        raise OSError("canary-secret")

    monkeypatch.setattr(_jsonl, "append", fail)
    with log.scope(tmp_path):
        log.emit("lost")
        log.emit("lost-again")
    assert len(caplog.records) == 1
    assert "canary-secret" not in caplog.text
    monkeypatch.setattr(_jsonl, "append", original)
    with log.scope(tmp_path / "other"):
        log.emit("unaffected")
    with log.scope(tmp_path):
        log.emit("recovered")
    assert execution_records(tmp_path)[0]["dropped_since_last_write"] == 2
    assert "dropped_since_last_write" not in execution_records(tmp_path / "other")[0]


def test_cleanup_failure_does_not_count_a_successful_append_as_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    original = _jsonl.prune

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("cleanup unavailable")

    monkeypatch.setattr(_jsonl, "prune", fail)
    with log.scope(tmp_path):
        log.emit("written")
        log.emit("also-written")
    assert len(caplog.records) == 1
    monkeypatch.setattr(_jsonl, "prune", original)
    with log.scope(tmp_path):
        log.emit("recovered")
    assert len(execution_records(tmp_path)) == 3
    assert all("dropped_since_last_write" not in record for record in execution_records(tmp_path))
