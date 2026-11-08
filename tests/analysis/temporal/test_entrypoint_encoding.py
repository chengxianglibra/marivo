"""Retired executable time payloads never regain calendar authority."""

import json
from pathlib import Path

import pytest

from marivo._temporal import _validate_time_scope_data


def test_historical_business_scope_payloads_are_rejected() -> None:
    samples = json.loads(Path(__file__).with_name("entrypoint_encoding_baseline.json").read_text())
    for sample in samples:
        if sample["contract"]["kind"] != "absolute":
            with pytest.raises(ValueError):
                _validate_time_scope_data(sample["ordinary"])
