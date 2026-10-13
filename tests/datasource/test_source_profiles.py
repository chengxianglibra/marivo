"""Real R9.2 physical profiles; opt-in remote services are never started here."""

from __future__ import annotations

from dataclasses import asdict
from math import isnan
from pathlib import Path

import ibis
import pytest

from marivo.datasource.adapters import PhysicalRequirement
from marivo.datasource.capabilities import provider_statement_log
from marivo.datasource.engines.base import MetadataInspectRequest
from marivo.datasource.ir import TableSourceIR
from tests.datasource.source_cases import (
    PROFILES,
    ROWS,
    receipt,
    source_case,
    source_params,
)

# Shared local source-profile assertions use DuckDB.
CASES = [(backend, profile) for backend, profiles in PROFILES.items() for profile in profiles]


@pytest.mark.parametrize(
    "backend,profile", source_params(CASES), ids=[f"{b}-{p}" for b, p in CASES]
)
def test_source_profile(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with source_case(backend, profile, tmp_path, monkeypatch) as case:
        session = case.session
        bound = session.bind(
            case.source,
            source_identity=f"source_profile:{backend}:{profile}",
            source_params=case.params,
        )
        assert bound.facts.business_coverage == "unknown"
        assert bound.facts.observed_scope == "unknown"
        metadata: dict[str, object] = {"schema": str(bound.facts.schema)}
        if isinstance(case.source, TableSourceIR):
            facts = session.provider.metadata.inspect_table(
                MetadataInspectRequest(
                    datasource="source_profile",
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
            bound,
            PhysicalRequirement(
                "source_profile.basic", 1, frozenset({"scan", "filter", "project"})
            ),
        )
        expression = bound.relation.order_by("tenant", "id", "revision")
        read = session.compile(
            qualified,
            expression,
            purpose="source_profile.basic",
            expected_schema=expression.schema().to_pyarrow(),
        )
        result = session.execute(read)
        actual = result.to_dict(orient="records")
        assert actual[0] == ROWS[0]
        assert actual[1]["id"] == ROWS[1]["id"]
        assert actual[1]["tenant"] == ROWS[1]["tenant"]
        assert actual[1]["revision"] == ROWS[1]["revision"]
        assert isnan(actual[1]["amount"])
        assert actual[2] == ROWS[2]
        assert session.submissions[-1].sql == read.sql
        assert asdict(session.submissions[-1])["state"] == "succeeded"

        parameter = ibis.param("int64")
        filtered = expression.filter(expression.revision == parameter).select("tenant", "id")
        filtered_read = session.compile(
            qualified,
            filtered,
            params={parameter: 2},
            purpose="source_profile.parameter",
            expected_schema=filtered.schema().to_pyarrow(),
        )
        filtered_result = session.execute(filtered_read)
        assert filtered_result.to_dict(orient="records") == [{"tenant": "a", "id": 11}]
        assert session.submissions[-1].sql == filtered_read.sql

        empty = expression.filter(expression.id < 0)
        empty_read = session.compile(
            qualified,
            empty,
            purpose="source_profile.empty",
            expected_schema=empty.schema().to_pyarrow(),
        )
        empty_frame = session.execute(empty_read)
        assert empty_frame.empty
        assert tuple(empty_frame.columns) == tuple(empty_read.schema.names)
        session.close()
        assert all(item.connection_disconnected for item in session.submissions)
        evidence: dict[str, object] = {
            "backend": backend,
            "profile": profile,
            "environment": case.environment,
            "oracle": ROWS,
            "actual": actual,
            "metadata": metadata,
            "result_rows": len(result),
            "executed_sql": [item.sql for item in session.submissions],
            "submissions": [asdict(item) for item in session.submissions],
            "provider_statements": [
                asdict(item) for item in provider_statement_log(session._backend)
            ],
        }
    receipt(f"profile-{backend}-{profile}", evidence)
