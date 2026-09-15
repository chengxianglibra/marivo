"""Opt-in live Slice 0 feasibility, with disposable tables and bounded receipts.

Run as a module from the checkout. This never registers an execution backend.
"""

from __future__ import annotations

import argparse
import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

import ibis
import pyarrow as pa
import trino.dbapi
from trino.exceptions import TrinoQueryError

from marivo.analysis.compiler.nodes import RetainedPartSpec
from tests.lazy_multisource_qualification import (
    SnapshotCompiler,
    assertion_envelope,
    clickhouse_envelope_sql,
    decode_envelope,
    qualification_recipe,
)
from tests.multisource_environment.smoke import CLICKHOUSE_SETTINGS, verify_clickhouse_settings

BUDGET = 1_048_576


class ClickHouseProbe:
    """Small uncompressed HTTP/Arrow fixture reader, not a production transport."""

    def __init__(self) -> None:
        password = (
            (Path.home() / ".cache/marivo-multisource/secrets.env")
            .read_text()
            .strip()
            .split("=", 1)[1]
        )
        self.authorization = "Basic " + base64.b64encode(f"qualifier:{password}".encode()).decode()
        self.receipts: list[dict[str, object]] = []

    def request(
        self,
        sql: str,
        *,
        role: str,
        settings: dict[str, int] | None = None,
        query_id: str | None = None,
    ) -> bytes:
        query_id = query_id or "q0_" + uuid4().hex
        parameters = {
            "query_id": query_id,
            "enable_http_compression": "0",
            "wait_end_of_query": "1",
            "output_format_arrow_compression_method": "none",
        }
        parameters.update(
            {key: str(value) for key, value in (CLICKHOUSE_SETTINGS | (settings or {})).items()}
        )
        request = Request(
            "http://127.0.0.1:18123/?" + urlencode(parameters),
            data=sql.encode(),
            headers={"Authorization": self.authorization, "Accept-Encoding": "identity"},
        )
        started = time.monotonic()
        with urlopen(request, timeout=40) as response:
            if response.headers.get("Content-Encoding", "identity") != "identity":
                raise ValueError("Compressed response outside probe scope")
            raw = response.read(BUDGET + 1)
            if not isinstance(raw, bytes):
                raise ValueError("Expected a byte response")
            if len(raw) > BUDGET:
                raise ValueError("Probe response byte budget exceeded before Arrow decoding")
        self.receipts.append(
            {
                "role": role,
                "sql": sql,
                "query_id": query_id,
                "wire_bytes": len(raw),
                "elapsed_seconds": time.monotonic() - started,
                "http_complete": True,
                "request_settings": {
                    key: value for key, value in parameters.items() if key != "query_id"
                },
            }
        )
        return raw

    def arrow(
        self,
        sql: str,
        *,
        role: str,
        settings: dict[str, int] | None = None,
        query_id: str | None = None,
    ) -> pa.Table:
        raw = self.request(
            sql + " FORMAT ArrowStream", role=role, settings=settings, query_id=query_id
        )
        table = pa.ipc.open_stream(raw).read_all()
        if table.num_rows > 1024 or table.nbytes > BUDGET:
            raise ValueError("Probe decoded budget exceeded")
        return table


