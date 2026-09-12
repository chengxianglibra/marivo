"""Three-process correlation acceptance with local storage."""

import os
from pathlib import Path

import pytest

from marivo.analysis.operators.association_contracts import CorrelationMethod
from tests.lazy_correlation_acceptance_fixtures import correlation_journey

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize(
    "method,kind",
    [
        ("pearson", "engine"),
        ("spearman", "engine"),
        ("pearson", "local"),
        ("spearman", "local"),
        ("kendall", "local"),
    ],
)
def test_three_process_correlation(tmp_path: Path, method: CorrelationMethod, kind: str) -> None:
    correlation_journey(tmp_path, method, kind, {**os.environ, "MARIVO_TELEMETRY": "off"})
