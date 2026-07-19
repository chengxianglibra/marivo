"""Dependency-neutral exact semantic ref aliases for analysis."""

from marivo.semantic.refs import DimensionRef, TimeDimensionRef

type AnalysisDimensionRef = DimensionRef | TimeDimensionRef

__all__ = ["AnalysisDimensionRef"]