def clickhouse_qualification() -> dict[str, object]:
    probe = ClickHouseProbe()
    namespace = "q0_" + uuid4().hex
    source, recipe = qualification_recipe(database=namespace)
    table_name = namespace + ".orders"
    results: dict[str, object] = {}
    probe.request(f"CREATE DATABASE {namespace}", role="create_fixture")
    try:
        types = {
            "float64": "Nullable(Float64)",
            "int64": "Nullable(Int64)",
            "string": "Nullable(String)",
            "date": "Nullable(Date)",
        }
        columns = ", ".join(
            f"`{name}` {types[str(kind)]}" for name, kind in source.schema().items()
        )
        probe.request(
            f"CREATE TABLE {table_name} ({columns}) ENGINE=MergeTree ORDER BY tuple()",
            role="create_fixture",
        )
        effective = probe.arrow(
            "SELECT name,value FROM system.settings WHERE name IN ("
            + ",".join(repr(name) for name in CLICKHOUSE_SETTINGS)
            + ")",
            role="effective_settings",
        )
        verify_clickhouse_settings([(row["name"], row["value"]) for row in effective.to_pylist()])
        results["settings"] = effective.to_pylist()
        results["version"] = probe.arrow(
            "SELECT version() AS version", role="server_version"
        ).to_pylist()
        cases = (
            ("non_null_count", "(1,10),(2,NULL),(3,20)", False, False, False),
            ("empty_source", "", False, True, False),
            ("empty_source_scalar", "", False, False, False),
            ("empty_primary", "(1,10)", False, True, False),
            ("filtered_duplicate", "(1,-10),(1,-20)", True, False, True),
            ("filtered_null_identity", "(NULL,-10)", True, False, True),
            ("empty_primary_duplicate", "(1,-10),(1,-20)", True, True, True),
        )
        case_results: list[dict[str, object]] = []
        for name, values, positive, empty, invalid in cases:
            probe.request(f"TRUNCATE TABLE {table_name}", role="reset_fixture")
            if values:
                probe.request(
                    f"INSERT INTO {table_name} (id,amount) VALUES {values}", role="seed_fixture"
                )
            _, candidate = qualification_recipe(database=namespace, positive_only=positive)
            envelope = probe.arrow(
                clickhouse_envelope_sql(candidate, empty_primary=empty), role=name
            )
            assert envelope.schema == assertion_envelope(candidate).schema().to_pyarrow()
            assert sum(row["__q_kind"] == "assertion" for row in envelope.to_pylist()) == 2
            rejected = False
            try:
                decoded = decode_envelope(envelope, candidate)
            except ValueError:
                if not invalid:
                    raise
                rejected = True
            assert rejected == invalid
            if not invalid:
                expected: list[dict[str, float | int | None]] = (
                    [] if empty else [{"revenue": 30.0, "order_count": 2}]
                )
                if name == "empty_source_scalar":
                    expected = [{"revenue": None, "order_count": 0}]
                assert decoded.primary.to_pylist() == expected
                if name == "non_null_count":
                    assert [list(part.to_pylist()[0].values()) for _, part in decoded.parts] == [
                        [30.0, 2, 3],
                        [2, 3],
                    ]
                # Missing validation records fail closed on a real server result too.
                incomplete = envelope.take(
                    pa.array(
                        [i for i, row in enumerate(envelope.to_pylist()) if row["__q_check"] != 0],
                        type=pa.int64(),
                    )
                )
                try:
                    decode_envelope(incomplete, candidate)
                except ValueError:
                    pass
                else:
                    raise AssertionError("Missing check was published")
            case_results.append(
                {
                    "case": name,
                    "rows": envelope.num_rows,
                    "decoded_bytes": envelope.nbytes,
                    "rejected": rejected,
                    "records": envelope.to_pylist(),
                }
            )
        results["envelope_cases"] = case_results

        # Check the documented physical type semantics separately from the float fixture.
        typed = probe.arrow(
            "SELECT toDecimal128('30.75',2) AS amount, toTimeZone(toDateTime64('2024-01-02 08:00:00.123',3,'Asia/Shanghai'),'UTC') AS moment, isFinite(toFloat64('nan')) AS nan_finite, isFinite(toFloat64('inf')) AS inf_finite, isFinite(toFloat64('1.5')) AS ordinary_finite",
            role="physical_types",
        )
        row = typed.to_pylist()[0]
        assert row == {
            "amount": Decimal("30.75"),
            "moment": datetime(2024, 1, 2, 0, 0, 0, 123000, tzinfo=timezone.utc),
            "nan_finite": 0,
            "inf_finite": 0,
            "ordinary_finite": 1,
        }
        joined = probe.arrow(
            "SELECT r.value AS value FROM (SELECT 1 AS id) l LEFT JOIN (SELECT 2 AS id, 7 AS value) r ON l.id=r.id",
            role="join_null_behavior",
        )
        assert joined.to_pylist() == [{"value": None}]
        results["physical_types"] = {
            "schema": str(typed.schema),
            "values": {key: str(value) for key, value in row.items()},
            "unmatched_join": joined.to_pylist(),
        }

        concurrency: list[dict[str, object]] = []
        for shared, duplicates in ((0, False), (1, False), (1, False), (1, False), (1, True)):
            probe.request(f"TRUNCATE TABLE {table_name}", role="reset_concurrency")
            probe.request(
                f"INSERT INTO {table_name} (id,amount) VALUES (1,1)", role="seed_concurrency"
            )
            query_id = "q0_overlap_" + uuid4().hex
            settings = {
                "enable_shared_storage_snapshot_in_query": shared,
                "merge_tree_storage_snapshot_sleep_ms": 200,
            }
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(
                    probe.arrow,
                    clickhouse_envelope_sql(recipe),
                    role="concurrent_envelope",
                    settings=settings,
                    query_id=query_id,
                )
                deadline = time.monotonic() + 10
                observed = False
                while time.monotonic() < deadline and not pending.done():
                    active = probe.arrow(
                        f"SELECT count() AS n FROM system.processes WHERE query_id='{query_id}'",
                        role="observe_reader",
                    )
                    if active.to_pylist() == [{"n": 1}]:
                        observed = True
                        break
                    time.sleep(0.02)
                assert observed, "Reader never observed on server"
                writes = 0
                for identity in range(2, 14):
                    if pending.done():
                        break
                    values_sql = f"({identity},1)" + (f",({identity},1)" if duplicates else "")
                    probe.request(
                        f"INSERT INTO {table_name} (id,amount) VALUES {values_sql}",
                        role="concurrent_insert",
                    )
                    # Prove at least one acknowledged write while the reader still exists.
                    active = probe.arrow(
                        f"SELECT count() AS n FROM system.processes WHERE query_id='{query_id}'",
                        role="observe_after_write",
                    )
                    writes += int(active.to_pylist() == [{"n": 1}])
                    time.sleep(0.03)
                envelope = pending.result(timeout=30)
            assert writes > 0, "No acknowledged write overlaps a live reader"
            checks = {
                row["__q_check"]: row["__q_violations"]
                for row in envelope.to_pylist()
                if row["__q_kind"] == "assertion"
            }
            if duplicates:
                primary = next(row for row in envelope.to_pylist() if row["__q_kind"] == "primary")
                measures = {name: primary[name] for name in recipe.primary_columns}
                parts = [
                    [primary[name] for name in part.column_names]
                    for part in recipe.retained_parts
                    if isinstance(part, RetainedPartSpec)
                ]
                assert measures["order_count"] > 1
                try:
                    decode_envelope(envelope, recipe)
                except ValueError:
                    pass
                else:
                    raise AssertionError("Concurrent duplicate rows escaped assertions")
            else:
                decoded = decode_envelope(envelope, recipe)
                measures = decoded.primary.to_pylist()[0]
                parts = [list(part.to_pylist()[0].values()) for _, part in decoded.parts]
            consistent = measures["revenue"] == measures["order_count"] and parts == [
                [measures["revenue"], measures["order_count"], measures["order_count"]],
                [measures["order_count"], measures["order_count"]],
            ]
            if shared:
                assert consistent, (measures, parts)
                assert checks == {0: 0, 1: measures["order_count"] - 1 if duplicates else 0}
            concurrency.append(
                {
                    "query_id": query_id,
                    "shared_snapshot": shared,
                    "duplicate_writes": duplicates,
                    "source_assertions": checks,
                    "debug_snapshot_sleep_ms": 200,
                    "acknowledged_writes_while_reader_active": writes,
                    "primary": measures,
                    "parts": parts,
                    "consistent": consistent,
                }
            )
        assert not concurrency[0]["consistent"], (
            "Negative control did not expose independent snapshots; rerun required"
        )
        results["concurrency"] = concurrency
        probe.request("SYSTEM FLUSH LOGS", role="flush_terminal_receipts")
        ids = [entry["query_id"] for entry in concurrency]
        terminal = probe.arrow(
            "SELECT query_id,type,exception_code,read_rows,result_rows,result_bytes FROM system.query_log WHERE query_id IN ("
            + ",".join(repr(value) for value in ids)
            + ") AND type='QueryFinish'",
            role="terminal_receipts",
        )
        assert {row["query_id"] for row in terminal.to_pylist()} == set(ids)
        assert all(row["exception_code"] == 0 for row in terminal.to_pylist())
        results["terminal"] = terminal.to_pylist()
    finally:
        probe.request(f"DROP DATABASE {namespace} SYNC", role="cleanup_fixture")
    return {
        "backend": "clickhouse",
        "fixture": namespace,
        "results": results,
        "statements": probe.receipts,
        "dataset_acceptance": False,
        "pre_transfer_barrier_proven": False,
    }


