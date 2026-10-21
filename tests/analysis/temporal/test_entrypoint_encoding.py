"""Pre-cutover scope encodings remain distinct and byte-stable in private consumers."""

import json
from pathlib import Path

from ibis.common.deferred import Just

from marivo._definition.deferred import recipe_fingerprint
from marivo._temporal import _time_scope_from_contract_data, _validate_time_scope_data
from marivo.refs import ref


def test_scope_payloads_and_deferred_fingerprints_match_pre_cutover() -> None:
    # Captured from 8f93bebaac before the entrypoint cutover, including explicit null
    # in the versioned occurrence contract and omission in the ordinary payload.
    samples = json.loads(Path(__file__).with_name("entrypoint_encoding_baseline.json").read_text())
    for sample in samples:
        ordinary = dict(sample["ordinary"])
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
