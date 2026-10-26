"""Public method counterexamples on exact physical source shapes."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypeAlias

import ibis
import pytest

import marivo.semantic as ms
from marivo.analysis.compiler.source_time import source_time
from marivo.datasource.adapters import PhysicalRequirement, provider_for
from marivo.datasource.errors import DatasourceConnectionError
from marivo.refs import RefPayloadV1
from marivo.semantic.ir import TargetDimensionContract, TimestampParse
from tests.datasource.source_cases import SourceData, source_case


@pytest.mark.runtime
def test_clickhouse_physical_utc_window_is_independent_of_session_timezone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.datasource.environment import clickhouse_analysis as ch

    data = SourceData(
        "id BIGINT, happened TIMESTAMP",
        "(1,'2026-08-01 00:00:00')",
        "id Int64, happened DateTime64(6, 'UTC')",
        [{"id": 1, "happened": datetime(2026, 8, 1, tzinfo=timezone.utc)}],
    )
    with source_case("clickhouse", "mergetree", tmp_path, monkeypatch, data=data) as case:
        bound = case.session.bind(case.source, source_identity="r93.utc")
        axis = TargetDimensionContract(
            RefPayloadV1.from_ref(ms.ref.time_dimension("sales.facts.happened")),
            RefPayloadV1.from_ref(ms.ref.entity("sales.facts")),
            "happened",
            "timestamp",
            False,
            True,
            None,
            True,
            "UTC",
            TimestampParse(),
        )
        value, authority = source_time(
            bound.relation.happened,
            axis,
            boundary_timezone="UTC",
            read_timezone=None,
            engine="clickhouse",
        )
        assert authority.source == "physical"
        start = ibis.literal(datetime(2026, 8, 1), type=value.type())
        end = ibis.literal(datetime(2026, 8, 2), type=value.type())
        expression = bound.relation.filter((value >= start) & (value < end)).select("id")
        qualified = case.session.qualify(
            bound, PhysicalRequirement("r93.utc", 1, frozenset({"scan", "filter", "project"}))
        )
        read = case.session.compile(
            qualified,
            expression,
            purpose="r93.utc",
            expected_schema=expression.schema().to_pyarrow(),
        )
        # Test-only independent connection settings do not rewrite product SQL.
        observations: list[dict[str, object]] = []
        with ch.connection(admin=True) as observer:
            for zone in ("UTC", "America/New_York", "Asia/Shanghai"):
                rows = observer.query(read.sql, settings={"session_timezone": zone}).result_rows
                assert rows == [(1,)]
                observations.append({"session_timezone": zone, "rows": rows})
        evidence_dir = os.environ.get("MARIVO_R93_EVIDENCE_DIR")
        if evidence_dir:
            Path(evidence_dir, "clickhouse-utc-window.json").write_text(
                json.dumps(
                    {
                        "environment": case.environment,
                        "actual_sql": read.sql,
                        "observations": observations,
                        "oracle": [[1]],
                        "boundary": "Independent test connection settings verify physical UTC window semantics; not complete C06 or cancellation qualification",
                    },
                    sort_keys=True,
                )
            )


@pytest.mark.runtime
def test_mysql_reader_initialization_failure_is_typed_and_releases_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pymysql.cursors import Cursor

    with source_case("mysql", "table", tmp_path, monkeypatch) as case:
        original = Cursor.execute
        rejected: list[Cursor] = []

        def execute(cursor: Cursor, query: str, args: object = None) -> int:
            if query == "SET @@session.time_zone = 'UTC'":
                rejected.append(cursor)
                raise RuntimeError("synthetic UTC initialization failure")
            return original(cursor, query, args)

        monkeypatch.setattr(Cursor, "execute", execute)
        with (
            pytest.raises(DatasourceConnectionError) as failure,
            provider_for("mysql").open(case.session.datasource),
        ):
            pytest.fail("A failed UTC reader was admitted")
        assert failure.value.received == "timezone initialization failure"
        assert len(rejected) == 1 and rejected[0].connection is None


SourceStatisticMethod: TypeAlias = Literal[
    "pearson",
    "spearman",
    "kendall",
    "zscore",
    "mad",
    "naive",
    "drift",
    "seasonal_naive",
    "runs",
]

# One recovery representative per result type; test_analysis_views owns all source method oracles.
SOURCE_STATISTIC_REPRESENTATIVES: tuple[SourceStatisticMethod, ...] = (
    "pearson",
    "zscore",
    "naive",
    "runs",
)
SOURCE_STATISTIC_FAMILIES: dict[SourceStatisticMethod, str] = {
    "pearson": "association",
    "zscore": "deviation",
    "naive": "forecast",
    "runs": "time",
}
