"""Intent entrypoints for analysis_py."""

from marivo.analysis_py.intents.assess_quality import assess_quality
from marivo.analysis_py.intents.compare import compare
from marivo.analysis_py.intents.correlate import correlate
from marivo.analysis_py.intents.decompose import decompose
from marivo.analysis_py.intents.discover import discover
from marivo.analysis_py.intents.forecast import forecast
from marivo.analysis_py.intents.observe import observe
from marivo.analysis_py.intents.test import hypothesis_test as test
from marivo.analysis_py.intents.transform import transform

__all__ = [
    "assess_quality",
    "compare",
    "correlate",
    "decompose",
    "discover",
    "forecast",
    "observe",
    "test",
    "transform",
]
