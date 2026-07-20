"""Pure semantic-shaped projections from bounded authoring snapshots."""

from __future__ import annotations

import inspect
import operator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Literal, get_type_hints

import pandas as pd
import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo._authoring.model import AuthoringStateRef
from marivo.datasource.evidence import (
    TIME_RULE_IDS,
    DimensionColumnEvidence,
    DimensionEvidenceResult,
    DimensionValuesResult,
    EntityColumnEvidence,
    EntityEvidenceResult,
    MeasureColumnEvidence,
    MeasureEvidenceResult,
    RelationshipEvidenceResult,
    TimeColumnEvidence,
    TimeEvidenceResult,
)
from marivo.datasource.metadata import ColumnMetadata
from marivo.datasource.snapshot import (
    ColumnProfile,
    DiscoverySnapshot,
    SnapshotCoverage,
    _profile_column,
)
from marivo.render import AgentResult


def _profile(
    name: str,
    values: list[object],
    *,
    data_type: str = "string",
    scope_exhaustion: Literal["exhaustive", "truncated"] = "truncated",
) -> ColumnProfile:
    return _profile_column(
        pd.DataFrame({name: values}),
        ColumnMetadata(
            name=name,
            type=data_type,
            nullable=True,
            comment=None,
            ordinal_position=1,
        ),
        partition_names=frozenset(),
        scope_exhaustion=scope_exhaustion,
    )


def _snapshot(
    profiles: tuple[ColumnProfile, ...],
    *,
    snapshot_id: str = "snapshot_left",
    datasource: str = "warehouse",
    table: str = "events",
    scope_exhaustion: Literal["exhaustive", "truncated"] = "truncated",
    value_evidence_state: Literal["available", "value_evidence_unavailable"] = "available",
) -> DiscoverySnapshot:
    now = datetime(2026, 7, 11, tzinfo=UTC)
    retained_row_count = profiles[0].sample_row_count if profiles else 0
    return DiscoverySnapshot(
        id=snapshot_id,
        datasource=ms.Ref.datasource(f"{datasource}"),
        source=md.table(table),
        scope=md.unpruned(max_rows=1000, timeout_seconds=30),
        columns=tuple(profile.name for profile in profiles),
        schema_fingerprint=f"schema-{snapshot_id}",
        profiles=profiles,
        coverage=SnapshotCoverage(
            observed_row_count=(
                retained_row_count + 1 if scope_exhaustion == "truncated" else retained_row_count
            ),
            retained_row_count=retained_row_count,
            scope_exhaustion=scope_exhaustion,
            scope_exactness=("sample_only" if scope_exhaustion == "truncated" else "scope_exact"),
            sampling_method="first_rows_limit",
            pushed_predicate=(),
        ),
        persist_values=True,
        value_evidence_state=value_evidence_state,
        cache_status="fresh",
        created_at=now,
        expires_at=now + timedelta(hours=24),
        _project_root=Path("/tmp/project"),
    )


@pytest.fixture
def snapshot() -> DiscoverySnapshot:
    log_dates: list[object] = [20260710] * 999 + [20261340]
    return _snapshot(
        (
            _profile("query_id", list(range(1000)), data_type="int64"),
            _profile(
                "self",
                [f"https://example.test/resources/{index}" for index in range(1000)],
            ),
            _profile("region", [f"region-{index % 15:02d}" for index in range(1000)]),
            _profile("log_date", log_dates, data_type="int64"),
            _profile("log_hour", [f"{index % 24:02d}" for index in range(1000)]),
            _profile(
                "epoch_like",
                [1_720_000_000 + index for index in range(1000)],
                data_type="int64",
            ),
            _profile(
                "amount",
                [float(index - 500) for index in range(1000)],
                data_type="float64",
            ),
        )
    )


def test_snapshot_projection_return_annotations_are_concrete_and_runtime_resolvable() -> None:
    expected = {
        "entity": EntityEvidenceResult,
        "dimensions": DimensionEvidenceResult,
        "values": DimensionValuesResult,
        "time_dimensions": TimeEvidenceResult,
        "measures": MeasureEvidenceResult,
        "relationships": RelationshipEvidenceResult,
    }

    for method_name, result_type in expected.items():
        method = getattr(DiscoverySnapshot, method_name)
        assert get_type_hints(method)["return"] is result_type
        assert inspect.signature(method, eval_str=True).return_annotation is result_type


def test_evidence_dto_annotations_are_concrete_and_runtime_resolvable() -> None:
    assert get_type_hints(EntityColumnEvidence)["profile"] is ColumnProfile
    assert get_type_hints(DimensionColumnEvidence)["profile"] is ColumnProfile
    assert get_type_hints(TimeColumnEvidence)["profile"] is ColumnProfile
    assert get_type_hints(MeasureColumnEvidence)["profile"] is ColumnProfile
    assert get_type_hints(RelationshipEvidenceResult)["left_profile"] == ColumnProfile | None


