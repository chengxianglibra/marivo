from datetime import date

import pytest

from marivo._temporal import TimeScope, _new_time_scope
from marivo.analysis import time_scope
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.refs import ref
from marivo.semantic.catalog import SemanticKind
from tests.lazy_observation_fixtures import make_sources
from tests.ref_helpers import make_ref


def test_normalize_timescope_input_accepts_concrete_instances():
    scope = time_scope(start="2026-05-01", end="2026-05-24")
    result = make_sources().observe(
        make_ref("sales.revenue", SemanticKind.METRIC), time_scope=scope
    )
    assert result is not None


def test_timescope_strings_are_normalized_for_structural_identity():
    from marivo.analysis import time_scope as public_time_scope

    string_scope = public_time_scope(start="2026-05-01", end="2026-05-24")
    typed_scope = public_time_scope(start=date(2026, 5, 1), end=date(2026, 5, 24))

    assert type(string_scope.start) is date
    assert type(string_scope.end) is date
    assert string_scope == typed_scope
    assert hash(string_scope) == hash(typed_scope)
    assert string_scope.model_dump() == {
        "start": date(2026, 5, 1),
        "end": date(2026, 5, 24),
    }


def test_timescope_direct_constructor_is_not_public():
    with pytest.raises(TypeError, match="direct construction is not supported"):
        TimeScope(start="2026-05-01", end="2026-05-24")


@pytest.mark.parametrize(
    "payload",
    [
        {"start": "2026-05-01", "end": "2026-05-24"},
        {
            "start": "2026-05-01",
            "end": "2026-05-24",
            "temporal_set": ref.temporal_set("sales.events"),
            "snapshot_digest": "sha256:exact",
            "boundary_timezone": "UTC",
            "key": "launch",
        },
    ],
)
def test_timescope_model_validate_does_not_open_public_constructor(payload):
    with pytest.raises(TypeError, match="direct construction is not supported"):
        TimeScope.model_validate(payload)


def test_private_timescope_validation_still_supports_recovery_data():
    scope = _new_time_scope(start="2026-05-01", end="2026-05-24")
    assert TimeScope.model_validate(scope) is scope


def test_normalize_timescope_input_rejects_strings():
    with pytest.raises(DatasetConstructionError) as exc_info:
        make_sources().observe(
            make_ref("sales.revenue", SemanticKind.METRIC), time_scope="last 7 days"
        )
    assert "TimeScope" in str(exc_info.value)


def test_normalize_timescope_input_rejects_start_end_dict():
    with pytest.raises(DatasetConstructionError) as exc_info:
        make_sources().observe(
            make_ref("sales.revenue", SemanticKind.METRIC),
            time_scope={"start": "2026-05-01", "end": "2026-05-24"},
        )
    assert "TimeScope" in str(exc_info.value)


@pytest.mark.parametrize(
    "raw",
    [
        {"expr": "mtd"},
        {"start": "2026-05-01", "end": "2026-05-24", "grain": "day"},
        {"start": "2026-05-01", "end": "2026-05-24", "time_dimension": "created_at"},
        {"start": "2026-05-01", "end": "2026-05-24", "extra": "nope"},
    ],
)
def test_normalize_timescope_input_rejects_expr_and_non_scope_keys(raw):
    with pytest.raises(DatasetConstructionError) as exc_info:
        make_sources().observe(make_ref("sales.revenue", SemanticKind.METRIC), time_scope=raw)
    assert "TimeScope" in str(exc_info.value)


def test_normalize_timescope_input_rejects_invalid_type():
    with pytest.raises(DatasetConstructionError) as exc_info:
        make_sources().observe(make_ref("sales.revenue", SemanticKind.METRIC), time_scope=123)
    assert "TimeScope" in str(exc_info.value)


def test_normalize_timescope_input_rejects_invalid_model():
    with pytest.raises(DatasetConstructionError) as exc_info:
        make_sources().observe(
            make_ref("sales.revenue", SemanticKind.METRIC), time_scope={"start": "2026-05-01"}
        )
    assert "TimeScope" in str(exc_info.value)
