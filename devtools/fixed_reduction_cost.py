"""Compare one fixed graph/input with direct-key L8 enabled and disabled.

Run ``.venv/bin/python -m devtools.fixed_reduction_cost``. Timings exclude setup,
Store, sources and publication. Work and allocator measurements run separately.
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
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa

from marivo.analysis.compiler.graph_lowering import LoweredLocal, LoweredPlan
from marivo.analysis.core.graph import Edge, MethodNode, method_node
from marivo.analysis.core.rules import CellDerive
from marivo.analysis.materialization import graph_local_execution as local
from marivo.analysis.materialization.graph_exchange import VerifiedFixedInput, from_arrow
from marivo.analysis.materialization.graph_execution import PreparedGraph
from marivo.analysis.materialization.graph_protocol import plan_digest
from marivo.analysis.methods import numeric_state
from marivo.analysis.methods.physical import Qualified, ScalarType
from tests.analysis.graph.reduction_fixtures import (
    Carrier,
    OriginalMethod,
    reduction,
    reduction_chain,
    reduction_input,
    reduction_plan,
)


def _measure(
    prepared: PreparedGraph, lowered: LoweredPlan, verified: VerifiedFixedInput, enabled: bool
) -> dict[str, object]:
    with nullcontext() if enabled else patch.object(local, "_reduction_groups", return_value={}):
        local.execute_verified_fixed(prepared, lowered, (verified,))
        samples: list[float] = []
        for _ in range(7):
            start = time.perf_counter()
            result = local.execute_verified_fixed(prepared, lowered, (verified,))
            samples.append((time.perf_counter() - start) * 1000)
            del result
        with (
            patch.object(
                numeric_state, "merge_components", wraps=numeric_state.merge_components
            ) as merges,
            patch.object(
                numeric_state, "finish_original", wraps=numeric_state.finish_original
            ) as finishes,
            patch.object(local, "_original_input", wraps=local._original_input) as inputs,
            patch.object(local, "_index_rows", wraps=local._index_rows) as indexes,
            patch.object(local, "from_arrow", wraps=from_arrow) as exchanges,
        ):
            result = local.execute_verified_fixed(prepared, lowered, (verified,))
            work = {
                "state_merges": merges.call_count,
                "finishes": finishes.call_count,
                "input_consumptions": inputs.call_count,
                "key_indexes": indexes.call_count,
                "exchange_constructions": exchanges.call_count,
                "merged_rows": [len(call.args[0]) for call in merges.call_args_list],
            }
            primary = result.primary.to_pylist()
            parts = [(p.role, p.table.to_pylist()) for p in result.parts]
            del result
        gc.collect()
        previous = pa.default_memory_pool()
        pool = pa.proxy_memory_pool(previous)
        pa.set_memory_pool(pool)
        tracemalloc.start()
        try:
            result = local.execute_verified_fixed(prepared, lowered, (verified,))
            _, python_peak = tracemalloc.get_traced_memory()
            arrow_peak = pool.max_memory()
            del result
        finally:
            tracemalloc.stop()
            pa.set_memory_pool(previous)
        return {
            "samples_ms": samples,
            "median_ms": statistics.median(samples),
            "python_peak_bytes": python_peak,
            "arrow_peak_bytes": arrow_peak,
            **work,
            "primary": primary,
            "parts": parts,
        }


def main() -> None:
    cases: tuple[tuple[OriginalMethod, Carrier, int, int, bool], ...] = (
        ("sum", "int64", 20000, 2, False),
        ("sum", "int64", 20000, 8, False),
        ("mean", "float64", 20000, 2, False),
        ("mean", "float64", 20000, 8, False),
        ("weighted_mean", "int64", 20000, 2, False),
        ("ratio", "decimal", 20000, 2, False),
        ("sum", "int64", 0, 8, False),
        ("sum", "int64", 20000, 2, True),
    )
    measurements: list[dict[str, object]] = []
    for method, carrier, count, length, shared in cases:
        leaf, saved = reduction_input(count, method=method, carrier=carrier)
        root: MethodNode
        if shared:
            middle = reduction(leaf, keep=(0,))
            root = method_node(
                (Edge("current", reduction(middle)), Edge("baseline", reduction(middle))),
                CellDerive(
                    "difference",
                    "cost-difference",
                    "strict",
                    "units",
                    "all",
                    "source.exact_pairing@v1",
                    "source.finite_numeric@v1",
                ),
                value_type=ScalarType("int64"),
            )
        else:
            root = reduction_chain(leaf, length)
        prepared, lowered = reduction_plan(root)
        ordinary = _measure(prepared, lowered, saved, False)
        grouped = _measure(prepared, lowered, saved, True)
        assert ordinary["primary"] == grouped["primary"] and ordinary["parts"] == grouped["parts"]
        expected: int | float | None = (
            0
            if shared
            else None
            if not count
            else count * (count + 1) // 2
            if method == "sum"
            else sum((i + 1) * (i % 5 + 1) for i in range(count))
            / sum(i % 5 + 1 for i in range(count))
            if method == "weighted_mean"
            else sum(range(1, count + 1)) / sum(i % 3 + 1 for i in range(count))
            if method == "mean"
            else sum(range(1, count + 1)) / sum(i % 5 + 1 for i in range(count))
        )
        assert isinstance(grouped["primary"], list)
        assert grouped["primary"][0]["value"] == expected
        measurements.append(
            {
                "method": method,
                "carrier": carrier,
                "rows": count,
                "chain_length": length,
                "shared": shared,
                "definition_fingerprint": root.fingerprint,
                "plan_digest": plan_digest(prepared.admitted),
                "implementations": [
                    stage.stage.implementation.qualification.implementation_id
                    for stage in lowered.stages
                    if isinstance(stage, LoweredLocal)
                    and isinstance(stage.stage.implementation.qualification, Qualified)
                ],
                "independent_value": expected,
                "ordinary": ordinary,
                "grouped": grouped,
            }
        )
    files = (
        "marivo/analysis/methods/numeric_state.py",
        "marivo/analysis/core/local_laws.py",
        "marivo/analysis/materialization/graph_local_execution.py",
        "marivo/analysis/materialization/graph_attribution.py",
        "marivo/analysis/materialization/graph_preparation.py",
        "tests/analysis/graph/reduction_fixtures.py",
        "devtools/fixed_reduction_cost.py",
    )
    print(
        json.dumps(
            {
                "environment": {
                    "platform": platform.platform(),
                    "python": platform.python_version(),
                    "pyarrow": pa.__version__,
                },
                "base": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "sha256": {
                    name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in files
                },
                "warmups": 1,
                "repetitions": 7,
                "measurements": measurements,
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