def trino_qualification() -> dict[str, object]:
    connection = trino.dbapi.Connection(
        host="127.0.0.1",
        port=18080,
        user="qualifier",
        catalog="iceberg",
        timezone="UTC",
        max_attempts=1,
        request_timeout=30,
    )
    cursor = connection.cursor()
    receipts: list[dict[str, object]] = []

    def execute(sql: str, role: str) -> list[list[object]]:
        cursor.execute(sql)
        rows: list[list[object]] = cursor.fetchall()
        assert len(rows) <= 1024
        assert cursor.stats["state"] == "FINISHED" and cursor.query_id
        receipts.append(
            {
                "role": role,
                "sql": sql,
                "query_id": cursor.query_id,
                "rows": rows,
                "terminal_stats": cursor.stats,
                "wire_bytes": None,
                "decoded_json_bytes": len(json.dumps(rows, default=str).encode()),
            }
        )
        return rows

    namespace = "q0_" + uuid4().hex
    table_name = f"iceberg.{namespace}.orders"
    source, recipe = qualification_recipe(catalog="iceberg", database=namespace)
    results: dict[str, object] = {}
    execute(f"CREATE SCHEMA iceberg.{namespace}", "create_fixture")
    try:
        types = {"float64": "DOUBLE", "int64": "BIGINT", "string": "VARCHAR", "date": "DATE"}
        columns = ", ".join(
            f'"{name}" {types[str(kind)]}' for name, kind in source.schema().items()
        )
        execute(
            f"CREATE TABLE {table_name} ({columns}) WITH (format='PARQUET',format_version=2)",
            "create_fixture",
        )
        results["version"] = execute("SELECT version()", "server_version")
        execute(
            f"INSERT INTO {table_name} (id,amount) VALUES (1,10),(2,NULL),(3,20)", "seed_fixture"
        )
        rows = execute(
            f"SELECT snapshot_id FROM iceberg.{namespace}.\"orders$refs\" WHERE name='main'",
            "resolve_snapshot",
        )
        assert len(rows) == 1 and type(rows[0][0]) is int
        snapshot = rows[0][0]
        assert isinstance(snapshot, int)
        compiler = SnapshotCompiler(source, snapshot)
        frozen_sql = compiler.statement(recipe.expression)
        for check in recipe.validations:
            assert execute(
                compiler.statement(check.expression), "pinned_assertion_before:" + check.name
            ) == [[0]]
        expected: list[list[object]] = [[30.0, 2, 30.0, 2, 3, 2, 3]]
        assert execute(frozen_sql, "pinned_primary_before") == expected
        execute(
            f"INSERT INTO {table_name} (id,amount) VALUES (1,100),(NULL,50),(4,5)", "mutate_source"
        )
        for check in recipe.validations:
            assert execute(
                compiler.statement(check.expression), "pinned_assertion_after:" + check.name
            ) == [[0]]
            assert execute(
                str(ibis.to_sql(check.expression, dialect="trino")),
                "current_assertion:" + check.name,
            ) == [[1]]
        assert execute(frozen_sql, "pinned_primary_after") == expected
        assert execute(f"SELECT count(*),sum(amount) FROM {table_name}", "current_reference") == [
            [6, 185.0]
        ]
        # Expire the actual once-readable snapshot, not just an invented missing ID.
        execute(
            f"ALTER TABLE {table_name} EXECUTE expire_snapshots(retention_threshold => '0s')",
            "expire_snapshot",
        )
        snapshots = execute(
            f'SELECT snapshot_id FROM iceberg.{namespace}."orders$snapshots"', "remaining_snapshots"
        )
        assert snapshot not in [row[0] for row in snapshots]
        expired: list[dict[str, object]] = []
        for role, sql in [
            ("expired_primary", frozen_sql),
            *(
                ("expired_assertion:" + check.name, compiler.statement(check.expression))
                for check in recipe.validations
            ),
        ]:
            try:
                execute(sql, role)
            except TrinoQueryError as error:
                assert "snapshot" in str(error).lower()
                expired.append(
                    {
                        "role": role,
                        "sql": sql,
                        "query_id": error.query_id,
                        "error": str(error),
                        "terminal": "server_error",
                    }
                )
            else:
                raise AssertionError("Expired snapshot silently replaced")
        results.update(
            {
                "snapshot": snapshot,
                "expected_primary_and_parts": expected,
                "expired": expired,
                "snapshot_expired": True,
            }
        )
    finally:
        try:
            execute(f"DROP SCHEMA iceberg.{namespace} CASCADE", "cleanup_fixture")
        finally:
            cursor.close()
            connection.close()  # type: ignore[no-untyped-call] # Driver's close is unannotated.
    return {
        "backend": "trino",
        "fixture": namespace,
        "results": results,
        "statements": receipts,
        "dataset_acceptance": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backend", choices=("trino", "clickhouse"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = trino_qualification() if args.backend == "trino" else clickhouse_qualification()
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2, default=str) + "\n")
    print(f"{args.backend} live qualification passed; evidence: {args.output}")
