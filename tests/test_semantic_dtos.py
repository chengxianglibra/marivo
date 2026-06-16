from __future__ import annotations

from typing import get_args, get_type_hints

import pytest

import marivo.semantic as ms
from marivo.datasource.ir import CsvSourceIR, ParquetSourceIR
from marivo.semantic.dtos import (
    AssessmentIssue,
    AuthoringAssessment,
    AuthoringQuestion,
    AuthoringSourceInput,
    TableSource,
    derive_status,
)
from marivo.semantic.ir import (
    TableSourceIR,
)


def test_table_source_round_trips_through_ir():
    src = TableSource(table="orders", database="sales_mart")
    ir = src.to_ir()
    assert isinstance(ir, TableSourceIR)
    assert ir.table == "orders"
    assert ir.database == "sales_mart"


def test_file_source_round_trips_through_ir():
    src = ParquetSourceIR(path="/data/orders.parquet")
    ir = src.to_ir()
    assert isinstance(ir, ParquetSourceIR)
    assert ir.path == "/data/orders.parquet"


def test_table_source_is_shared_datasource_ir_type():
    from marivo.datasource.ir import TableSourceIR
    from marivo.semantic.dtos import TableSource

    source = TableSource(table="orders", database="warehouse")

    assert isinstance(source, TableSourceIR)
    assert source.to_ir() is source
    assert source.to_dict() == {
        "kind": "table",
        "table": "orders",
        "database": "warehouse",
    }


def test_parquet_source_is_shared_datasource_ir_type():
    from marivo.datasource.ir import ParquetSourceIR

    source = ParquetSourceIR(path="orders.parquet")

    assert isinstance(source, ParquetSourceIR)
    assert source.to_ir() is source
    assert source.to_dict() == {
        "kind": "parquet",
        "path": "orders.parquet",
        "hive_partitioning": False,
        "columns": None,
    }


def test_csv_source_is_shared_datasource_ir_type():
    from marivo.datasource.ir import CsvSourceIR

    source = CsvSourceIR(path="orders.csv", delimiter="|")

    assert isinstance(source, CsvSourceIR)
    assert source.to_ir() is source
    assert source.to_dict() == {
        "kind": "csv",
        "path": "orders.csv",
        "header": True,
        "delimiter": "|",
        "columns": None,
    }


def test_file_source_parquet_dict_round_trips_through_semantic_ir_parser():
    from marivo.semantic.ir import source_from_dict

    src = ParquetSourceIR(path="/data/orders.parquet", hive_partitioning=True)

    restored = source_from_dict(src.to_dict())

    assert isinstance(restored, ParquetSourceIR)
    assert restored.path == "/data/orders.parquet"
    assert restored.hive_partitioning is True


def test_file_source_csv_dict_round_trips_through_semantic_ir_parser():
    from marivo.semantic.ir import source_from_dict

    src = CsvSourceIR(path="/data/orders.csv", delimiter="\t")

    restored = source_from_dict(src.to_dict())

    assert isinstance(restored, CsvSourceIR)
    assert restored.path == "/data/orders.csv"
    assert restored.delimiter == "\t"


def test_dataset_source_cannot_mix_table_and_file_fields():
    with pytest.raises(TypeError):
        TableSource(table="orders", path="/data/orders.parquet")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        ParquetSourceIR(path="/data/orders.parquet", table="orders")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        ParquetSourceIR(path="/data/orders.parquet", database="sales_mart")  # type: ignore[call-arg]


def test_table_source_to_dict_is_json_safe():
    src = TableSource(table="orders", database=("a", "b"))
    assert src.to_dict() == {
        "kind": "table",
        "table": "orders",
        "database": ["a", "b"],
    }


def test_file_source_to_dict_is_json_safe():
    src = ParquetSourceIR(path="/data/orders.parquet", hive_partitioning=True)
    assert src.to_dict() == {
        "kind": "parquet",
        "path": "/data/orders.parquet",
        "hive_partitioning": True,
        "columns": None,
    }


def test_authoring_source_input_to_dict_is_json_safe():
    src = AuthoringSourceInput(
        role="from",
        datasource="warehouse",
        source=TableSource(table="orders", database="sales_mart"),
        columns=("customer_id",),
    )
    assert src.to_dict() == {
        "role": "from",
        "datasource": "warehouse",
        "source": {"kind": "table", "table": "orders", "database": "sales_mart"},
        "columns": ["customer_id"],
    }


def test_authoring_source_role_is_finite_public_vocabulary():
    from marivo.semantic.dtos import AuthoringSourceRole

    assert get_args(AuthoringSourceRole) == ("primary", "from", "to", "component")
    assert get_type_hints(AuthoringSourceInput)["role"] == AuthoringSourceRole


def test_derive_status_blocked_on_blocker_issue():
    issue = AssessmentIssue(
        kind="missing_column",
        severity="blocker",
        refs=("sales.revenue",),
        message="x",
        rule_id="r1",
    )
    assert derive_status((issue,), ()) == "blocked"


