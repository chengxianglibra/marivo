"""Mirror intent-function docstrings onto the Session execution methods.

The execution surface (``session.observe`` / ``compare`` / ...) delegates to the
intent functions in ``marivo.analysis.intents.*``, which own the canonical
docstrings. The methods carry real type annotations in ``core.py`` source; this
installer copies the docstring across at import time so ``help(session.observe)``
and IPython ``?`` show it without duplicating the text. A hard import cycle
(``intents`` imports ``Session`` from ``core``) prevents copying it at
class-definition time, so this runs once from ``marivo.analysis.__init__``.

``intent_method_bindings`` is the single registry of which Session method mirrors
which intent function; the installer and the guard test both consume it so the
two never drift apart.

The same mirroring covers the escape-hatch methods (``session.from_pandas`` /
``promote_*``, owned by ``escape_hatch.*``) and the ``session.discover`` /
``session.transform`` namespace helpers (owned by the ``DiscoverAPI`` /
``TransformAPI`` methods).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from marivo.analysis.session.core import (
    Session,
    SessionDiscoverNamespace,
    SessionTransformNamespace,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def intent_method_bindings() -> dict[str, Callable[..., object]]:
    """Return ``{Session method name: intent function}`` for the delegating surface.

    Intent modules are imported lazily here because they import ``Session`` at
    module load; importing them at class-definition time would close the
    ``core`` <-> ``intents`` cycle.
    """

    from marivo.analysis.intents.assess_quality import assess_quality
    from marivo.analysis.intents.compare import compare
    from marivo.analysis.intents.correlate import correlate
    from marivo.analysis.intents.decompose import decompose
    from marivo.analysis.intents.forecast import forecast
    from marivo.analysis.intents.observe import observe
    from marivo.analysis.intents.test import hypothesis_test

    return {
        "observe": observe,
        "compare": compare,
        "decompose": decompose,
        "correlate": correlate,
        "forecast": forecast,
        "assess_quality": assess_quality,
        "hypothesis_test": hypothesis_test,
    }


def install_intent_docstrings() -> None:
    """Copy each canonical ``__doc__`` onto its Session-bound method.

    Idempotent and safe to call more than once.
    """

    from marivo.analysis import escape_hatch
    from marivo.analysis.intents.discover import discover
    from marivo.analysis.intents.transform import transform

    for method_name, intent in intent_method_bindings().items():
        getattr(Session, method_name).__doc__ = intent.__doc__

    # Escape-hatch methods own their docstrings in escape_hatch.*.
    Session.from_pandas.__doc__ = escape_hatch.from_pandas.__doc__
    Session.explore_ibis.__doc__ = escape_hatch.explore_ibis.__doc__
    Session.promote_metric_frame.__doc__ = escape_hatch.promote_metric_frame.__doc__
    Session.promote_delta_frame.__doc__ = escape_hatch.promote_delta_frame.__doc__
    Session.promote_attribution_frame.__doc__ = escape_hatch.promote_attribution_frame.__doc__

    # Discover namespace helpers own their docstrings on DiscoverAPI.
    SessionDiscoverNamespace.__call__.__doc__ = type(discover).__call__.__doc__
    for name in (
        "point_anomalies",
        "period_shifts",
        "driver_axes",
        "interesting_slices",
        "interesting_windows",
        "cross_sectional_outliers",
    ):
        getattr(SessionDiscoverNamespace, name).__doc__ = getattr(discover, name).__doc__

    # Transform namespace helpers own their docstrings on TransformAPI.
    SessionTransformNamespace.__call__.__doc__ = type(transform).__call__.__doc__
    for name in (
        "filter",
        "slice",
        "rollup",
        "topk",
        "bottomk",
        "rank",
        "normalize",
        "window",
    ):
        getattr(SessionTransformNamespace, name).__doc__ = getattr(transform, name).__doc__
