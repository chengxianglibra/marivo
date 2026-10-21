"""Frozen scope recovery rejects incomplete authority and retains exact identity."""

import json
from pathlib import Path

import pytest
from ibis.common.deferred import Just

from marivo._definition.deferred import recipe_fingerprint
from marivo._temporal import _time_scope_from_contract_data, _validate_time_scope_data
from marivo.refs import ref


def test_scope_payloads_require_complete_authority_and_retain_frozen_identity() -> None:
    # The old transport fixture is historical input, never migrated in place.
    samples = json.loads(Path(__file__).with_name("entrypoint_encoding_baseline.json").read_text())
    for sample in samples:
        ordinary = dict(sample["ordinary"])
        if sample["contract"]["kind"] == "absolute" and "boundary_timezone" not in ordinary:
            with pytest.raises(ValueError, match=r"timezone|boundary"):
                _validate_time_scope_data(ordinary)
            with pytest.raises(ValueError, match=r"timezone|boundary"):
                _time_scope_from_contract_data(sample["contract"])
            continue
        # Trusted transport decoding restores typed Refs before scope validation.
        if "calendar" in ordinary:
            ordinary["calendar"] = ref.period_calendar(ordinary["calendar"]["path"])
        if "temporal_set" in ordinary:
            ordinary["temporal_set"] = ref.temporal_set(ordinary["temporal_set"]["path"])
        scope = _validate_time_scope_data(ordinary)
        assert scope._model_dump(mode="json") == sample["ordinary"]
        assert scope._contract().model_dump(mode="json") == sample["contract"]
        assert recipe_fingerprint(Just(scope)) == sample["fingerprint"]
        recovered = _time_scope_from_contract_data(sample["contract"])
        assert recovered._contract().model_dump(mode="json") == sample["contract"]
        assert recipe_fingerprint(Just(recovered)) == sample["fingerprint"]
