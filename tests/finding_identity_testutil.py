"""Shared replay / idempotency assertion utilities for finding identity tests.

Phase 4b-3: provided as a common import for all 4d-* extractor test suites so
they do not each re-implement the same stability checks.

Usage example::

    from tests.finding_identity_testutil import (
        assert_finding_id_stable,
        assert_stable_key_beats_index,
        assert_projection_order_excluded,
    )

    class TestMyExtractor(unittest.TestCase):
        def test_replay_stability(self):
            assert_finding_id_stable(
                self, "art_001", "observation", "value", key=None, index=None
            )
"""

from __future__ import annotations

import unittest

from marivo.evidence_engine.canonical_finding import (
    ArtifactItemRefCollection,
    make_artifact_item_ref,
    make_canonical_item_key,
    make_finding_id,
    make_item_identity,
)


def assert_finding_id_stable(
    tc: unittest.TestCase,
    artifact_id: str,
    finding_type: str,
    collection: ArtifactItemRefCollection,
    *,
    key: str | None = None,
    index: int | None = None,
) -> None:
    """Assert replay/idempotency: two calls with the same inputs produce identical outputs.

    Exercises the full ``make_item_identity`` co-generation path (the mandated
    extractor call site) rather than the individual string helper alone, so this
    function validates that both halves of the identity are stable together:

    - ``canonical_item_key`` string (used as input to ``make_finding_id``)
    - ``ArtifactItemRef`` struct (stored in ``FindingProvenance.artifact_item_ref``)
    - ``finding_id`` derived from the key
    """
    cik1, ref1 = make_item_identity(collection, key=key, index=index)
    cik2, ref2 = make_item_identity(collection, key=key, index=index)
    tc.assertEqual(
        cik1,
        cik2,
        f"canonical_item_key must be stable for ({collection!r}, key={key!r}, index={index!r}): "
        f"got {cik1!r} then {cik2!r}",
    )
    tc.assertEqual(
        ref1,
        ref2,
        f"artifact_item_ref must be stable for ({collection!r}, key={key!r}, index={index!r}): "
        f"got {ref1!r} then {ref2!r}",
    )
    id1 = make_finding_id(artifact_id, finding_type, cik1)
    id2 = make_finding_id(artifact_id, finding_type, cik2)
    tc.assertEqual(
        id1,
        id2,
        f"finding_id must be stable for ({artifact_id!r}, {finding_type!r}, {cik1!r}): "
        f"got {id1!r} then {id2!r}",
    )
    tc.assertTrue(id1.startswith("fnd_"), f"finding_id must start with 'fnd_': got {id1!r}")


def assert_stable_key_beats_index(
    tc: unittest.TestCase,
    collection: ArtifactItemRefCollection,
    key: str,
    index: int,
) -> None:
    """Assert that a stable key takes priority over an index in both helpers.

    When both ``key`` and ``index`` are supplied:
    - ``make_canonical_item_key`` must embed ``key``, not ``index``
    - ``make_artifact_item_ref`` must set ``ref["key"] = key`` and ``ref["index"] = None``
    - ``make_item_identity`` must return consistent (key-based) outputs
    """
    cik = make_canonical_item_key(collection, key=key, index=index)
    expected_cik = f"{collection}:{key}"
    tc.assertEqual(
        cik,
        expected_cik,
        f"canonical_item_key must be '{expected_cik}' (key-priority), got {cik!r}",
    )

    ref = make_artifact_item_ref(collection, key=key, index=index)
    tc.assertEqual(ref["key"], key, f"ArtifactItemRef.key must be {key!r}: got {ref['key']!r}")
    tc.assertIsNone(
        ref["index"],
        f"ArtifactItemRef.index must be None when stable key is present: got {ref['index']!r}",
    )

    cik2, ref2 = make_item_identity(collection, key=key, index=index)
    tc.assertEqual(
        cik, cik2, "make_item_identity canonical_item_key must match make_canonical_item_key"
    )
    tc.assertEqual(
        ref, ref2, "make_item_identity artifact_item_ref must match make_artifact_item_ref"
    )


def assert_projection_order_excluded(
    tc: unittest.TestCase,
    artifact_id: str,
    finding_type: str,
    collection: ArtifactItemRefCollection,
    stable_key: str,
) -> None:
    """Assert that changing projection rank does not change finding_id.

    Simulates two projection orderings (index=1 vs index=5) for an item that
    has a stable key.  Because key beats index in the D2 priority rule, both
    orderings must collapse to the same ``canonical_item_key`` and therefore
    the same ``finding_id``.

    This is a non-trivial check: it would fail if the extractor accidentally
    passed the index-based rank as the ``index`` argument while also passing
    the stable key, and the helper incorrectly preferred the index.
    """
    cik_rank1 = make_canonical_item_key(collection, key=stable_key, index=1)
    cik_rank5 = make_canonical_item_key(collection, key=stable_key, index=5)
    tc.assertEqual(
        cik_rank1,
        cik_rank5,
        f"canonical_item_key must absorb projection rank when stable_key={stable_key!r}: "
        f"got {cik_rank1!r} (index=1) vs {cik_rank5!r} (index=5)",
    )

    id_rank1 = make_finding_id(artifact_id, finding_type, cik_rank1)
    id_rank5 = make_finding_id(artifact_id, finding_type, cik_rank5)
    tc.assertEqual(
        id_rank1,
        id_rank5,
        f"finding_id must not change when projection rank changes (stable_key={stable_key!r}): "
        f"got {id_rank1!r} vs {id_rank5!r}",
    )

    # Anti-pattern: using rank directly as key DOES shift the id — proving the
    # stable-key path above is genuinely distinct, not an accidental no-op.
    bad_cik_rank1 = make_canonical_item_key(collection, key="rank_1")
    bad_cik_rank5 = make_canonical_item_key(collection, key="rank_5")
    bad_id_rank1 = make_finding_id(artifact_id, finding_type, bad_cik_rank1)
    bad_id_rank5 = make_finding_id(artifact_id, finding_type, bad_cik_rank5)
    tc.assertNotEqual(
        bad_id_rank1,
        bad_id_rank5,
        "Anti-pattern check: rank-based keys MUST diverge to prove the stable-key path is distinct",
    )


__all__ = [
    "assert_finding_id_stable",
    "assert_projection_order_excluded",
    "assert_stable_key_beats_index",
]
