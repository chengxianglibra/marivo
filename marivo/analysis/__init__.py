"""Typed lazy analysis with explicit execution and committed Dataset reads."""

from types import ModuleType
from typing import TYPE_CHECKING, Literal

from marivo._definition.windows import all_history as all_history
from marivo._definition.windows import since as since
from marivo._definition.windows import to_date as to_date
from marivo._definition.windows import trailing_duration as trailing_duration
from marivo._definition.windows import trailing_periods as trailing_periods
from marivo._temporal import BeforeEndBoundary as BeforeEndBoundary
from marivo._temporal import Grain as Grain
from marivo._temporal import TimeScope as TimeScope

if TYPE_CHECKING:
    __all__: list[str]
    from marivo._definition.correspondence import CalendarCorrespondence as CalendarCorrespondence
    from marivo._definition.correspondence import CorrespondenceAxis as CorrespondenceAxis
    from marivo._definition.correspondence import DeclaredCorrespondence as DeclaredCorrespondence
    from marivo._temporal import InstantAnchor as InstantAnchor
    from marivo.analysis import session as session
    from marivo.analysis._contract import AnalysisAction as AnalysisAction
    from marivo.analysis._contract import AnalysisContract as AnalysisContract
    from marivo.analysis.dsl.artifact import Artifact as Artifact
    from marivo.analysis.dsl.column import Column as Column
    from marivo.analysis.dsl.column import row_count as row_count
    from marivo.analysis.dsl.correspondence import corresponding as corresponding
    from marivo.analysis.dsl.correspondence import ordinal as ordinal
    from marivo.analysis.dsl.correspondence import period_offset as period_offset
    from marivo.analysis.dsl.correspondence import previous_year as previous_year
    from marivo.analysis.dsl.metrics import member_count as member_count
    from marivo.analysis.dsl.population import MemberAxis as MemberAxis
    from marivo.analysis.dsl.population import MembershipAxis as MembershipAxis
    from marivo.analysis.dsl.population import Population as Population
    from marivo.analysis.dsl.population import PopulationPredicate as PopulationPredicate
    from marivo.analysis.dsl.population import coordinates as coordinates
    from marivo.analysis.dsl.population import identities as identities
    from marivo.analysis.dsl.population import in_population as in_population
    from marivo.analysis.dsl.population import member as member
    from marivo.analysis.dsl.population import membership as membership
    from marivo.analysis.dsl.population import product as product
    from marivo.analysis.dsl.query import Query as Query
    from marivo.analysis.dsl.references import broadcast as broadcast
    from marivo.analysis.dsl.temporal import CalendarAxis as CalendarAxis
    from marivo.analysis.evidence._dataset_types import ArtifactDigest as ArtifactDigest
    from marivo.analysis.evidence._dataset_types import Finding as Finding
    from marivo.analysis.evidence._dataset_types import FindingPage as FindingPage
    from marivo.analysis.refs import ArtifactRef as ArtifactRef
    from marivo.analysis.session._lazy_read_model import ArtifactSummary as ArtifactSummary
    from marivo.analysis.session._lazy_read_model import FailedRun as FailedRun
    from marivo.analysis.session._lazy_read_model import IncompleteRun as IncompleteRun
    from marivo.analysis.session._lazy_read_model import RunPage as RunPage
    from marivo.analysis.session._lazy_read_model import SessionGraph as SessionGraph
    from marivo.analysis.session._lazy_read_model import SucceededRun as SucceededRun
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
        count: Positive sub-day width; calendar-variable units require one.

    Returns:
        The immutable public Grain value.

    Example:
        >>> import marivo.analysis as mv
        >>> mv.grain("month")

    Constraints:
        Semantic calendar levels are constructed by ``ms.calendar_grain(...)``.
    """
    from marivo._temporal import builtin_grain

    return builtin_grain(unit, count=count)
