"""Typed output directory retained with each finished attribution target."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from typing_extensions import override

from marivo.analysis.model.method_values import _FactRecord, _fail, _short


@dataclass(frozen=True, slots=True, repr=False)
class FrameMethodOutput(_FactRecord):
    """Associate one exact original output path with its report role."""

    role: Literal[
        "contribution",
        "total",
        "observed_total",
        "share",
        "value",
        "current",
        "baseline",
        "absolute",
        "numerator",
        "denominator",
        "numerator_current",
        "numerator_baseline",
        "denominator_current",
        "denominator_baseline",
        "valid_count",
        "valid_count_current",
        "valid_count_baseline",
        "present",
        "current_present",
        "baseline_present",
        "effect_within",
        "effect_mix",
        "effect_entry_exit",
        "effect_additive",
        "contribution_std_error",
    ]
    path: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.role not in (
            "contribution",
            "total",
            "observed_total",
            "share",
            "value",
            "current",
            "baseline",
            "absolute",
            "numerator",
            "denominator",
            "numerator_current",
            "numerator_baseline",
            "denominator_current",
            "denominator_baseline",
            "valid_count",
            "valid_count_current",
            "valid_count_baseline",
            "present",
            "current_present",
            "baseline_present",
            "effect_within",
            "effect_mix",
            "effect_entry_exit",
            "effect_additive",
            "contribution_std_error",
        ):
            _fail("a closed report output role", repr(self.role))
        if (
            type(self.path) is not tuple
            or not self.path
            or any(type(part) is not str or not part for part in self.path)
        ):
            _fail("a complete nonempty output path", repr(self.path))

    @override
    def _summary(self) -> str:
        return f"kind=output, role={self.role}, path={_short(repr(self.path), 60)}"


@dataclass(frozen=True, slots=True, repr=False)
class FrameMethodTarget(_FactRecord):
    """Original target and output directory, retained even for an empty parent set."""

    target: tuple[str, ...]
    kind: Literal["level", "absolute", "relative"]
    outputs: tuple[FrameMethodOutput, ...]

    def __post_init__(self) -> None:
        if (
            type(self.target) is not tuple
            or not self.target
            or any(type(part) is not str or not part for part in self.target)
        ):
            _fail("a complete original target path", repr(self.target))
        if self.kind not in ("level", "absolute", "relative"):
            _fail("a closed target kind", repr(self.kind))
        if type(self.outputs) is not tuple or any(
            type(item) is not FrameMethodOutput for item in self.outputs
        ):
            _fail("immutable closed output mappings", repr(self.target))
        roles = tuple(item.role for item in self.outputs)
        if (
            roles[:4] != ("contribution", "total", "observed_total", "share")
            or len(set(roles)) != len(roles)
            or len({item.path for item in self.outputs}) != len(self.outputs)
        ):
            _fail("four ordered main roles and unique diagnostics", repr(self.target))

    @override
    def _summary(self) -> str:
        return (
            f"kind={self.kind}, target={_short(repr(self.target), 60)}, outputs={len(self.outputs)}"
        )