def test_fixed_time_rules_are_narrow_and_count_every_value(
    snapshot: DiscoverySnapshot,
) -> None:
    result = snapshot.time_dimensions(columns=("log_date", "log_hour", "epoch_like"))
    date_match = next(
        item
        for item in result.evidence_by_column["log_date"].deterministic_matches
        if item.rule == "date.yyyymmdd"
    )
    assert (date_match.checked, date_match.matched, date_match.failed) == (1000, 999, 1)
    hour_match = next(
        item
        for item in result.evidence_by_column["log_hour"].deterministic_matches
        if item.rule == "time.hour_00_23"
    )
    assert hour_match.role == "component_only"
    assert result.evidence_by_column["epoch_like"].deterministic_matches == ()
    assert "epoch_like" in result.render()
    assert TIME_RULE_IDS == (
        "type.native_date",
        "type.native_timestamp",
        "date.iso",
        "datetime.iso",
        "date.yyyymmdd",
        "time.hour_00_23",
    )


def test_time_rules_use_strict_full_values_without_uncommon_or_epoch_inference() -> None:
    result = _snapshot(
        (
            _profile("iso_date", ["2026-07-11", "2026-02-30"]),
            _profile("iso_datetime", ["2026-07-11T12:30:00", "2026/07/11 12:30:00"]),
            _profile("uncommon", ["11-Jul-2026", "12-Jul-2026"]),
            _profile("hours", [0, 23, 24], data_type="int64"),
        )
    ).time_dimensions(columns=("iso_date", "iso_datetime", "uncommon", "hours"))

    iso_date = result.evidence_by_column["iso_date"].deterministic_matches
    assert [(item.rule, item.checked, item.matched, item.failed) for item in iso_date] == [
        ("date.iso", 2, 1, 1)
    ]
    iso_datetime = result.evidence_by_column["iso_datetime"].deterministic_matches
    assert [(item.rule, item.checked, item.matched, item.failed) for item in iso_datetime] == [
        ("datetime.iso", 2, 1, 1)
    ]
    assert result.evidence_by_column["uncommon"].deterministic_matches == ()
    hours = result.evidence_by_column["hours"].deterministic_matches
    assert [(item.rule, item.checked, item.matched, item.failed) for item in hours] == [
        ("time.hour_00_23", 3, 2, 1)
    ]


def test_native_time_rules_stay_inside_the_closed_rule_set() -> None:
    result = _snapshot(
        (
            _profile("native_date", ["2026-07-11"], data_type="date"),
            _profile(
                "native_timestamp",
                ["2026-07-11T12:30:00"],
                data_type="timestamp(6)",
            ),
        )
    ).time_dimensions(columns=("native_date", "native_timestamp"))

    date_rules = result.evidence_by_column["native_date"].deterministic_matches
    timestamp_rules = result.evidence_by_column["native_timestamp"].deterministic_matches
    assert date_rules[0].rule == "type.native_date"
    assert timestamp_rules[0].rule == "type.native_timestamp"
    assert {
        match.rule
        for evidence in result.evidence_by_column.values()
        for match in evidence.deterministic_matches
    } <= set(TIME_RULE_IDS)


def test_entity_evidence_does_not_rank_sampled_unique_columns(
    snapshot: DiscoverySnapshot,
) -> None:
    result = snapshot.entity(columns=("query_id", "self"))
    assert result.evidence_by_column["query_id"].sample_unique is True
    assert result.evidence_by_column["self"].sample_unique is True
    assert result.evidence_by_column["query_id"].name_suffix == "_id"
    assert result.evidence_by_column["self"].url_syntax_matched == 1000
    assert "recommend" not in result.render().lower()
    assert isinstance(result.evidence_by_column, MappingProxyType)
    with pytest.raises(TypeError):
        operator.setitem(
            result.evidence_by_column,
            "other",
            result.evidence_by_column["query_id"],
        )


def test_dimension_and_measure_evidence_expose_column_local_profiles(
    snapshot: DiscoverySnapshot,
) -> None:
    dimensions = snapshot.dimensions(columns=("region", "query_id"))
    assert dimensions.columns == ("region", "query_id")
    assert dimensions.evidence_by_column["region"].profile.sample_distinct_count == 15
    assert dimensions.evidence_by_column["region"].sample_values_complete is False
    assert dimensions.evidence_by_column["region"].scope_values_complete is False

    measures = snapshot.measures(columns=("amount",))
    amount = measures.evidence_by_column["amount"].profile
    assert (amount.min_value, amount.max_value) == (-500.0, 499.0)
    assert (amount.negative_count, amount.zero_count) == (500, 1)
    assert measures.evidence_by_column["amount"].unresolved == (
        "aggregation",
        "unit",
        "additivity",
        "business_definition",
    )


