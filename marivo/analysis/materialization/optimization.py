"""Ordered physical rewrites over qualified Frame and source plans."""

from __future__ import annotations

from typing import TYPE_CHECKING

from marivo.analysis.compiler.frame_filter_fusion import fuse_frame_filters
from marivo.analysis.compiler.frame_select_fusion import fuse_frame_selects
from marivo.analysis.compiler.frame_top import bound_pending_frame_top
from marivo.analysis.materialization import query_key_fusion, query_terminal_fusion, sample_fusion
from marivo.analysis.model.frame_contracts import CheckedStageInputT
from marivo.analysis.model.optimization_rules import SOURCE_KEY_RULE_IDS

if TYPE_CHECKING:
    from collections.abc import Mapping

    from marivo.analysis.compiler.compiled_frame import CompiledFramePlan
    from marivo.analysis.compiler.lowered_query import LoweredQuery
    from marivo.analysis.materialization.prepared_sources import PreparedSources
    from marivo.analysis.model.optimization_policy import OptimizationPolicy


def optimize_lowered_source(
    prepared: PreparedSources,
    lowered: LoweredQuery,
    policy: OptimizationPolicy,
    *,
    include_sample: bool = True,
) -> LoweredQuery:
    """Keep source-sample folding ahead of key and terminal fusion."""
    if include_sample:
        lowered = sample_fusion.fuse_sample_reductions(prepared, lowered)
    if policy.enabled_rules & SOURCE_KEY_RULE_IDS:
        lowered = query_key_fusion.fuse_query_keys(prepared, lowered, policy=policy)
    if policy.allows("source_terminal_fusion"):
        lowered = query_terminal_fusion.fuse_query_terminal(prepared, lowered)
    return lowered


def optimize_compiled_frame(
    compiled: CompiledFramePlan[CheckedStageInputT],
    providers: Mapping[str, str],
    protected_inputs: frozenset[int],
    policy: OptimizationPolicy,
    *,
    include_top: bool = True,
) -> CompiledFramePlan[CheckedStageInputT]:
    """Apply terminal Top before local selection and projection fusion."""
    if include_top and policy.allows("pending_frame_top"):
        compiled = bound_pending_frame_top(compiled, providers)
    if policy.allows("L1"):
        compiled = fuse_frame_filters(compiled, protected_inputs=protected_inputs)
    if policy.allows("L5_select_fusion"):
        compiled = fuse_frame_selects(compiled, protected_inputs=protected_inputs)
    return compiled
