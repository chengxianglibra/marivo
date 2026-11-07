"""Finite SQL parameters retain exact native types without HTTP external data."""

from __future__ import annotations

import http.client
import math
import os
from collections.abc import Callable, Iterator
from contextlib import closing
from datetime import date, datetime, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import ibis
import pandas as pd
import pytest
from clickhouse_connect.driver.exceptions import DatabaseError
from clickhouse_connect.driver.external import ExternalData

import marivo.analysis as mv
from marivo.datasource._calendar_relations import CalendarData, calendar_data, compile_calendar_data
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.environment import clickhouse_analysis as ch
from tests.datasource.source_cases import SourceData, receipt, source_case
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source


@pytest.fixture
def no_multipart(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """One local HTTP boundary rejects uploads and records only content types."""
    if os.environ.get("MARIVO_CLICKHOUSE_ANALYSIS_TEST") != "1":
        pytest.skip("Explicit ready ClickHouse service required")
    target = int(os.environ.get("MARIVO_CLICKHOUSE_TEST_PORT", "18123"))
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            content_type = self.headers.get("Content-Type", "")
            requests.append(content_type)
            if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                chunks: list[bytes] = []
                while size := int(self.rfile.readline().split(b";", 1)[0], 16):
                    chunks.append(self.rfile.read(size))
                    self.rfile.read(2)
                self.rfile.readline()
                body = b"".join(chunks)
            else:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if content_type.startswith("multipart/"):
                output = b"multipart disabled by test boundary"
                self.send_response(415)
                self.send_header("Content-Length", str(len(output)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(output)
                return
            headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in ("host", "transfer-encoding", "content-length", "connection")
            }
            with closing(http.client.HTTPConnection("127.0.0.1", target, timeout=30)) as connection:
                connection.request("POST", self.path, body=body, headers=headers)
                response = connection.getresponse()
                output = response.read()
                self.send_response(response.status)
                for key, value in response.getheaders():
                    if key.lower() not in (
                        "content-length",
                        "transfer-encoding",
                        "connection",
                        "keep-alive",
                    ):
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(output)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(output)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("MARIVO_CLICKHOUSE_TEST_PORT", str(server.server_port))
    try:
        yield requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _parameters() -> CalendarData:
    schema = ibis.schema(
        {
            "instant": "timestamp('UTC', 6)",
            "day": "date",
            "signed": "int64",
            "unsigned": "uint64",
            "flag": "boolean",
            "text": "string",
            "decimal": "decimal(24, 6)",
            "float": "float64",
            "small_float": "float32",
        }
    )
    point = datetime(1969, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
    rows = (
        (
            point,
            date(2026, 1, 2),
            -(2**63),
            2**64 - 1,
            True,
            "quote'\\\n雪",
            Decimal("123456789012.123456"),
            float("inf"),
            -0.0,
        ),
        (None,) * 9,
        (
            point,
            date(1960, 1, 3),
            0,
            0,
            False,
            "",
            Decimal("-0.000001"),
            float("nan"),
            float("-inf"),
        ),
    )
    node = calendar_data(schema, rows).op()
    assert isinstance(node, CalendarData)
    return node


def test_clickhouse_parameter_sql_has_typed_wire_values_and_no_external_table() -> None:
    node = _parameters()
    sql = compile_calendar_data(node, "clickhouse").sql(dialect="clickhouse")
    assert "JSONExtract" in sql and "arrayJoin" in sql
    assert "fromUnixTimestamp64Micro" in sql
    assert "18446744073709551615" in sql and "123456789012.123456" in sql
    assert node.name not in sql
    assert "Array(Tuple(Nullable(Int64), Nullable(String)" in sql
    assert len(sql.encode()) < 65536


@pytest.mark.runtime
def test_clickhouse_parameters_roundtrip_when_multipart_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_multipart: list[str]
) -> None:
    data = SourceData("id BIGINT", "(1)", "id Int64", [{"id": 1}])
    with source_case("clickhouse", "mergetree", tmp_path, monkeypatch, data) as case:
        with ch.connection() as client:
            external = ExternalData(
                file_name="external.csv", data=b"1\n", fmt="CSV", structure=["id Int64"]
            )
            with pytest.raises(DatabaseError, match=r"415|multipart disabled"):
                client.query("SELECT * FROM external", external_data=external)
        assert sum(item.startswith("multipart/") for item in no_multipart) == 1
        before = len(no_multipart)
        node = _parameters()
        actual = case.session.collect_bounded(
            node.to_expr(), purpose="calendar.parameters", max_rows=4
        )
        assert len(actual) == 3
        assert pd.Timestamp(actual.instant.iloc[0]) == pd.Timestamp("1969-12-31T23:59:59.999999Z")
        assert actual.day.iloc[0] == date(2026, 1, 2)
        assert actual.day.iloc[2] == date(1960, 1, 3)
        assert actual.signed.iloc[0] == -(2**63) and actual.unsigned.iloc[0] == 2**64 - 1
        assert bool(actual.flag.iloc[0]) and not bool(actual.flag.iloc[2])
        assert actual.text.iloc[0] == "quote'\\\n雪"
        assert actual.decimal.iloc[0] == Decimal("123456789012.123456")
        assert math.isinf(actual.float.iloc[0]) and math.isnan(actual.float.iloc[2])
        assert math.copysign(1, actual.small_float.iloc[0]) == -1
        assert actual.iloc[1].isna().all()
        empty = calendar_data(node.schema, ())
        assert case.session.collect_bounded(
            empty, purpose="calendar.empty_parameters", max_rows=1
        ).empty
        assert not any(item.startswith("multipart/") for item in no_multipart[before:])
        receipt(
            "calendar-parameter-transport",
            {
                "environment": case.environment,
                "multipart_negative": 1,
                "successful_content_types": no_multipart[before:],
                "returned_rows": len(actual),
                "sql_bytes": [len(read.sql.encode()) for read in case.session.submissions],
            },
        )


@pytest.mark.runtime
@pytest.mark.parametrize("profile", ["mergetree", "distributed"])
def test_native_calendar_and_actual_samples_without_multipart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    no_multipart: list[str],
    profile: str,
) -> None:
    points = pd.date_range("2026-01-01", periods=4032, freq="5min", tz="UTC")
    data = SourceData(
        "id BIGINT",
        "",
        "id Int64, point Nullable(DateTime64(6, 'UTC')), amount Int64, category String",
        [
            {"id": i, "point": point.to_pydatetime(), "amount": 2, "category": "A"}
            for i, point in enumerate(points)
        ]
        + [{"id": 4032, "point": None, "amount": 999, "category": "A"}],
    )
    monkeypatch.chdir(tmp_path)
    with source_case("clickhouse", "mergetree", tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        table_name = case.source.table + ("_native_distributed" if profile == "distributed" else "")
        try:
            if profile == "distributed":
                with ch.connection(admin=True) as admin:
                    admin.command(
                        f"CREATE TABLE qualification.{table_name} AS qualification.{case.source.table} ENGINE=Distributed(marivo_single_shard, qualification, {case.source.table}, rand())"
                    )
            semantic_project_factory(
                {
                    "datasources/warehouse.py": _datasource_model("clickhouse", case, monkeypatch),
                    "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Test', default=True)\n",
                    "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                    + f"facts=ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table({table_name!r}, database='qualification'), primary_key=['id'])\n"
                    + "point=ms.time_dimension_column(name='point', entity=facts, column='point', sample_interval=(5, 'minute'))\n"
                    + "amount=ms.measure_column(name='amount', entity=facts, column='amount', additivity=ms.additive_all())\n"
                    + "category=ms.dimension_column(name='category', entity=facts, column='category')\n",
                }
            )
            session = mv.session.get_or_create("native-calendar-" + profile, timezone="UTC")
            point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
            amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
            category = catalog_value(session.catalog, "sales.facts.category", kind="dimension")
            total = amount.sum(time=point)
            trace = capture_source(monkeypatch)
            scope = session.time_scope(start="2026-01-01", end="2026-01-16")
            sampled = total.per(mv.grain("minute", count=5))
            internal = (
                session.observe(
                    {
                        "mean": sampled.mean(),
                        "count": sampled.count(),
                        "nested_p95": sampled.quantile(0.95).per(mv.grain("day")).mean(),
                    },
                    where=scope,
                )
                .compute()
                .to_pandas()
            )
            assert internal["mean"].tolist() == [2] and internal["count"].tolist() == [4032]
            assert internal["nested_p95"].tolist() == [2]
            fixed = session.observe(
                {"v": total}, by=(scope.by(mv.grain("minute", count=5)),)
            ).compute()
            rows = fixed.to_pandas()
            assert len(rows) == 4320 and rows.v.tolist() == [2] * 4032 + [0] * 288
            empty_scope = session.time_scope(start="2026-01-17", end="2026-01-18")
            empty_rows = (
                session.observe(
                    {"sum": total + 1, "mean": amount.mean(time=point) + 1},
                    by=(empty_scope.by(mv.grain("day")),),
                )
                .compute()
                .to_pandas()
            )
            assert empty_rows["sum"].tolist() == [1] and empty_rows["mean"].isna().all()
            assert (
                session.observe({"v": total}, by=(empty_scope.by(mv.grain("day")), category))
                .compute()
                .to_pandas()
                .empty
            )
            for zone in ("UTC", "Asia/Shanghai"):
                for unit in ("day", "week", "month"):
                    axis_scope = session.time_scope(
                        start="2026-01-01", end="2026-01-16", timezone=zone
                    )
                    observed = (
                        session.observe({"v": total}, by=(axis_scope.by(mv.grain(unit)),))
                        .compute()
                        .to_pandas()
                    )
                    assert observed.v.sum() == 8064
            saved = fixed.persist()
            before = len(trace.native_sql)
            restored = session.persisted_frame(saved.artifact_id)
            pd.testing.assert_series_equal(restored.to_pandas().v, rows.v)
            periods = restored.read_periods(restored.axes[0], limit=2)
            assert len(periods) == 2 and not hasattr(periods.items[0], "start")
            assert len(trace.native_sql) == before
            assert all(len((sql + "\n FORMAT Native").encode()) < 65536 for sql in trace.native_sql)
            assert not any(item.startswith("multipart/") for item in no_multipart)
            receipt(
                "native-calendar-" + profile,
                {
                    "environment": case.environment,
                    "profile": profile,
                    "internal_samples": 4032,
                    "internal_mean": 2,
                    "output_rows": 4320,
                    "sql_bytes": [
                        len((sql + "\n FORMAT Native").encode()) for sql in trace.native_sql
                    ],
                    "sql": trace.native_sql,
                    "http_content_types": no_multipart,
                },
            )
        finally:
            if profile == "distributed":
                with ch.connection(admin=True) as admin:
                    admin.command(f"DROP TABLE IF EXISTS qualification.{table_name}")
