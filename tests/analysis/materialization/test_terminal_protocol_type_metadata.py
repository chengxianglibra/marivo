"""Static codec metadata reuse must not retain or trust decoded payloads."""

from __future__ import annotations

import json
from typing import get_type_hints
from unittest.mock import patch

import pytest

from marivo.analysis.materialization import terminal_protocol
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.terminal_protocol import InvocationInput, decode, encode


def test_record_type_hints_are_reused_without_caching_payload_validation() -> None:
    first = encode(InvocationInput("first-result"))
    second = encode(InvocationInput("second-result"))
    with patch.object(terminal_protocol, "get_type_hints", wraps=get_type_hints) as resolve_hints:
        assert decode(first, InvocationInput) == InvocationInput("first-result")
        assert decode(second, InvocationInput) == InvocationInput("second-result")
        assert resolve_hints.call_count <= 1

        invalid = json.loads(first)
        invalid["value"]["fields"]["result_id"] = 42
        with pytest.raises(IntegrityError, match="exact type"):
            decode(
                json.dumps(invalid, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                InvocationInput,
            )
        invalid["value"]["fields"]["result_id"] = ""
        with pytest.raises(IntegrityError, match="exact completed result ID"):
            decode(
                json.dumps(invalid, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                InvocationInput,
            )
