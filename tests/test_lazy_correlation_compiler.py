"""Independent source and bounded numeric Association references."""

from pathlib import Path

import ibis
import pandas as pd
import pytest

from marivo.analysis.compiler.correlation import lower_correlate, prepare_pairs
from marivo.analysis.operators.association_contracts import CorrelationMethod
from marivo.analysis.operators.association_values import execute_pairs
from tests.lazy_correlation_fixtures import association_spec, reference


@pytest.mark.parametrize("method", ["pearson", "spearman", "kendall"])
def test_source_and_numeric_reference(tmp_path: Path, method: CorrelationMethod) -> None:
    spec = association_spec(method)
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table(
            "numeric_pairs", {"revenue": [1.0, 3.0, 3.0, 8.0, None], "order_count": [2, 1, 1, 5, 9]}
        )
        recipe, checks = (
            prepare_pairs(table, spec) if method == "kendall" else lower_correlate(table, spec)
        )
        for check in checks:
            assert backend.execute(check.expression).iloc[0, 0] == 0
        frame = backend.to_pyarrow(recipe).to_pandas(types_mapper=pd.ArrowDtype)
        if method == "kendall":
            frame = execute_pairs(frame, spec)
        expected = reference([1, 3, 3, 8], [2, 1, 1, 5], method)
        row = frame.iloc[0]
        assert row.coefficient == pytest.approx(expected, abs=1e-12)
        assert row.complete_pair_count == 4 and row.null_pair_count == 1
    finally:
        backend.disconnect()


@pytest.mark.parametrize("method", ["pearson", "spearman", "kendall"])
def test_mixed_numeric_pairs_do_not_lose_integer_distinctions(method: CorrelationMethod) -> None:
    from itertools import combinations

    from marivo.analysis.datasets.handles import LogicalRootHandle
    from marivo.analysis.operators.association_contracts import CorrelatePayload
    from marivo.refs import ref
    from tests.lazy_observation_fixtures import make_sources

    source = (
        make_sources()
        .observe(
            [
                ref.metric("sales.order_count"),
                ref.metric("sales.revenue"),
                ref.metric("sales.mean_amount"),
            ]
        )
        .correlate(method=method)
    )
    assert isinstance(source._root, LogicalRootHandle) and isinstance(
        source._root.payload, CorrelatePayload
    )
    spec = source._root.payload.spec
    values = {
        "order_count": [2**60 + i for i in (1, 3, 2, 5)],
        "revenue": [1.0, 4.0, 3.0, 2.0],
        "mean_amount": [2.0, 1.0, 3.0, 4.0],
    }
    normalized = [[1.0, 3.0, 2.0, 5.0], [1.0, 4.0, 3.0, 2.0], [2.0, 1.0, 3.0, 4.0]]
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table("mixed_pairs", values)
        pairs, _ = prepare_pairs(table, spec)
        result = execute_pairs(
            backend.to_pyarrow(pairs).to_pandas(types_mapper=pd.ArrowDtype), spec
        )
        expected = [
            reference(normalized[a], normalized[b], method) for a, b in combinations(range(3), 2)
        ]
        assert result.coefficient.tolist() == pytest.approx(expected)
    finally:
        backend.disconnect()
