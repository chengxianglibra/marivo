"""Immutable analysis data with explicit computation and persistence."""

from types import ModuleType
from typing import TYPE_CHECKING, Literal

from marivo._definition.windows import all_history as all_history
from marivo._definition.windows import since as since
from marivo._definition.windows import to_date as to_date
from marivo._definition.windows import trailing_duration as trailing_duration
from marivo._definition.windows import trailing_periods as trailing_periods
from marivo._temporal import Grain as Grain
from marivo._temporal import TimeScope as TimeScope

if TYPE_CHECKING:
    __all__: list[str]
    from marivo._definition.correspondence import CalendarCorrespondence as CalendarCorrespondence
    from marivo._grouping import Grouping as Grouping
    from marivo._temporal import InstantAnchor as InstantAnchor
    from marivo.analysis import session as session
    from marivo.analysis.core.frame_method_facts import FrameMethodFacts as FrameMethodFacts
    from marivo.analysis.core.frame_page import FramePage as FramePage
    from marivo.analysis.core.frame_page import FrameReadStats as FrameReadStats
    from marivo.analysis.core.frame_schema import FrameAxisSchema as FrameAxisSchema
    from marivo.analysis.core.frame_schema import FrameField as FrameField
    from marivo.analysis.core.frame_schema import FrameSchema as FrameSchema
    from marivo.analysis.core.lineage import ColumnLineage as ColumnLineage
    from marivo.analysis.core.lineage import FrameLineage as FrameLineage
    from marivo.analysis.core.lineage import SemanticObjectIdentity as SemanticObjectIdentity
    from marivo.analysis.dsl.baseline import ComparisonBaseline as ComparisonBaseline
    from marivo.analysis.dsl.baseline import baseline as baseline
    from marivo.analysis.dsl.column import Column as Column
    from marivo.analysis.dsl.column import row_count as row_count
    from marivo.analysis.dsl.correspondence import offset as offset
    from marivo.analysis.dsl.correspondence import ordinal as ordinal
    from marivo.analysis.dsl.frame import AnalysisFrame as AnalysisFrame
    from marivo.analysis.dsl.frame_axis import FrameAxis as FrameAxis
    from marivo.analysis.dsl.population import Population as Population
    from marivo.analysis.dsl.population import coordinates as coordinates
    from marivo.analysis.dsl.population import identity as identity
    from marivo.analysis.dsl.population import in_population as in_population
    from marivo.analysis.dsl.population import membership as membership
    from marivo.analysis.dsl.population import product as product
    from marivo.analysis.dsl.temporal import CalendarAxis as CalendarAxis
    from marivo.analysis.evidence._dataset_types import EvidencePage as EvidencePage
    from marivo.analysis.evidence._dataset_types import Finding as Finding
    from marivo.analysis.evidence._dataset_types import FindingPage as FindingPage
    from marivo.analysis.refs import ArtifactRef as ArtifactRef
    from marivo.analysis.session.core import Session as Session


if not TYPE_CHECKING:

    def __getattr__(name: str) -> object:
        if name == "help":
            raise AttributeError('Use marivo.help("analysis") for the public analysis contract.')
        if name == "catalog":
            raise AttributeError(
                "The analysis catalog is session-bound; acquire session = mv.session.get_or_create(name), then use catalog = session.catalog."
            )
        if name.startswith("__") and name not in ("__all__", "__marivo_telemetry_capabilities__"):
            raise AttributeError(name)
        return getattr(_initialize_public(), name)


def _initialize_public() -> ModuleType:
    from importlib import import_module

    return import_module("marivo.analysis._public")


def __dir__() -> list[str]:
    return sorted(_initialize_public().__all__)


def grain(
    unit: Literal[
        "second",
        "minute",
        "hour",
        "day",
        "week",
        "month",
        "quarter",
        "year",
    ],
    *,
    count: int = 1,
) -> Grain:
    """Construct one builtin aggregation grain.

    Args:
        unit: One builtin unit from second through year.
        count: Positive width; units day through year require one.

    Returns:
        The immutable public Grain value.

    Example:
        >>> import marivo.analysis as mv
        >>> mv.grain("month")

    Constraints:
        Subday widths must tile a nominal day and remain shorter than one day.
        Use one spelling: 60second, 120second and 60minute reject in favor of
        larger units; 24hour rejects. 90second and 90minute remain legal.
        Grains have equality/hash but no ordering. Semantic calendar lookup
        comes from ``ms.calendar_grain(...)``; ``calendar.grain(level)`` captures
        its certified snapshot identity.
    """
    from marivo._temporal import builtin_grain

    return builtin_grain(unit, count=count)