def test_values_separates_dictionary_and_scope_completeness(
    snapshot: DiscoverySnapshot,
) -> None:
    result = snapshot.values("region", limit=10)
    assert result.sample_distinct_count == 15
    assert result.returned_value_count == 10
    assert result.sample_values_complete is False
    assert result.scope_values_complete is False
    assert result.frequency_capacity == 10
    assert result.status == "incomplete"


def test_values_never_represents_unavailable_evidence_as_empty(
    snapshot: DiscoverySnapshot,
) -> None:
    unavailable = replace(
        snapshot,
        value_evidence_state="value_evidence_unavailable",
        profiles=tuple(
            replace(profile, top_values=None, display_samples=None) for profile in snapshot.profiles
        ),
    )

    result = unavailable.values("region", limit=10)

    assert result.value_evidence_state == "value_evidence_unavailable"
    assert result.values is None
    assert result.returned_value_count is None
    assert result.status == "incomplete"
    assert "unavailable" in result.render().lower()
    assert "values: none" not in result.render().lower()

    dimensions = unavailable.dimensions(columns=("region",))
    measures = unavailable.measures(columns=("amount",))
    assert dimensions.status == "incomplete"
    assert measures.status == "incomplete"
    assert "value_evidence_unavailable" in dimensions.issues
    assert "value_evidence_unavailable" in measures.issues
    assert "unavailable" in dimensions.render().lower()
    assert "unavailable" in measures.render().lower()


def test_missing_retained_values_expose_typed_reacquisition_repair(
    snapshot: DiscoverySnapshot,
) -> None:
    unavailable = replace(
        snapshot,
        value_evidence_state="value_evidence_unavailable",
        profiles=tuple(
            replace(profile, top_values=None, display_samples=None) for profile in snapshot.profiles
        ),
    )

    results = (
        unavailable.dimensions(columns=("region",)),
        unavailable.values("region", limit=10),
        unavailable.measures(columns=("amount",)),
    )

    for result in results:
        assert result.repair is not None
        assert result.repair.kind == "reacquire"
        assert result.repair.help_target.canonical_id == "SourceInspection.sample"
        assert result.repair.preserves_evidence is False
        assert result.repair.snippet is not None
        assert "persist_values=True" in result.repair.snippet
        assert "refresh=True" in result.repair.snippet
        assert not hasattr(result, "next_calls")

    multi_column = unavailable.dimensions(columns=("region", "query_id"))
    assert multi_column.repair is not None
    assert 'columns=("region", "query_id")' in multi_column.repair.snippet


def test_complete_evidence_contract_is_terminal_and_binds_snapshot_identity(
    snapshot: DiscoverySnapshot,
) -> None:
    results = (
        snapshot.entity(columns=("query_id",)),
        snapshot.time_dimensions(columns=("log_date",)),
    )

    for result in results:
        assert result.snapshot_id == snapshot.id
        assert result.repair is None
        assert result.contract().states == (
            AuthoringStateRef(
                id="evidence.projected",
                subject_refs=result.columns,
                evidence_ids=(snapshot.id,),
            ),
        )
        assert result.contract().transitions == ()
        assert not hasattr(result, "next_calls")
        rendered = result.render().lower()
        assert ".contract()" in rendered
        assert ".repair" in rendered
        assert "next calls" not in rendered


def test_relationship_projection_is_explicit_one_column_and_cross_scope_unresolved(
    snapshot: DiscoverySnapshot,
) -> None:
    other = _snapshot(
        (_profile("query_id", list(range(500, 1500)), data_type="int64"),),
        snapshot_id="snapshot_right",
        datasource="crm",
        table="customers",
    )

    result = snapshot.relationships(other, left=("query_id",), right=("query_id",))

    assert result.left_snapshot_id == snapshot.id
    assert result.right_snapshot_id == other.id
    assert result.left == ("query_id",)
    assert result.right == ("query_id",)
    assert result.type_compatible is True
    assert result.evidence_state == "unavailable"
    assert result.retained_overlap_count is None
    assert result.scope_comparability == "unresolved"
    assert result.status == "incomplete"


