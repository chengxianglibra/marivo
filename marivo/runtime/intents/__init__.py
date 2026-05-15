from marivo.runtime.intents.attribute import run_attribute_intent
from marivo.runtime.intents.compare import run_compare_intent
from marivo.runtime.intents.correlate import run_correlate_intent
from marivo.runtime.intents.decompose import run_decompose_intent
from marivo.runtime.intents.detect import run_detect_intent
from marivo.runtime.intents.forecast import run_forecast_intent
from marivo.runtime.intents.observe import run_observe_intent
from marivo.runtime.intents.test import run_test_intent
from marivo.runtime.intents.validate import run_validate_intent

__all__ = [
    "run_attribute_intent",
    "run_compare_intent",
    "run_correlate_intent",
    "run_decompose_intent",
    "run_detect_intent",
    "run_forecast_intent",
    "run_observe_intent",
    "run_test_intent",
    "run_validate_intent",
]
