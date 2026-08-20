"""Typed coverage analysis frames."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ConfigDict

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta
from marivo.render import Card


class CoverageFrameMeta(BaseFrameMeta):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["coverage_frame"] = "coverage_frame"
    parent_ref: str
    coverage_kind: Literal["time_slot", "window_coverage"] = "time_slot"
    axes: dict[str, Any]
    sample_interval: str | None = None


@dataclass(repr=False)
class CoverageFrame(BaseFrame):
    """Call marivo.help(CoverageFrame) for its public consumption contract."""

    meta: CoverageFrameMeta

    def _repr_identity(self) -> str:
        return f"CoverageFrame ref={self.meta.ref} parent={self.meta.parent_ref} rows={self.meta.row_count}"

    def _card(self) -> Card:
        card = self._header_card()
        card.field(
            "coverage",
            (
                f"kind={self.meta.coverage_kind} parent={self.meta.parent_ref} "
                f"sample_interval={self.meta.sample_interval or 'none'}"
            ),
        )
        if self.meta.axes:
            card.listing(
                "axes",
                (f"{key}={self.meta.axes[key]}" for key in sorted(self.meta.axes)),
            )
        self._append_evidence_sections(card)
        return self._append_preview_table(card)
