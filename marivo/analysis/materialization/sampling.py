"""The registered DuckDB Entity reservoir fence and bounded realization receipt."""

from __future__ import annotations

import re
from collections.abc import Callable

from marivo.analysis.compiler.nodes import CompiledSampleFence
from marivo.analysis.materialization.contracts import SamplingRealization
from marivo.analysis.materialization.duckdb_statements import (
    reservoir_statement,
    reservoir_validation,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.execution import ExecutionAdapter, Statement
from marivo.analysis.observation.sampling import EntitySamplingPolicy


def admit_sampling(policy: EntitySamplingPolicy) -> None:
    """Admit exact native parameters before any source statement; never coerce them."""
    if (
        type(policy.target_rows) is not int
        or not 1 <= policy.target_rows <= 1_000_000_000
        or (
            policy.seed is not None
            and (type(policy.seed) is not int or not 0 <= policy.seed < 2**31)
        )
    ):
        raise MaterializationError(
            expected="DuckDB reservoir target_rows from 1 to 1000000000 and seed from 0 to 2147483647 or None",
            received="the exact sampling request exceeds the registered engine capability",
            repair="Use a target and seed supported by this Entity sampling implementation.",
            stage="implementation_registration",
        )


def sample_statement(backend: ExecutionAdapter, fence: CompiledSampleFence) -> str:
    admit_sampling(fence.policy)
    if (
        re.fullmatch(r"__mv_sample_[0-9]+", fence.relation_name) is None
        or not fence.identity_columns
    ):
        raise _invalid("an exact declared sampling fence", "invalid compiler sampling handoff")
    if not set(fence.identity_columns).issubset(fence.expression.columns):
        raise _invalid("the complete Entity primary key", "sampling input omits identity fields")
    return reservoir_statement(
        backend.compile(fence.expression),
        fence.relation_name,
        fence.policy.target_rows,
        fence.policy.seed,
    )


def execute_sample(
    backend: ExecutionAdapter,
    fence: CompiledSampleFence,
    *,
    statement: Statement,
    ordinal: int,
    record: Callable[[str, str], None],
    event: Callable[[str], None],
) -> SamplingRealization:
    """Create the already reserved relation once; return scalar identity-validation facts."""
    sql = statement.sql
    record("sampling_fence", sql)
    event("sampling_fence")
    event("source_statement")
    backend.submit(statement)
    validation_sql = reservoir_validation(fence.relation_name, fence.identity_columns)
    record("sampling_validation", validation_sql)
    event("source_statement")
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
