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

    source = CsvSourceIR(
        path="orders.csv",
        schema=(("order_id", "string"), ("amount", "decimal(18,2)")),
        delimiter="|",
    )

    assert isinstance(source, CsvSourceIR)
    assert source.to_ir() is source
    assert source.to_dict() == {
        "kind": "csv",
        "path": "orders.csv",
        "schema": {"order_id": "string", "amount": "decimal(18,2)"},
        "header": True,
        "delimiter": "|",
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

    src = CsvSourceIR(
        path="/data/orders.csv",
        schema=(("order_id", "string"), ("amount", "decimal(18,2)")),
        delimiter="\t",
    )

    restored = source_from_dict(src.to_dict())

    assert isinstance(restored, CsvSourceIR)
    assert restored.path == "/data/orders.csv"
    assert restored.schema == (("order_id", "string"), ("amount", "decimal(18,2)"))
    assert restored.delimiter == "\t"


def test_file_source_json_dict_round_trips_through_semantic_ir_parser():
    from marivo.datasource.ir import JsonSourceIR
    from marivo.semantic.ir import source_from_dict

    src = JsonSourceIR(
        path="/data/events.json",
        schema=(("event_id", "string"), ("occurred_at", "timestamp")),
        format="newline_delimited",
    )

    restored = source_from_dict(src.to_dict())

    assert isinstance(restored, JsonSourceIR)
    assert restored.path == "/data/events.json"
    assert restored.schema == (("event_id", "string"), ("occurred_at", "timestamp"))
    assert restored.format == "newline_delimited"


def test_source_from_dict_rejects_non_string_schema_entries() -> None:
    from marivo.semantic.ir import source_from_dict

    with pytest.raises(TypeError, match="schema"):
        source_from_dict({"kind": "csv", "path": "orders.csv", "schema": {"order_id": 123}})
    with pytest.raises(TypeError, match="schema"):
        source_from_dict({"kind": "json", "path": "events.json", "schema": {123: "string"}})


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


def test_authoring_assessment_is_frozen():
    assessment = AuthoringAssessment(status="supported", issues=(), questions=())
    with pytest.raises(AttributeError):
        assessment.status = "blocked"  # type: ignore[misc]


def test_verify_result_is_public_result_object() -> None:
    from marivo.semantic.dtos import VerifyResult

    result = VerifyResult(
        status="passed",
        ref="sales.orders",
        kind="entity",
        validation_level="static",
        runtime_checked=False,
        issues=(),
        warnings=(),
    )

    assert (
        repr(result)
        == "<VerifyResult status=passed ref=sales.orders kind=entity; call .show() to inspect>"
    )
    rendered = result.render()
    assert rendered == "\n".join(
        [
            "VerifyResult status=passed ref=sales.orders kind=entity",
            "status: passed",
            "validation_level: static",
            "runtime_checked: false",
            "Next step:",
            "- continue the batch or run ms.readiness(refs=...)",
            "available:",
            "- .issues",
            "- .warnings",
        ]
    )
    assert ms.VerifyResult is VerifyResult


def test_verify_result_render_shows_issue_details() -> None:
    from marivo.semantic.dtos import AssessmentIssue, VerifyResult

    issue = AssessmentIssue(
        kind="project_load_failed",
        severity="blocker",
        refs=("trino_query",),
        message="Cannot verify 'trino_query': project failed to load.",
        rule_id="verify_object_project_load_failed",
    )
    result = VerifyResult(
        status="failed",
        ref="trino_query",
        kind="entity",
        validation_level="static",
        runtime_checked=False,
        issues=(issue,),
        warnings=(),
    )

    rendered = result.render()
    assert rendered == "\n".join(
        [
            "VerifyResult status=failed ref=trino_query kind=entity",
            "status: failed, 1 issue",
            "validation_level: static",
            "runtime_checked: false",
            "issues:",
            "- [blocker] project_load_failed: Cannot verify 'trino_query': project failed to load.",
            "Next step:",
            "- repair this object, then re-run ms.verify_object(ref)",
            "available:",
            "- .issues",
            "- .warnings",
        ]
    )


def test_verify_result_render_shows_warning_details() -> None:
    from marivo.semantic.dtos import AssessmentIssue, VerifyResult

    warning = AssessmentIssue(
        kind="missing_evidence",
        severity="warning",
        refs=("sales.orders",),
        message="No evidence recorded for this object.",
        rule_id="verify_object_missing_evidence",
    )
    result = VerifyResult(
        status="passed",
        ref="sales.orders",
        kind="entity",
        validation_level="static",
        runtime_checked=False,
        issues=(),
        warnings=(warning,),
    )

    rendered = result.render()
    assert rendered == "\n".join(
        [
            "VerifyResult status=passed ref=sales.orders kind=entity",
            "status: passed, 1 warning",
            "validation_level: static",
            "runtime_checked: false",
            "warnings:",
            "- [warning] missing_evidence: No evidence recorded for this object.",
            "Next step:",
            "- continue the batch or run ms.readiness(refs=...)",
            "available:",
            "- .issues",
            "- .warnings",
        ]
    )


def test_verify_result_render_lists_many_issues_as_omittable_card_section() -> None:
    from marivo.semantic.dtos import AssessmentIssue, VerifyResult

    issues = tuple(
        AssessmentIssue(
            kind="static_check_failed",
            severity="blocker",
            refs=("x",),
            message=f"Issue {i}",
            rule_id=f"rule_{i}",
        )
        for i in range(7)
    )
    result = VerifyResult(
        status="failed",
        ref="x",
        kind="entity",
        validation_level="static",
        runtime_checked=False,
        issues=issues,
        warnings=(),
    )

    rendered = result.render()
    assert "7 issues" in rendered
    assert "Issue 0" in rendered
    assert "Issue 4" in rendered
    assert "Issue 5" in rendered
    assert "Issue 6" in rendered
    assert "more issues" not in rendered


def test_verify_result_passed_render_has_continue_next_step() -> None:
    from marivo.semantic.dtos import VerifyResult

    result = VerifyResult(
        status="passed",
        ref="sales.orders",
        kind="entity",
        validation_level="static",
        runtime_checked=False,
        issues=(),
        warnings=(),
    )
    text = result.render()
    assert "Next step" in text
    assert "ms.readiness(" in text


def test_verify_result_failed_render_has_repair_next_step() -> None:
    from marivo.semantic.dtos import AssessmentIssue, VerifyResult

    issues = (
        AssessmentIssue(
            kind="static_check_failed",
            severity="blocker",
            refs=("trino_query",),
            message="Static check failed.",
            rule_id="verify_object_static_check_failed",
        ),
    )
    result = VerifyResult(
        status="failed",
        ref="sales.orders",
        kind="entity",
        validation_level="static",
        runtime_checked=False,
        issues=issues,
        warnings=(),
    )
    text = result.render()
    assert "Next step" in text
    assert "repair" in text.lower()
    assert "ms.verify_object(" in text
