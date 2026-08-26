"""Replay-stable finding, digest-item, and fingerprint identities."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from marivo.analysis.evidence.identity import (
    canonical_json,
    canonical_subject_key,
    make_artifact_id,
    make_digest_fingerprint,
    make_digest_item_id,
    make_finding_id,
    make_typed_item_key,
)
from marivo.analysis.evidence.types import Subject
from marivo.semantic.metric_graph import RuntimeExpressionSubjectV1
from tests.shared_fixtures import make_test_subject


def test_canonical_json_and_subject_key_are_order_stable() -> None:
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    first = make_test_subject(
        metric_id="revenue", slice_by={"region": "us"}, analysis_axis="change"
    )
    second = make_test_subject(
        metric_id="revenue", slice_by={"region": "us"}, analysis_axis="change"
    )
    assert canonical_subject_key(first) == canonical_subject_key(second)
    assert len(canonical_subject_key(first)) == 32


def test_artifact_finding_and_item_ids_are_deterministic() -> None:
    artifact = make_artifact_id("compare", ["b", "a"], {"alignment": "window"}, {})
    assert artifact == make_artifact_id("compare", ["a", "b"], {"alignment": "window"}, {})
    finding = make_finding_id(artifact, "delta", "value")
    assert finding == make_finding_id(artifact, "delta", "value")
    item = make_digest_item_id(
        artifact_ref=artifact,
        item_kind="change",
        source_finding_refs=(finding,),
    )
    assert item.startswith("itm_")


def test_typed_item_keys_preserve_scalar_types_and_structure() -> None:
    values = (None, "", 1, "1", 1.0, "1.0", True, "True")
    keys = {make_typed_item_key(namespace="row", coordinates={"value": value}) for value in values}
    assert len(keys) == len(values)

    first = make_typed_item_key(
        namespace="row",
        coordinates={"left": "a|right=b=c", "right": "d"},
    )
    second = make_typed_item_key(
        namespace="row",
        coordinates={"left": "a", "right": "b=c|right=d"},
    )
    assert first != second
    assert first == make_typed_item_key(
        namespace="row",
        coordinates={"right": "d", "left": "a|right=b=c"},
    )


def test_typed_item_keys_preserve_temporal_types_before_json_projection() -> None:
    timestamp = pd.Timestamp("2026-08-01T00:00:00")
    timestamp_text = timestamp.isoformat()
    day = date(2026, 8, 1)

    assert make_typed_item_key(
        namespace="row", coordinates={"value": timestamp}
    ) != make_typed_item_key(namespace="row", coordinates={"value": timestamp_text})
    assert make_typed_item_key(namespace="row", coordinates={"value": day}) != (
        make_typed_item_key(namespace="row", coordinates={"value": day.isoformat()})
    )
    assert make_typed_item_key(namespace="row", coordinates={"value": pd.NA}) == (
        make_typed_item_key(namespace="row", coordinates={"value": None})
    )
    numpy_timestamp = np.datetime64("2026-08-01T00:00:00.000000000")
    assert make_typed_item_key(
        namespace="row", coordinates={"value": numpy_timestamp}
    ) == make_typed_item_key(namespace="row", coordinates={"value": timestamp})
    assert make_typed_item_key(
        namespace="row", coordinates={"value": numpy_timestamp}
    ) != make_typed_item_key(
        namespace="row",
        coordinates={"value": numpy_timestamp.astype("int64").item()},
    )


def test_runtime_subject_key_never_merges_across_sessions() -> None:
    def subject(session_id: str) -> Subject:
        return Subject(
            typed_metric_subject=RuntimeExpressionSubjectV1(
                kind="runtime_expression",
                session_id=session_id,
                expression_fingerprint="expr_same",
                artifact_id="art_same",
                scope_fingerprint="scope_same",
            ),
            analysis_axis="scalar",
        )

    assert canonical_subject_key(subject("sess_a")) != canonical_subject_key(subject("sess_b"))


def test_digest_fingerprint_excludes_its_self_field() -> None:
    first = make_digest_fingerprint({"artifact_ref": "art_x", "fingerprint": "first"})
    second = make_digest_fingerprint({"artifact_ref": "art_x", "fingerprint": "second"})
    assert first == second
