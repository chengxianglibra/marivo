"""Structured local admission errors for private lazy observation."""

from marivo.analysis.datasets.errors import DatasetConstructionError


class ObservationConstructionError(DatasetConstructionError):
    """The supplied source or operator lacks exact semantic authority."""


class ObservationBindingError(ObservationConstructionError):
    """Parameterized source inputs do not match their declaration."""


class ObservationPredicateError(ObservationConstructionError):
    """An analysis predicate cannot be interpreted at the current row grain."""
