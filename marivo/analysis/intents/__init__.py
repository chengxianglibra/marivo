"""Intent entrypoints for analysis."""

from marivo.analysis.intents.assess_quality import assess_quality
from marivo.analysis.intents.attribute import attribute
from marivo.analysis.intents.compare import compare
from marivo.analysis.intents.correlate import correlate
from marivo.analysis.intents.discover import discover
from marivo.analysis.intents.forecast import forecast
from marivo.analysis.intents.hypothesis_test import hypothesis_test
from marivo.analysis.intents.observe import observe
from marivo.analysis.intents.transform import transform

__all__ = [
    "assess_quality",
    "attribute",
    "compare",
    "correlate",
    "discover",
    "forecast",
    "hypothesis_test",
    "observe",
    "transform",
]
