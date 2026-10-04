"""Real R9.2 physical profiles; opt-in remote services are never started here."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import ibis
import pyarrow as pa
import pytest
from ibis.backends import BaseBackend

from marivo.datasource import adapters
from marivo.datasource.adapters import PhysicalRequirement, _Cursor
from marivo.datasource.capabilities import provider_statement_log
from marivo.datasource.engines.base import MetadataInspectRequest
from marivo.datasource.errors import DatasourceSourceCapabilityError
from marivo.datasource.ir import TableSourceIR
from scripts.r9_qualification_requirements import PROFILES
from tests.r9_source_cases import ROWS, SourceData, receipt, source_case

pytestmark = pytest.mark.runtime
CASES = [(backend, profile) for backend, profiles in PROFILES.items() for profile in profiles]


class FaultCursor:
    """Inject after a real driver submission; always release the native cursor."""

    def __init__(self, native: _Cursor, fault: str):
        self.native = native
        self.fault = fault
        self.closed = False

    def fetchmany(self, size: int) -> Sequence[Sequence[object]]:
        if self.fault == "fetch":
            raise OSError("r92 fetch failure")
        rows = self.native.fetchmany(size)
        if self.fault == "decode" and rows:
            return [(1.5, *rows[0][1:])]
        return rows

    def close(self) -> None:
        self.native.close()
        self.closed = True
        if self.fault == "close":
            raise OSError("r92 close failure")


@pytest.mark.parametrize("backend,profile", CASES, ids=[f"{b}-{p}" for b, p in CASES])
def test_source_profile(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submitted: list[str] = []
    cursors: list[FaultCursor] = []
    fault = ""
    native = adapters._native_cursor

    def capture(owner: BaseBackend, name: str, sql: str) -> _Cursor:
        submitted.append(sql)
        cursor = FaultCursor(native(owner, name, sql), fault)
        cursors.append(cursor)
        return cursor

    monkeypatch.setattr(adapters, "_native_cursor", capture)
    with source_case(backend, profile, tmp_path, monkeypatch) as case:
        session = case.session
        bound = session.bind(
            case.source, source_identity=f"r92:{backend}:{profile}", source_params=case.params
        )
        assert bound.facts.business_coverage == "unknown"
        assert bound.facts.observed_scope == "unknown"
        metadata: dict[str, object] = {"schema": str(bound.facts.schema)}
        if isinstance(case.source, TableSourceIR):
            facts = session.provider.metadata.inspect_table(
                MetadataInspectRequest(
                    datasource="r92",
                    backend=session._backend,
                    table=case.source.table,
                    database=case.source.database,
                    table_expr=bound.relation,
                    include_partitions=True,
                    datasource_ir=session.datasource,
                )
            )
            assert {column.name for column in facts.columns} == set(ROWS[0])
            assert facts.is_view == ("view" in profile)
            metadata.update(
                {"is_view": facts.is_view, "columns": [column.name for column in facts.columns]}
            )
        qualified = session.qualify(
            bound, PhysicalRequirement("r92.basic", 1, frozenset({"scan", "filter", "project"}))
        )
        expression = bound.relation.order_by("tenant", "id", "revision")
        read = session.compile(
            qualified,
            expression,
            purpose="r92.basic",
            expected_schema=expression.schema().to_pyarrow(),
        )
        batches = list(session.batches(read, chunk_size=1))
        assert len(batches) == 3
        actual = pa.Table.from_batches(batches, schema=read.schema).to_pylist()
        assert actual == ROWS
        assert submitted[-1] == read.sql
        assert asdict(session.submissions[-1])["state"] == "succeeded"
        assert cursors[-1].closed and not session._streams

        parameter = ibis.param("int64")
        filtered = expression.filter(expression.revision == parameter).select("tenant", "id")
        filtered_read = session.compile(
            qualified,
            filtered,
            params={parameter: 2},
            purpose="r92.parameter",
            expected_schema=filtered.schema().to_pyarrow(),
        )
        result = pa.Table.from_batches(
            session.batches(filtered_read, chunk_size=1), schema=filtered_read.schema
        ).to_pylist()
        assert result == [{"tenant": "a", "id": 9007199254740993}]
        assert submitted[-1] == filtered_read.sql

        empty = expression.filter(expression.id < 0)
        empty_read = session.compile(
            qualified, empty, purpose="r92.empty", expected_schema=empty.schema().to_pyarrow()
        )
        stream = session.batches(empty_read, chunk_size=1)
        assert stream.schema.equals(read.schema)
        assert list(stream) == []
        assert cursors[-1].closed and not session._streams
        early = session.batches(read, chunk_size=1)
        assert next(iter(early)).num_rows == 1
        early.close()
        assert asdict(session.submissions[-1])["state"] == "closed_early"
        assert cursors[-1].closed and not session._streams

        for fault in ("fetch", "decode", "close"):
            error = DatasourceSourceCapabilityError if fault == "decode" else OSError
            with pytest.raises(error) as failure:
                list(session.batches(read, chunk_size=1))
            if fault == "decode":
                assert isinstance(failure.value, DatasourceSourceCapabilityError)
                assert failure.value.expected and failure.value.received and failure.value.repair
            assert asdict(session.submissions[-1])["state"] == "failed", fault
            if fault == "close":
                assert session.submissions[-1].cursor_state == "close_failed"
            assert cursors[-1].closed and not session._streams
        fault = ""
        active = session.batches(read, chunk_size=1)
        assert next(iter(active)).num_rows == 1
        termination = session.interrupt()
        assert termination == (
            "local_closed" if backend in {"duckdb", "sqlite"} else "remote_unknown"
        )
        assert cursors[-1].closed and not session._streams
        assert all(item.connection_disconnected for item in session.submissions)
        evidence: dict[str, object] = {
            "backend": backend,
            "profile": profile,
            "environment": case.environment,
            "oracle": ROWS,
            "actual": actual,
            "metadata": metadata,
            "batch_rows": [batch.num_rows for batch in batches],
            "actual_native_submissions": submitted,
            "submissions": [asdict(item) for item in session.submissions],
            "provider_statements": [
                asdict(item) for item in provider_statement_log(session._backend)
            ],
            "faults": ["fetch", "decode", "close"],
            "termination": termination,
            "remote_termination_proof": False,
        }
    receipt(f"profile-{backend}-{profile}", evidence)


RISKS = (
    "int64-overflow-nullable",
    "decimal-precision-scale",
    "native-parsed-time-dst",
    "composite-versioned-identity",
)


def risk_data(backend: str, risk: str) -> SourceData:
    if risk == "decimal-precision-scale":
        rows: list[dict[str, object]] = [
            {"id": 1, "value": Decimal("9007199254.740993")},
            {"id": 2, "value": Decimal("-9007199254.740994")},
            {"id": 3, "value": None},
        ]
        return SourceData(
            "id BIGINT, value DECIMAL(18,6)",
            "(1,9007199254.740993),(2,-9007199254.740994),(3,NULL)",
            "id Int64, value Nullable(Decimal(18,6))",
            rows,
        )
    if risk == "native-parsed-time-dst":
        # The repeated local wall time is paired with two distinct UTC instants.
        utc = backend == "clickhouse"
        rows = [
            {
                "id": 1,
                "value": datetime(
                    2026, 11, 1, 5, 30, 0, 123456, tzinfo=timezone.utc if utc else None
                ),
                "wall": datetime(
                    2026, 11, 1, 1, 30, 0, 123456, tzinfo=timezone.utc if utc else None
                ),
            },
            {
                "id": 2,
                "value": datetime(
                    2026, 11, 1, 6, 30, 0, 123457, tzinfo=timezone.utc if utc else None
                ),
                "wall": datetime(
                    2026, 11, 1, 1, 30, 0, 123456, tzinfo=timezone.utc if utc else None
                ),
            },
        ]
        values = "(1,'2026-11-01 05:30:00.123456','2026-11-01 01:30:00.123456'),(2,'2026-11-01 06:30:00.123457','2026-11-01 01:30:00.123456')"
        if backend == "trino":
            values = values.replace("'2026-", "TIMESTAMP '2026-")
        timestamp = "DATETIME(6)" if backend == "mysql" else "TIMESTAMP(6)"
        if backend == "sqlite":
            timestamp = "TIMESTAMP"
        return SourceData(
            f"id BIGINT, value {timestamp}, wall {timestamp}",
            values,
            "id Int64, value DateTime64(6,'UTC'), wall DateTime64(6,'UTC')",
            rows,
        )
    if risk == "int64-overflow-nullable":
        rows = [
            {"id": 1, "value": -(2**63)},
            {"id": 2, "value": 2**63 - 1},
            {"id": 3, "value": None},
        ]
        return SourceData(
            "id BIGINT, value BIGINT",
            "(1,-9223372036854775808),(2,9223372036854775807),(3,NULL)",
            "id Int64, value Nullable(Int64)",
            rows,
        )
    return SourceData(
        "id BIGINT, amount BIGINT, tenant VARCHAR(10), revision BIGINT",
        "(9007199254740992,2,'a',1),(9007199254740993,NULL,'a',2),(9007199254740993,4,'b',1)",
        "id Int64, amount Nullable(Int64), tenant LowCardinality(String), revision Int64",
        ROWS,
    )


@pytest.mark.parametrize("backend", list(PROFILES))
@pytest.mark.parametrize("risk", RISKS)
def test_source_type_risk(
    backend: str, risk: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = risk_data(backend, risk)
    profile = PROFILES[backend][0]
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        session = case.session
        binding = session.bind(case.source, source_identity=f"r92:{backend}:{risk}")
        qualified = session.qualify(
            binding, PhysicalRequirement("r92.types", 1, frozenset({"scan", "project"}))
        )
        expression = binding.relation.order_by(
            *(["tenant", "id", "revision"] if risk == "composite-versioned-identity" else ["id"])
        )
        read = session.compile(
            qualified,
            expression,
            purpose="r92.types",
            expected_schema=expression.schema().to_pyarrow(),
        )
        blocker: str | None = None
        actual: list[dict[str, object]] = []
        if backend == "sqlite" and risk == "decimal-precision-scale":
            with pytest.raises(DatasourceSourceCapabilityError) as failure:
                list(session.batches(read, chunk_size=1))
            assert failure.value.expected and failure.value.received and failure.value.repair
            assert session.submissions[-1].state == "failed"
            blocker = "SQLite NUMERIC affinity returns float rather than exact Decimal; required exact-decimal source success remains blocked"
        else:
            actual = pa.Table.from_batches(
                session.batches(read, chunk_size=1), schema=read.schema
            ).to_pylist()
            assert actual == data.rows
            if risk == "decimal-precision-scale":
                assert read.schema.field("value").type == pa.decimal128(18, 6)
            if risk == "int64-overflow-nullable":
                with pytest.raises(DatasourceSourceCapabilityError) as failure:
                    adapters._exact_array([2**63], read.schema.field("value"), backend_name=backend)
                assert failure.value.expected and failure.value.received and failure.value.repair
            if risk == "composite-versioned-identity":
                assert len({(row["tenant"], row["id"], row["revision"]) for row in actual}) == 3
            if risk == "native-parsed-time-dst":
                assert actual[0]["wall"] == actual[1]["wall"]
                assert actual[0]["value"] != actual[1]["value"]
                assert isinstance(actual[0]["value"], datetime)
                assert actual[0]["value"].microsecond == 123456
        assert not session._streams
        session.close()
        assert all(item.connection_disconnected for item in session.submissions)
        evidence: dict[str, object] = {
            "backend": backend,
            "risk": risk,
            "environment": case.environment,
            "oracle": data.rows,
            "actual": actual,
            "schema": str(read.schema),
            "blocker": blocker,
            "submissions": [asdict(item) for item in session.submissions],
            "remote_termination_proof": False,
        }
    receipt(f"risk-{backend}-{risk}", evidence)
