"""Focused regression tests for datasource live help rendering."""

from __future__ import annotations

import inspect

import pytest

import marivo.datasource as md
from marivo._help.model import MarivoHelpTargetError
from marivo.datasource.errors import DatasourceHelpTargetError
from marivo.introspection.live.model import SURFACE_LIMITS
from tests.shared_fixtures import rendered_help

_DATASOURCE_IMPORT = "import marivo.datasource as md"
_SEMANTIC_IMPORT = "import marivo.semantic as ms"


def _text(target: object | None = None) -> str:
    return rendered_help(target, owner="datasource")


def test_datasource_root_help_lists_live_capabilities_and_bounded_effects() -> None:
    text = _text()

    for target in (
        "inspect",
        "SourceInspection.sample",
        "raw_sql",
        "partition",
        "time_range",
        "unpruned",
    ):
        assert target in text
    assert "-> SourceInspection" in text
    assert "effects: live_metadata_read, opens_connection" in text
    assert "effects: none" not in text
    assert "no mutation" not in text
    assert "no extra guards" not in text
    assert "Consumed types:" not in text
    assert "Errors:" not in text
    assert _DATASOURCE_IMPORT in text
    assert _SEMANTIC_IMPORT not in text
    assert text.count("\n") + 1 <= SURFACE_LIMITS.root_help_max_lines
    assert len(text) <= SURFACE_LIMITS.root_help_max_codepoints


@pytest.mark.parametrize(
    ("target", "needles"),
    [
        ("inspect", ("Entrypoint: md.inspect", "Signature:", "Output family: SourceInspection")),
        ("SourceInspection.sample", ("Preconditions:", "Effects:", "Example:")),
        (
            "raw_sql",
            (
                "potentially_unbounded_read",
                "requires_positive_row_guard",
                "governed read-only SQL exploration",
                "cannot enter typed analysis",
                "check is_truncated before drawing conclusions",
            ),
        ),
        (
            "source_param",
            ("Entrypoint: md.source_param", "Signature:", "Output family: SourceParameter"),
        ),
        (
            "source_column",
            (
                "Entrypoint: md.source_column",
                "identifier-only",
                "asserts schema without casting",
                "arbitrary SQL remains terminal through md.raw_sql",
                "inspection is metadata-only",
                "bounded runtime evidence",
            ),
        ),
        (
            "table",
            (
                "columns: 'Mapping[str, TableColumnBindingIR] | None'",
                'catalog_source = md.table("orders")',
                'md.source_column("event.timestamp"',
                "complete identifier-only bindings",
            ),
        ),
        (
            "json",
            (
                "query_params",
                "records_path",
                "field_paths",
                "method",
                "body",
                "md.source_param",
                "json_request_shape",
                "stable output aliases",
                "one shared array traversal",
                'field_paths={"app_name": "apps[].name"}',
            ),
        ),
        (
            "duckdb",
            ("http_scope", "http_bearer_token_env", "duckdb_http_auth_scoped"),
        ),
        (
            "trino",
            (
                "user_env",
                "auth_env",
                "resolve only from explicit *_env references",
                "ambient MARIVO_* names are ignored",
            ),
        ),
        (
            "register",
            (
                "Entrypoint: md.register",
                "resolve only from explicit *_env references",
                "ambient MARIVO_* names are ignored",
            ),
        ),
        (
            "SourceInspection.sample",
            ("source_params", "SourceParameters optional", "json_source_params_exact"),
        ),
        (
            "time_range",
            (
                "Entrypoint: md.time_range",
                "half-open",
                "start",
                "end",
                "Output family: PartitionScope",
            ),
        ),
    ],
)
def test_focused_help_renders_live_contract(target: str, needles: tuple[str, ...]) -> None:
    text = _text(target)
    for needle in needles:
        assert needle in text
    assert _DATASOURCE_IMPORT in text
    assert (_SEMANTIC_IMPORT in text) == (
        target in {"inspect", "raw_sql", "SourceInspection.sample"}
    )
    assert text.count("\n") + 1 <= SURFACE_LIMITS.focused_help_max_lines
    assert len(text) <= SURFACE_LIMITS.focused_help_max_codepoints


