"""An immutable complete coordinate handle bound to one exact Frame input."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from marivo.analysis.core.frame_structure import AxisRole

if TYPE_CHECKING:
    from marivo.analysis.dsl.frame import AnalysisFrame


@dataclass(frozen=True, slots=True, init=False, repr=False, eq=False)
class FrameAxis:
    """Acquire complete coordinate handles from a Frame's axes or axis(name)."""

    _frame: AnalysisFrame
    _input_id: str
    token: str
    name: str
    role: AxisRole
    key_names: tuple[str, ...]

    def __init__(self) -> None:
        raise TypeError("Use frame.axes or frame.axis(name).")

    @classmethod
    def _from_frame(
        cls, frame: AnalysisFrame, token: str, name: str, role: AxisRole, key_names: tuple[str, ...]
    ) -> FrameAxis:
        result = object.__new__(cls)
        for key, value in (
            ("_frame", frame),
            ("_input_id", frame._input_id),
            ("token", token),
            ("name", name),
            ("role", role),
            ("key_names", key_names),
        ):
            object.__setattr__(result, key, value)
        return result

    def __repr__(self) -> str:
        return f"<FrameAxis token={self.token[:40]!r} role={self.role} input={self._input_id[:16]}; use .show()>"

    def show(self) -> None:
        """Print captured coordinate metadata without reading rows or preparing sources."""
        print("FrameAxis")
        for key, value in (
            ("token", self.token),
            ("name", self.name),
            ("role", self.role),
            ("key_names", repr(self.key_names)),
        ):
            print(
                f"{key}={value}"
                if len(value.encode("utf-8")) <= 600
                else f"{key}=omitted ({len(value)} characters)"
            )