def test_derive_status_blocked_on_blocking_question():
    q = AuthoringQuestion(
        id="q1",
        decision_kind="metric_provenance_status",
        subject_refs=("sales.revenue",),
        prompt="p",
        reason="r",
        readiness_effect="blocks",
    )
    assert derive_status((), (q,)) == "blocked"


def test_derive_status_needs_input_then_supported():
    needs = AssessmentIssue(
        kind="missing_evidence",
        severity="warning",
        refs=("sales.revenue",),
        message="x",
        rule_id="r1",
    )
    assert derive_status((needs,), ()) == "needs_input"
    assert derive_status((), ()) == "supported"


def test_authoring_assessment_status_uses_needs_input():
    issue = AssessmentIssue(
        kind="missing_source",
        severity="warning",
        refs=("sales.revenue",),
        message="source context is missing",
        rule_id="source_context_present",
    )
    status = derive_status((issue,), ())
    assessment = AuthoringAssessment(status=status, issues=(issue,), questions=())

    assert status == "needs_input"
    assert assessment.status == "needs_input"


def test_derive_brief_status_returns_sufficient_for_empty():
    from marivo.semantic.dtos import derive_brief_status

    assert derive_brief_status((), ()) == "sufficient"


def test_derive_brief_status_blocked_on_blocker_issue():
    from marivo.semantic.dtos import derive_brief_status

    issue = AssessmentIssue(
        kind="missing_column",
        severity="blocker",
        refs=("sales.revenue",),
        message="x",
        rule_id="r1",
    )
    assert derive_brief_status((issue,), ()) == "blocked"


def test_derive_brief_status_blocked_on_blocking_question():
    from marivo.semantic.dtos import derive_brief_status

    q = AuthoringQuestion(
        id="q1",
        decision_kind="metric_provenance_status",
        subject_refs=("sales.revenue",),
        prompt="p",
        reason="r",
        readiness_effect="blocks",
    )
    assert derive_brief_status((), (q,)) == "blocked"


def test_derive_brief_status_needs_input_on_missing_evidence():
    from marivo.semantic.dtos import derive_brief_status

    needs = AssessmentIssue(
        kind="missing_evidence",
        severity="warning",
        refs=("sales.revenue",),
        message="x",
        rule_id="r1",
    )
    assert derive_brief_status((needs,), ()) == "needs_input"


def test_authoring_assessment_is_frozen():
    assessment = AuthoringAssessment(status="supported", issues=(), questions=())
    with pytest.raises(AttributeError):
        assessment.status = "blocked"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Stepwise authoring: Brief DTO tests
# ---------------------------------------------------------------------------


_BRIEF_NAMES = [
    "DomainBrief",
    "EntityBrief",
    "DimensionBrief",
    "TimeDimensionBrief",
    "MetricBrief",
    "RelationshipBrief",
    "CrossEntityMetricBrief",
    "DerivedMetricBrief",
]


def test_brief_status_replaces_review_status_for_stepwise_authoring() -> None:
    from marivo.semantic.dtos import BriefStatus

    assert set(get_args(BriefStatus)) == {"sufficient", "needs_input", "blocked"}


def test_every_brief_field_has_a_description() -> None:
    import dataclasses

    import marivo.semantic as ms

    missing: list[str] = []
    for name in _BRIEF_NAMES:
        cls = getattr(ms, name)
        for f in dataclasses.fields(cls):
            if not f.metadata.get("description"):
                missing.append(f"{name}.{f.name}")
    assert not missing, f"Brief fields without a description: {missing}"


def test_help_emits_brief_field_descriptions() -> None:
    import dataclasses

    import marivo.semantic as ms
    from marivo.introspection.surface import render
    from marivo.semantic.help import _surface

    for name in _BRIEF_NAMES:
        cls = getattr(ms, name)
        data = render(_surface(), name, "json")
        fields = {f["name"]: f for f in data.get("fields", [])}
        missing = [
            f.name for f in dataclasses.fields(cls) if not fields.get(f.name, {}).get("description")
        ]
        assert not missing, f"Help fields without a description for {name}: {missing}"

    data = render(_surface(), "CrossEntityMetricBrief", "json")
    fields = {f["name"]: f for f in data.get("fields", [])}
    assert fields["entities"]["description"] == "Target entity refs to join from the root entity."


def test_registered_match_is_explainable_not_fuzzy() -> None:
    from marivo.semantic.dtos import RegisteredMatch

    match = RegisteredMatch(ref="sales.orders", basis="same_source")

    assert match.ref == "sales.orders"
    assert match.basis == "same_source"
    # basis is an enum-like literal, not a fuzzy keyword match
    assert "keyword" not in set(get_args(type(match).__annotations__["basis"]))


def test_verify_result_is_public_result_object() -> None:
    from marivo.semantic.dtos import VerifyResult

    result = VerifyResult(
        status="passed",
        ref="sales.orders",
        kind="entity",
        issues=(),
        warnings=(),
        scan=None,
        auto_recorded=(),
    )

    assert repr(result) == "<VerifyResult status=passed ref=sales.orders kind=entity>"
    assert "VerifyResult status=passed" in result.render()
    assert ms.VerifyResult is VerifyResult


