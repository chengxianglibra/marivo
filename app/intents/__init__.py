from app.intents.attribute import run_attribute_intent
from app.intents.compare import run_compare_intent
from app.intents.correlate import run_correlate_intent
from app.intents.decompose import run_decompose_intent
from app.intents.detect import run_detect_intent
from app.intents.forecast import run_forecast_intent
from app.intents.observe import run_observe_intent
from app.intents.test import run_test_intent

__all__ = [
    "run_attribute_intent",
    "run_compare_intent",
    "run_correlate_intent",
    "run_decompose_intent",
    "run_detect_intent",
    "run_forecast_intent",
    "run_observe_intent",
    "run_test_intent",
]
