"""Keep embedded sources in the default test selection."""

from tests.datasource.source_cases import source_params


def test_source_params_marks_only_external_services_for_runtime() -> None:
    cases = source_params(("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"))

    assert [tuple(mark.name for mark in case.marks) for case in cases] == [
        (),
        (),
        ("runtime",),
        ("runtime",),
        ("runtime",),
        ("runtime",),
    ]