def test_inspection_help_teaches_result_reads_from_an_assigned_value() -> None:
    inspect_text = _text("inspect")
    partitions_text = _text("SourceInspection.partitions")
    sample_text = _text("SourceInspection.sample")

    assert "inspection = md.inspect(" in inspect_text
    assert "inspection.show()" in inspect_text
    assert 'inspection.partitions(limit=1, order="asc").show()' in partitions_text
    assert 'inspection.partitions(limit=100, order="desc").show()' in partitions_text
    assert "# ascending edge" in partitions_text
    assert "# descending edge" in partitions_text
    assert "# earliest" not in partitions_text
    assert "# latest" not in partitions_text
    assert "partition_listing_bounded" in partitions_text
    assert "inspection = md.inspect(" in sample_text
    assert "snapshot = inspection.sample(" in sample_text
    assert "snapshot.show()" in sample_text
    assert "snapshot.contract().show()" not in sample_text
    assert "snapshot.dimensions" not in sample_text
    assert "persist_values=True" in sample_text
    assert "snapshot_value_persistence" in sample_text
    assert "later process" in sample_text
    assert 'source_params={"apps": ["app-1", "app-2"]}' in sample_text


def test_connection_test_help_teaches_result_read() -> None:
    text = _text("test")

    assert "result = md.test(" in text
    assert "result.show()" in text
    assert "result.contract().show()" not in text


def test_authoring_routes_direct_exploration_without_lifecycle_states() -> None:
    text = _text("authoring")

    assert "datasource.declared" not in text
    assert "evidence.projected" not in text
    assert 'declare -> marivo.help("datasource.duckdb")' in text
    assert 'register and test -> marivo.help("datasource.register")' in text
    assert 'metadata -> marivo.help("datasource.inspect")' in text
    assert 'explicit scope -> marivo.help("datasource.partition")' in text
    assert 'optional bounded sampling -> marivo.help("datasource.SourceInspection.sample")' in text
    assert 'governed SQL exploration -> marivo.help("datasource.raw_sql")' in text
    assert "RawSqlResult is terminal data" in text
    assert _DATASOURCE_IMPORT in text
    assert _SEMANTIC_IMPORT not in text
    assert 'marivo.help("semantic.authoring")' in text
    assert "1." not in text


def test_consumed_type_help_uses_only_registered_public_contract() -> None:
    text = _text(md.SourceInspection)

    assert "Producers: inspect" in text
    assert "Public fields:" in text
    assert "Public consumption:" in text
    assert _DATASOURCE_IMPORT in text
    assert _SEMANTIC_IMPORT not in text
    assert "Signature:" not in text
    assert "_" not in "\n".join(line for line in text.splitlines() if line.strip().startswith("_"))


@pytest.mark.parametrize("target", [md.TrinoSpec, md.PartitionScope, md.UnprunedScope])
def test_result_type_help_exposes_read_surface_without_lifecycle_contract(target: type) -> None:
    text = _text(target)

    assert "Public consumption: show, render" in text
    assert "Detail: call .show() for bounded readable state." in text
    assert ".contract()" not in text


def test_datasource_failure_type_help_is_registry_owned() -> None:
    text = _text(md.DatasourceFailure)

    assert "Producers: test, DatasourceCatalog.test" in text
    assert "code" in text
    assert "backend_code" in text
    assert "backend_name" in text
    assert "message" in text


def test_help_accepts_registered_receiver_path_and_rejects_private_names() -> None:
    for target in ("snapshot.entity", "ai_context", "datasource_name_global", "_surface"):
        with pytest.raises(MarivoHelpTargetError):
            _text(target)


def test_help_keeps_public_callable_signatures_authoritative() -> None:
    for callable_target in (
        md.duckdb,
        md.source_column,
        md.table,
        md.partition,
        md.time_range,
        md.SourceInspection.sample,
    ):
        assert str(inspect.signature(callable_target)) in _text(callable_target)


def test_error_help_includes_only_the_datasource_import() -> None:
    text = _text(DatasourceHelpTargetError)

    assert _DATASOURCE_IMPORT in text
    assert _SEMANTIC_IMPORT not in text


def test_all_focused_help_defines_every_alias_it_uses() -> None:
    from marivo.datasource._capabilities.registry import REGISTRY

    for target in REGISTRY.canonical_ids():
        text = _text(target)
        assert _DATASOURCE_IMPORT in text
        assert (_SEMANTIC_IMPORT in text) == ("ms." in text), target
