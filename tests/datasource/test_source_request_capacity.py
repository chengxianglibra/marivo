"""Native request limits use active connection facts and actual SQL encoding."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from marivo.datasource.adapters import PhysicalRequirement
from marivo.datasource.engines.clickhouse import validate_query_size
from marivo.datasource.errors import DatasourceSourceCapabilityError
from tests.datasource.source_cases import source_case, source_params


def test_clickhouse_capacity_counts_utf8_format_and_client_override() -> None:
    from ibis.backends.clickhouse import Backend

    backend = Backend()
    sql = "SELECT '雪😀'"
    size = len((sql + "\n FORMAT Native").encode())
    settings = {"max_query_size": SimpleNamespace(value=str(size))}
    client_settings: dict[str, str] = {}
    backend.con = SimpleNamespace(server_settings=settings, get_client_setting=client_settings.get)
    validate_query_size(backend, sql)
    client_settings["max_query_size"] = str(size - 1)
    with pytest.raises(DatasourceSourceCapabilityError) as failure:
        validate_query_size(backend, sql)
    assert failure.value.received == f"{size} query bytes including FORMAT Native"
    client_settings["max_query_size"] = "0"
    validate_query_size(backend, sql)
    client_settings.clear()
    settings.clear()
    # An unavailable fact retains native enforcement, not a fabricated default.
    validate_query_size(backend, sql)


@pytest.mark.parametrize("backend,profile", source_params([("clickhouse", "mergetree")]))
def test_clickhouse_rejects_oversized_sql_before_issuing_or_submitting(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with source_case(backend, profile, tmp_path, monkeypatch) as case:
        bound = case.session.bind(case.source, source_identity="capacity")
        qualified = case.session.qualify(
            bound, PhysicalRequirement("query.source_read", 1, frozenset({"scan", "group"}))
        )
        expression = bound.relation.aggregate(total=bound.relation.amount.sum())
        read = case.session.compile(
            qualified,
            expression,
            purpose="query.capacity",
            expected_schema=expression.schema().to_pyarrow(),
        )
        assert case.session.execute(read)["total"].tolist() == [6]
        client = case.session._backend.con
        assert int(client.server_settings["max_query_size"].value) > 0
        submitted = len(case.session.submissions)
        issued = len(case.session._issued)
        with monkeypatch.context() as patch:
            patch.setitem(client.params, "max_query_size", "1")
            with pytest.raises(DatasourceSourceCapabilityError, match="max_query_size=1"):
                case.session.compile(
                    qualified,
                    expression,
                    purpose="query.capacity",
                    expected_schema=expression.schema().to_pyarrow(),
                )
        assert len(case.session._issued) == issued
        assert len(case.session.submissions) == submitted