def test_relationship_complete_dictionaries_expose_retained_overlap_counts() -> None:
    left = _snapshot(
        (_profile("customer_id", [1, 2, 3], data_type="int64", scope_exhaustion="exhaustive"),),
        scope_exhaustion="exhaustive",
    )
    right = _snapshot(
        (_profile("customer_id", [2, 3, 4], data_type="int64", scope_exhaustion="exhaustive"),),
        snapshot_id="snapshot_right",
        scope_exhaustion="exhaustive",
    )
    right = replace(
        right,
        scope=md.partition(
            {"log_date": "20260711"},
            max_rows=1000,
            timeout_seconds=30,
        ),
    )

    result = left.relationships(right, left=("customer_id",), right=("customer_id",))
    rendered = result.render()

    assert result.evidence_state == "available"
    assert result.retained_overlap_count == 2
    assert result.retained_left_orphan_count == 1
    assert result.retained_right_orphan_count == 1
    assert "left_scope: UnprunedScope(max_rows=1000, timeout_seconds=30)" in rendered
    assert (
        "right_scope: PartitionScope(values=(('log_date', '20260711'),), "
        "max_rows=1000, timeout_seconds=30)"
    ) in rendered
    assert (
        "retained_counts_scope: retained values within left_scope and right_scope only" in rendered
    )
    assert rendered.index("left_scope:") < rendered.index("retained_overlap_count:")
    assert rendered.index("right_scope:") < rendered.index("retained_overlap_count:")
    assert "scope_comparability: unresolved" in rendered


def test_relationship_multi_column_is_unavailable_without_projection_query(
    snapshot: DiscoverySnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = _snapshot(
        (
            _profile("query_id", list(range(1000)), data_type="int64"),
            _profile("region", [f"region-{index % 15:02d}" for index in range(1000)]),
        ),
        snapshot_id="snapshot_right",
    )
    monkeypatch.setattr(
        "marivo.datasource.snapshot._backends.build_backend",
        lambda *_args, **_kwargs: pytest.fail("projection attempted a query"),
    )

    result = snapshot.relationships(
        other,
        left=("query_id", "region"),
        right=("query_id", "region"),
    )

    assert result.status == "incomplete"
    assert result.evidence_state == "unavailable"
    assert result.type_compatible is None
    assert "multi_column" in result.issues
    assert result.repair is not None
    assert result.repair.kind == "retry"
    assert result.repair.help_target.canonical_id == "DiscoverySnapshot.relationships"
    assert result.repair.preserves_evidence is True
    assert (
        result.repair.snippet
        == 'snapshot.relationships(other, left=("query_id",), right=("query_id",))'
    )
    assert not hasattr(result, "next_calls")


def test_every_projection_is_local_and_validates_requested_columns(
    snapshot: DiscoverySnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "marivo.datasource.snapshot._backends.build_backend",
        lambda *_args, **_kwargs: pytest.fail("projection attempted a query"),
    )
    other = _snapshot(
        (_profile("query_id", list(range(1000)), data_type="int64"),),
        snapshot_id="snapshot_right",
    )

    results: tuple[AgentResult, ...] = (
        snapshot.entity(columns=("query_id",)),
        snapshot.dimensions(columns=("region",)),
        snapshot.values("region", limit=3),
        snapshot.time_dimensions(columns=("log_date",)),
        snapshot.measures(columns=("amount",)),
        snapshot.relationships(other, left=("query_id",), right=("query_id",)),
    )
    assert all(isinstance(result, AgentResult) for result in results)
    assert all("\n" not in repr(result) for result in results)
    assert all(result.render() == result.render() for result in results)
    assert all(not result.render().endswith("\n") for result in results)

    with pytest.raises(ValueError, match="missing"):
        snapshot.entity(columns=("missing",))
    with pytest.raises(ValueError, match="positive integer"):
        snapshot.values("region", limit=0)


def test_projection_cards_contracts_and_help_are_query_free(
    snapshot: DiscoverySnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("projection evidence must not access the datasource")

    monkeypatch.setattr("marivo.datasource.snapshot._backends.build_backend", fail)
    monkeypatch.setattr("marivo.datasource.backends.build_backend", fail)
    monkeypatch.setattr("marivo.datasource.backends.build_backend_with_secrets", fail)
    other = _snapshot(
        (_profile("query_id", [1, 2], data_type="int64", scope_exhaustion="exhaustive"),),
        snapshot_id="snapshot_right",
        scope_exhaustion="exhaustive",
    )

    results = (
        snapshot.entity(columns=("query_id",)),
        snapshot.dimensions(columns=("region",)),
        snapshot.values("region", limit=3),
        snapshot.time_dimensions(columns=("log_date",)),
        snapshot.measures(columns=("amount",)),
        snapshot.relationships(other, left=("query_id",), right=("query_id",)),
    )

    for result in results:
        assert result.contract().transitions == ()
        assert result.show() is None
        assert md.help_text(result)


def test_rendering_is_bounded_without_truncating_structured_mapping() -> None:
    profiles = tuple(
        _profile(f"column_{index:03d}", [index], data_type="int64") for index in range(200)
    )
    result = _snapshot(profiles).entity(columns=tuple(profile.name for profile in profiles))

    rendered = result.render(max_output_bytes=700)

    assert len(result.evidence_by_column) == 200
    assert len(rendered.encode()) <= 700
    assert "output truncated" in rendered
    assert "displayed=" in rendered
    assert "total=200" in rendered
    assert "omitted=" in rendered
