"""Datasource repair and result consistency contracts after lifecycle removal."""

from __future__ import annotations

import pytest

from marivo.datasource._capabilities.contracts import repair_for_authoring_code
from marivo.datasource.errors import repair
from marivo.datasource.manage import DatasourceFailure, DatasourceTestResult


@pytest.mark.parametrize(
    ("ok", "failure", "has_repair"),
    [
        (
            True,
            DatasourceFailure(
                code="connection_open_failed",
                exception_type="RuntimeError",
                backend_code=None,
                backend_name=None,
                message="failed",
            ),
            False,
        ),
        (False, None, True),
        (
            False,
            DatasourceFailure(
                code="connection_open_failed",
                exception_type="RuntimeError",
                backend_code=None,
                backend_name=None,
                message="failed",
            ),
            False,
        ),
    ],
)
def test_connection_test_result_rejects_inconsistent_success_failure_state(
    ok: bool,
    failure: DatasourceFailure | None,
    has_repair: bool,
) -> None:
    typed_repair = (
        repair(
            kind="reconnect",
            canonical_id="datasource_catalog.test",
            action="Reconnect the datasource after fixing its connection settings.",
        )
        if has_repair
        else None
    )

    with pytest.raises(ValueError, match="DatasourceTestResult"):
        DatasourceTestResult(
            name="warehouse",
            ok=ok,
            latency_ms=None,
            failure=failure,
            repair=typed_repair,
        )


def test_authoring_repair_mapping_is_exact() -> None:
    for code, kind, canonical_id, preserves_evidence in [
        ("datasource_missing", "register", "register", False),
        ("source_mismatch", "configure", "inspect", False),
        ("selected_columns_required", "inspect", "inspect", True),
        ("unknown_source_column", "inspect", "inspect", True),
        ("partition_state_unknown", "rescope", "source_inspection.partitions", True),
        ("incomplete_partition_fields", "rescope", "source_inspection.partitions", True),
        ("partition_predicate_unsupported", "rescope", "source_inspection.partitions", True),
        ("transformed_partition_unsupported", "rescope", "time_range", True),
        ("native_timeout_configuration_failed", "configure", "inspect", False),
        ("acquisition_connection_failed", "reconnect", "datasource_catalog.test", True),
        ("acquisition_source_failed", "inspect", "inspect", True),
        ("acquisition_execution_failed", "reacquire", "source_inspection.sample", False),
        ("cache_stale", "reacquire", "source_inspection.sample", False),
        ("schema_stale", "reacquire", "source_inspection.sample", False),
        ("fingerprint_stale", "reacquire", "source_inspection.sample", False),
    ]:
        result = repair_for_authoring_code(code)

        assert result.kind == kind
        assert result.help_target.canonical_id == canonical_id
        assert result.preserves_evidence is preserves_evidence


def test_transformed_partition_repair_is_conditional_on_supported_time_range() -> None:
    result = repair_for_authoring_code("transformed_partition_unsupported")

    assert "date or timestamp output column" in result.action
    assert "predicate pushdown" in result.action
    assert "md.time_range" in result.action
    assert "otherwise" in result.action


def test_execution_failure_repair_has_deterministic_stop_boundary() -> None:
    result = repair_for_authoring_code("acquisition_execution_failed")

    assert "at most once" in result.action
    assert "remaining data-access budget permits" in result.action
    assert "Caller-provided read, row, and timeout limits take precedence" in result.action
    assert "same structured code and backend name" in result.action
    assert "stop and report" in result.action
