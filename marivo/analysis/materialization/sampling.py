"""The registered DuckDB Entity reservoir fence and bounded realization receipt."""

from __future__ import annotations

import re
from collections.abc import Callable

from ibis.backends.duckdb import Backend
from sqlglot import expressions as sge

from marivo.analysis.compiler.nodes import CompiledSampleFence
from marivo.analysis.materialization.contracts import SamplingRealization
from marivo.analysis.materialization.errors import MaterializationError
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


def sample_statement(backend: Backend, fence: CompiledSampleFence) -> str:
    admit_sampling(fence.policy)
    if (
        re.fullmatch(r"__mv_sample_[0-9]+", fence.relation_name) is None
        or not fence.identity_columns
    ):
        raise _invalid("an exact declared sampling fence", "invalid compiler sampling handoff")
    if not set(fence.identity_columns).issubset(fence.expression.columns):
        raise _invalid("the complete Entity primary key", "sampling input omits identity fields")
    name = sge.to_identifier(fence.relation_name, quoted=True).sql(dialect="duckdb")
    sql = (
        f"CREATE TEMPORARY TABLE {name} AS SELECT * FROM ({backend.compile(fence.expression)}) "
        f"AS __mv_eligible USING SAMPLE reservoir({fence.policy.target_rows} ROWS)"
    )
    if fence.policy.seed is not None:
        sql += f" REPEATABLE({fence.policy.seed})"
    return sql


def execute_sample(
    backend: Backend,
    fence: CompiledSampleFence,
    *,
    ordinal: int,
    record: Callable[[str, str], None],
    event: Callable[[str], None],
) -> SamplingRealization:
    """Create the already reserved relation once; return scalar identity-validation facts."""
    sql = sample_statement(backend, fence)
    record("sampling_fence", sql)
    event("sampling_fence")
    event("source_statement")
    backend.raw_sql(sql)
    names = tuple(
        sge.to_identifier(name, quoted=True).sql(dialect="duckdb")
        for name in fence.identity_columns
    )
    relation = sge.to_identifier(fence.relation_name, quoted=True).sql(dialect="duckdb")
    identity = "struct_pack(" + ", ".join(f"{name} := {name}" for name in names) + ")"
    nulls = " OR ".join(f"{name} IS NULL" for name in names)
    order = ", ".join(names)
    validation_sql = (
        f"SELECT count(*) AS realized_entity_count, "
        f"count(*) - count(DISTINCT {identity}) AS duplicate_keys, "
        f"count(*) FILTER (WHERE {nulls}) AS null_keys, "
        f"sha256(coalesce(string_agg(sha256(to_json({identity})), '' ORDER BY {order}), '')) "
        f"AS membership_digest FROM {relation}"
    )
    record("sampling_validation", validation_sql)
    event("source_statement")
    row: object = backend.raw_sql(validation_sql).fetchone()
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
