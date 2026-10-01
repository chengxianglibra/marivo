"""No-I/O admission, closed authority and semantic approximation boundaries."""

import pytest

from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.semantic._quantile import distribution_metric_input
from tests.lazy_distribution_fixtures import make_distribution_registry
from tests.lazy_observation_fixtures import NoIoActionPort


def test_nonpercentile_explicit_method_rejected_without_execution() -> None:
    registry, sidecar = make_distribution_registry()
    source = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="contracts",
        store_id="contracts",
    )
    from marivo.refs import ref

    with pytest.raises(DatasetConstructionError, match="governed root median or percentile"):
        source.observe(
            distribution_metric_input(ref.metric("sales.order_count"), method="duckdb_tdigest@v1")
        )
