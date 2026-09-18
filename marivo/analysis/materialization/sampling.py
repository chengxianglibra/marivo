"""Shared sampling execution order and typed realization receipt."""

from __future__ import annotations

import re
from collections.abc import Callable

from marivo.analysis.compiler.nodes import CompiledSampleFence
from marivo.analysis.materialization.contracts import SamplingRealization
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.execution import ExecutionAdapter, Statement


def sample_statement(backend: ExecutionAdapter, fence: CompiledSampleFence) -> str:
    """Ask the selected adapter for its exact shared-sample implementation."""
    return backend.sample_sql(fence)


def execute_sample(
    backend: ExecutionAdapter,
    fence: CompiledSampleFence,
    *,
    statement: Statement,
    ordinal: int,
    event: Callable[[str], None],
) -> SamplingRealization:
    """Create the already reserved relation once; return scalar identity-validation facts."""
    event("sampling_fence")
    backend.submit(statement)
    validation_sql = backend.sample_validation_sql(fence)
    row: object = backend.submit(
        backend.statement(validation_sql, role="sampling_validation")
    ).fetchone()
    if not isinstance(row, tuple) or len(row) != 4:
        raise _invalid(
            "one scalar sampling validation receipt", "invalid sampling validation output"
        )
    count, duplicate_count, null_count, member_digest = row
    if (
        type(count) is not int
        or not 0 <= count <= fence.policy.target_rows
        or type(duplicate_count) is not int
        or type(null_count) is not int
        or duplicate_count != 0
        or null_count != 0
        or not isinstance(member_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", member_digest) is None
    ):
        raise _invalid(
            "unique non-null sampled Entity identities within the native target",
            "sampling validation failed",
        )
    event("sampling_validated")
    return SamplingRealization(
        ordinal,
        fence.population_definition_fingerprint,
        fence.target_population_definition_fingerprint,
        fence.policy.target_rows,
        fence.policy.seed,
        count,
        member_digest,
    )


def _invalid(expected: str, received: str) -> MaterializationError:
    return MaterializationError(
        expected=expected,
        received=received,
        repair="Repair the governed Population identity or exact sampling implementation and retry.",
        stage="output_validation",
    )