# ---------------------------------------------------------------------------
# EntityBrief.render() — rich evidence display
# ---------------------------------------------------------------------------


def _make_entity_brief(**overrides: object) -> ms.EntityBrief:
    from marivo.datasource.metadata import ColumnMetadata, TableMetadata
    from marivo.datasource.scan import ColumnProfile, ScanReport
    from marivo.semantic.dtos import EntityBrief, PrimaryKeyCandidate, VersioningHints

    defaults: dict[str, object] = {
        "status": "sufficient",
        "datasource": "wh",
        "source": TableSource(table="orders", database="sales"),
        "domain": "sales",
        "table": TableMetadata(
            datasource="wh",
            table="orders",
            database="sales",
            backend_type="duckdb",
            comment=None,
            columns=(
                ColumnMetadata(
                    name="order_id", type="int64", nullable=False, comment=None, ordinal_position=1
                ),
                ColumnMetadata(
                    name="amount", type="float64", nullable=True, comment=None, ordinal_position=2
                ),
            ),
            partitions=(),
            warnings=(),
        ),
        "column_profiles": (
            ColumnProfile(
                name="order_id",
                data_type="int64",
                nullable=False,
                comment=None,
                null_count=0,
                empty_count=0,
                distinct_count=5000,
                top_values=(),
                sample_values=(),
                min_value=1,
                max_value=5000,
            ),
            ColumnProfile(
                name="amount",
                data_type="float64",
                nullable=True,
                comment=None,
                null_count=3,
                empty_count=0,
                distinct_count=4800,
                top_values=(),
                sample_values=(),
                min_value=0.01,
                max_value=999.99,
            ),
        ),
        "primary_key_candidates": (
            PrimaryKeyCandidate(columns=("order_id",), sampled_unique=True, distinct_ratio=1.0),
        ),
        "versioning_hints": VersioningHints(
            snapshot_partition="dt",
            cadence_estimate="daily",
            validity_pair=("valid_from", "valid_to"),
        ),
        "time_like_columns": ("created_at",),
        "matches": (),
        "questions": (),
        "issues": (),
        "scan": ScanReport(
            partition_used=None,
            partition_resolution="none",
            rows_scanned=5000,
            columns_scanned=("order_id", "amount"),
            truncated=False,
            elapsed_seconds=0.1,
            warnings=(),
        ),
    }
    defaults.update(overrides)
    return EntityBrief(**defaults)  # type: ignore[arg-type]


def test_entity_brief_render_includes_column_profiles() -> None:
    brief = _make_entity_brief()
    rendered = brief.render()
    assert "order_id" in rendered
    assert "int64" in rendered
    assert "5000" in rendered
    assert "column" in rendered
    assert "type" in rendered
    assert "distinct" in rendered
    assert "nulls" in rendered


def test_entity_brief_render_includes_pk_candidates() -> None:
    brief = _make_entity_brief()
    rendered = brief.render()
    assert "pk_candidates=" in rendered
    assert "order_id" in rendered
    assert "distinct=1.00" in rendered


def test_entity_brief_render_includes_time_like_columns() -> None:
    brief = _make_entity_brief()
    rendered = brief.render()
    assert "time_like=" in rendered
    assert "created_at" in rendered


def test_entity_brief_render_includes_versioning_hints() -> None:
    brief = _make_entity_brief()
    rendered = brief.render()
    assert "snapshot=dt" in rendered
    assert "cadence=daily" in rendered
    assert "validity=valid_from/valid_to" in rendered


def test_entity_brief_render_omits_sparse_sections() -> None:
    from marivo.semantic.dtos import VersioningHints

    brief = _make_entity_brief(
        column_profiles=(),
        primary_key_candidates=(),
        versioning_hints=VersioningHints(
            snapshot_partition=None, cadence_estimate=None, validity_pair=None
        ),
        time_like_columns=(),
    )
    rendered = brief.render()
    assert "pk_candidates=" not in rendered
    assert "time_like=" not in rendered
    assert "snapshot=" not in rendered
    assert "cadence=" not in rendered
    assert "validity=" not in rendered


def test_entity_brief_render_truncation_hint_when_many_columns() -> None:
    from marivo.datasource.scan import ColumnProfile

    many_profiles = tuple(
        ColumnProfile(
            name=f"col_{i}",
            data_type="varchar",
            nullable=True,
            comment=None,
            null_count=0,
            empty_count=0,
            distinct_count=100,
            top_values=(),
            sample_values=(),
            min_value=None,
            max_value=None,
        )
        for i in range(10)
    )
    brief = _make_entity_brief(column_profiles=many_profiles)
    rendered = brief.render()
    assert "inspect .column_profiles for all columns" in rendered


def test_entity_brief_satisfies_agent_result_protocol() -> None:
    from marivo.render import AgentResult

    brief = _make_entity_brief()
    assert isinstance(brief, AgentResult)
