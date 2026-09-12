"""Closed replay-based Lifecycle analysis values."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict


def _fingerprint(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class FromInception(BaseModel):
    """Required Phase 3 replay seed using the first qualifying inception."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["from_inception"] = "from_inception"

    @property
    def fingerprint(self) -> str:
        """Return the stable seed-contract fingerprint."""
        return _fingerprint(
            {
                "schema": "marivo.lifecycle_seed/v1",
                "kind": self.kind,
            }
        )


def from_inception() -> FromInception:
    """Return the only Phase 3 Lifecycle replay seed.

    Returns:
        A frozen from-inception replay seed.

    Guidance:
        Use this when the modeled Event history contains a deterministic
        inception before the state interval you want to read. Replay
        reconstructs state from that inception even when it precedes the
        window, so the window bounds the reported intervals rather than the
        evidence. A missing inception is never replaced by assuming the
        initial state: under complete input coverage it fails, and under
        unknown coverage the subject is reported as coverage-censored.

    Example:
        >>> seed = mv.from_inception()

    Constraints:
        Replay has no default seed and no hidden fallback; pass this value
        explicitly. The StateModel must declare at least one inception
        trigger.
    """
    return FromInception()


__all__ = ["FromInception", "from_inception"]
