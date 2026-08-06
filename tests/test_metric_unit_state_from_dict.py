"""Contract-drift tests for ``unit_state_from_dict`` (issue #63).

The function is ``canonical_value``'s inverse for ``MetricUnitStateV2`` and is
executed on the public ``.metric()`` projection path. Issue #63 demands a single
fail-closed contract: malformed or forward payloads must raise a typed marivo
error instead of silently degrading, raising bare ``KeyError``/``ValueError``,
or producing silently wrong data.
"""

from __future__ import annotations

import pytest

from marivo.semantic.metric_graph_canonical import canonical_value
from marivo.semantic.unit_algebra import (
    FactorizedUnitV2,
    OpaqueUnitV2,
    UnitStatePayloadError,
    UnknownUnitV2,
    unit_state_from_dict,
)

# ---------------------------------------------------------------------------
# Round-trips: every authored shape must reconstruct exactly.
# ---------------------------------------------------------------------------


def _roundtrip(state: object) -> object:
    rebuilt = unit_state_from_dict(canonical_value(state))
    assert rebuilt is not None
    return rebuilt


def test_roundtrip_factorized() -> None:
    original = FactorizedUnitV2(
        schema="metric-unit-algebra/v2",
        numerator=("CNY", "USD"),
        denominator=("s",),
    )
    assert _roundtrip(original) == original


def test_roundtrip_dimensionless() -> None:
    original = FactorizedUnitV2(schema="metric-unit-algebra/v2", numerator=(), denominator=())
    assert _roundtrip(original) == original


def test_roundtrip_empty_numerator_with_denominator() -> None:
    original = FactorizedUnitV2(schema="metric-unit-algebra/v2", numerator=(), denominator=("s",))
    assert _roundtrip(original) == original


def test_roundtrip_single_atom() -> None:
    original = FactorizedUnitV2(schema="metric-unit-algebra/v2", numerator=("CNY",), denominator=())
    assert _roundtrip(original) == original


def test_roundtrip_opaque() -> None:
    original = OpaqueUnitV2(schema="metric-unit-opaque/v2", value="CNY/(request)")
    assert _roundtrip(original) == original


def test_roundtrip_unknown() -> None:
    original = UnknownUnitV2(schema="metric-unit-unknown/v2")
    assert _roundtrip(original) == original


def test_roundtrip_is_canonical_idempotent() -> None:
    original = FactorizedUnitV2(
        schema="metric-unit-algebra/v2",
        numerator=("CNY",),
        denominator=("s", "s"),
    )
    rebuilt = _roundtrip(original)
    # canonical_value of the rebuilt state must be bytewise identical to the
    # canonical_value of the original (tuple/list normalization is stable).
    assert canonical_value(rebuilt) == canonical_value(original)


# ---------------------------------------------------------------------------
# Passthrough: None and already-typed states pass through unchanged.
# ---------------------------------------------------------------------------


def test_none_passes_through() -> None:
    assert unit_state_from_dict(None) is None


def test_typed_state_passes_through() -> None:
    original = FactorizedUnitV2(schema="metric-unit-algebra/v2", numerator=("CNY",), denominator=())
    assert unit_state_from_dict(original) is original


def test_typed_opaque_passes_through() -> None:
    original = OpaqueUnitV2(schema="metric-unit-opaque/v2", value="CNY/(request)")
    assert unit_state_from_dict(original) is original


# ---------------------------------------------------------------------------
# Fail-closed: malformed payloads raise a typed marivo error.
# ---------------------------------------------------------------------------


def test_non_dict_payload_is_rejected() -> None:
    for payload in (42, [1, 2], "CNY", b"bytes"):
        with pytest.raises(UnitStatePayloadError):
            unit_state_from_dict(payload)


def test_missing_schema_is_rejected() -> None:
    with pytest.raises(UnitStatePayloadError):
        unit_state_from_dict({"numerator": ["CNY"]})


def test_non_string_schema_is_rejected() -> None:
    with pytest.raises(UnitStatePayloadError):
        unit_state_from_dict({"schema": 42})


def test_unknown_schema_raises_typed_error() -> None:
    """A forward-persisted schema must not silently degrade to unknown.

    Issue #63: previously an unknown ``schema`` returned ``None``, which then
    persisted as an unknown unit and silently dropped the real unit. Fail-closed
    surfaces the forward-incompatible payload instead.
    """
    with pytest.raises(UnitStatePayloadError, match="unsupported unit state schema"):
        unit_state_from_dict(
            {"schema": "metric-unit-algebra/v3", "numerator": [], "denominator": []}
        )


def test_opaque_missing_value_raises_typed_error() -> None:
    """Previously a bare ``KeyError: 'value'`` leaked to the public caller."""
    with pytest.raises(UnitStatePayloadError):
        unit_state_from_dict({"schema": "metric-unit-opaque/v2"})


def test_opaque_non_string_value_raises_typed_error() -> None:
    with pytest.raises(UnitStatePayloadError):
        unit_state_from_dict({"schema": "metric-unit-opaque/v2", "value": 42})


def test_algebra_numerator_missing_raises_typed_error() -> None:
    with pytest.raises(UnitStatePayloadError):
        unit_state_from_dict({"schema": "metric-unit-algebra/v2", "denominator": ["s"]})


def test_algebra_denominator_missing_raises_typed_error() -> None:
    with pytest.raises(UnitStatePayloadError):
        unit_state_from_dict({"schema": "metric-unit-algebra/v2", "numerator": ["CNY"]})


def test_algebra_numerator_string_raises_typed_error() -> None:
    """The single silently-wrong-data path from issue #63: ``"CNY"`` previously
    became ``('C', 'N', 'Y')`` with no error and no warning."""
    with pytest.raises(UnitStatePayloadError):
        unit_state_from_dict(
            {"schema": "metric-unit-algebra/v2", "numerator": "CNY", "denominator": []}
        )


def test_algebra_denominator_string_raises_typed_error() -> None:
    with pytest.raises(UnitStatePayloadError):
        unit_state_from_dict(
            {"schema": "metric-unit-algebra/v2", "numerator": ["CNY"], "denominator": "s"}
        )


def test_algebra_non_string_atom_raises_typed_error() -> None:
    with pytest.raises(UnitStatePayloadError):
        unit_state_from_dict(
            {"schema": "metric-unit-algebra/v2", "numerator": ["CNY", 42], "denominator": []}
        )


def test_algebra_unreduced_factors_raise_typed_error() -> None:
    """``FactorizedUnitV2.__post_init__`` requires reduced bytewise-sorted
    factors; the failure must surface as a typed error, not a bare ValueError."""
    with pytest.raises(UnitStatePayloadError):
        unit_state_from_dict(
            {
                "schema": "metric-unit-algebra/v2",
                "numerator": ["s", "CNY"],
                "denominator": ["s"],
            }
        )


def test_unknown_payload_with_extra_fields_raises_typed_error() -> None:
    with pytest.raises(UnitStatePayloadError):
        unit_state_from_dict({"schema": "metric-unit-unknown/v2", "value": "sneaky"})


def test_error_carries_expected_and_received_fields() -> None:
    with pytest.raises(UnitStatePayloadError) as excinfo:
        unit_state_from_dict({"schema": "metric-unit-opaque/v2"})
    assert excinfo.value.message
    assert excinfo.value.expected is not None
    assert excinfo.value.received is not None
