"""DuckDB reservoir eligibility and SQL, owned by the concrete adapter."""

from __future__ import annotations

import re

from marivo.analysis.compiler.nodes import CompiledSampleFence
from marivo.analysis.materialization.duckdb_statements import reservoir_statement
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.execution import ExecutionAdapter
from marivo.analysis.materialization.sampling import _invalid
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


def sample_sql(backend: ExecutionAdapter, fence: CompiledSampleFence) -> str:
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
