"""Intent entrypoints for analysis_py."""

from marivo.analysis_py.intents.compare import compare
from marivo.analysis_py.intents.correlate import correlate
from marivo.analysis_py.intents.decompose import decompose
from marivo.analysis_py.intents.discover import discover
from marivo.analysis_py.intents.observe import observe
from marivo.analysis_py.intents.transform import transform

__all__ = ["compare", "correlate", "decompose", "discover", "observe", "transform"]
