"""Compare the same fixed graph with sequential grouping enabled and disabled.

Run ``.venv/bin/python -m devtools.fixed_selection_cost > /tmp/fixed-selection-cost.json``.
Timing, memory and actual consumer-work instrumentation run separately. No Store,
source reads, publication or cache hits are included in these local executor costs.
"""

from __future__ import annotations

import gc
import hashlib
import json
import platform
import statistics
import subprocess
import time
import tracemalloc
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa

from marivo.analysis.compiler.graph_lowering import LoweredLocal, LoweredPlan
from marivo.analysis.core.graph import Edge, MethodNode, method_node
from marivo.analysis.core.rules import CellDerive, PartsTransport
from marivo.analysis.materialization import graph_local_execution as local
from marivo.analysis.materialization.graph_exchange import (
    ExchangePart,
    ExchangeResult,
    VerifiedFixedInput,
)
from marivo.analysis.materialization.graph_execution import PreparedGraph
from tests.analysis.graph.selection_fixtures import (
    selection,
    selection_chain,
    selection_input,
    selection_plan,
)


@dataclass
class Work:
    primary_conversions: int = 0
    part_key_conversions: int = 0
    indexes: int = 0
    result_constructions: int = 0
    part_restrictions: int = 0
    predicate_rows: dict[str, int] = field(default_factory=dict)


def _work(prepared: PreparedGraph, lowered: LoweredPlan, verified: VerifiedFixedInput) -> Work:
    counters = Work(
        predicate_rows={
            stage.stage.node.identity: 0
            for stage in lowered.stages
            if isinstance(stage, LoweredLocal)
            and isinstance(stage.stage.node.parameters, PartsTransport)
        }
    )
    decode, index = local._transport_rows, local._index_rows
    accepts, finish = local._selection_accepts, local._transport_exchange

    def rows(table: pa.Table) -> list[dict[str, object]]:
        if "value" in table.column_names:
            counters.primary_conversions += 1
        else:
            counters.part_key_conversions += 1
        return decode(table)

    def indexed(
        table: pa.Table, keys: tuple[str, ...]
    ) -> dict[tuple[object, ...], dict[str, object]]:
        counters.indexes += 1
        return index(table, keys)

    def accepted(
        node: MethodNode,
        inputs: list[dict[tuple[object, ...], dict[str, object]]],
        key: tuple[object, ...],
    ) -> bool:
        counters.predicate_rows[node.identity] = counters.predicate_rows.get(node.identity, 0) + 1
        return accepts(node, inputs, key)

    def finished(
        method: LoweredLocal,
        source: ExchangeResult,
        binding: str,
        primary: pa.Table,
        parts: tuple[ExchangePart, ...],
    ) -> ExchangeResult:
        counters.result_constructions += 1
        result = finish(method, source, binding, primary, parts)
        counters.part_restrictions += len(result.parts)
        return result

    with (
        patch.object(local, "_transport_rows", rows),
        patch.object(local, "_index_rows", indexed),
        patch.object(local, "_selection_accepts", accepted),
        patch.object(local, "_transport_exchange", finished),
    ):
        local.execute_verified_fixed(prepared, lowered, (verified,))
    return counters


def _measure(
    prepared: PreparedGraph, lowered: LoweredPlan, verified: VerifiedFixedInput, enabled: bool
) -> dict[str, object]:
    with nullcontext() if enabled else patch.object(local, "_selection_groups", return_value={}):
        # One warm-up, followed by seven uninstrumented executions of the same graph/input.
        local.execute_verified_fixed(prepared, lowered, (verified,))
        samples = []
        for _ in range(7):
            start = time.perf_counter()
            result = local.execute_verified_fixed(prepared, lowered, (verified,))
            samples.append((time.perf_counter() - start) * 1000)
            del result
        work = _work(prepared, lowered, verified)
        gc.collect()
        pool = pa.proxy_memory_pool(pa.default_memory_pool())
        previous = pa.default_memory_pool()
        pa.set_memory_pool(pool)
        tracemalloc.start()
        try:
            result = local.execute_verified_fixed(prepared, lowered, (verified,))
            _, python_peak = tracemalloc.get_traced_memory()
            arrow_peak = pool.max_memory()
            values = result.primary["value"].to_pylist()
            keys = result.primary["key_1"].to_pylist()
            del result
        finally:
            tracemalloc.stop()
            pa.set_memory_pool(previous)
        return {
            "samples_ms": samples,
            "median_ms": statistics.median(samples),
            "python_peak_bytes": python_peak,
            "arrow_peak_bytes": arrow_peak,
            "primary_conversions": work.primary_conversions,
            "part_key_conversions": work.part_key_conversions,
            "indexes": work.indexes,
            "result_constructions": work.result_constructions,
            "part_restrictions": work.part_restrictions,
            "predicate_rows": list(work.predicate_rows.values()),
            "result_rows": len(values),
            "oracle_keys": keys,
            "oracle_values": values,
        }


def main() -> None:
    cases = [
        (f"chain-{length}-{retained}-parts-{parts}", length, retained, parts)
        for length in (2, 8)
        for retained in (90, 20)
        for parts in (False, True)
    ]
    cases.extend((("empty", 8, 0, True), ("shared", 2, 90, False)))
    reports = []
    for name, length, retained, parts in cases:
        count = 20_000
        threshold = count - count * retained // 100 - 1
        leaf, verified = selection_input(count, parts=parts)
        if name == "shared":
            shared = selection(leaf, threshold)
            left, right = selection(shared, threshold + 1), selection(shared, threshold + 1)
            root = method_node(
                (Edge("current", left), Edge("baseline", right)),
                CellDerive(
                    "difference",
                    "difference",
                    "strict",
                    "units",
                    "all",
                    "source.exact_pairing@v1",
                    "source.finite_numeric@v1",
                ),
                value_type=leaf.value_type,
            )
            expected = [i for i in range(count) if i > threshold + 1]
        else:
            root = selection_chain(leaf, length, threshold)
            expected = [i for i in range(count) if i > threshold + length - 1]
        prepared, lowered = selection_plan(root)
        ordinary = _measure(prepared, lowered, verified, False)
        fused = _measure(prepared, lowered, verified, True)
        for result in (ordinary, fused):
            assert result.pop("oracle_keys") == list(map(str, expected))
            assert result.pop("oracle_values") == (
                [0] * len(expected) if name == "shared" else expected
            )
        assert fused["predicate_rows"] == ordinary["predicate_rows"]
        reports.append(
            {
                "case": name,
                "rows": count,
                "length": length,
                "first_retained_percent": retained,
                "parts": parts,
                "sequential": ordinary,
                "grouped": fused,
            }
        )
    print(
        json.dumps(
            {
                "base": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "source_sha256": {
                    name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
                    for name in (
                        "marivo/analysis/core/local_laws.py",
                        "marivo/analysis/materialization/graph_local_execution.py",
                        "tests/analysis/graph/selection_fixtures.py",
                    )
                },
                "platform": platform.platform(),
                "python": platform.python_version(),
                "pyarrow": pa.__version__,
                "route": "artifact_python/int64",
                "samples": 7,
                "warmup": 1,
                "memory": "one separate execution; Python tracemalloc plus Arrow proxy allocator peaks",
                "cases": reports,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
