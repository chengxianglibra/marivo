# R9.3 full-grid Unknown input decision

Status: implemented on 2026-10-06; bounded native public consumers pass.

The public entry is `members.each(grid).observe(sum_metric, during=grid.window,
complete_during=(scope, ...))`. It accepts up to 64 absolute, aware datetime
intervals inside the original non-partial grid. Their canonical UTC union permits
an incomplete middle bucket with complete buckets on both sides. The declaration
binds the exact receiver, Metric graph and contribution root through this call.

The final producer covers ordinary scalar sums directly as well as existing
Duration sums. Scalar sums avoid an unnecessary quotient just to obtain numeric
Unknown. Duration quotients preserve the same Unknown reason. Physical read
completeness, partial Cells and original additive components remain separate from
business completeness. Uncovered original buckets carry
`Unknown(insufficient_business_coverage)`. Rollup/coarsening is refused.

The owning contract is the Explicit business completeness section of
`docs/specs/analysis/python-analysis-design.md`. Native table/Parquet consumers
verify three forecast refusals, unavailable run splits, original input/grid
retention, partial-state corruption refusal and resource cleanup. Parquet adds
fresh-process recovery with every source file offline. Duration has a separate
native public producer and retained quotient check. Existing empty/Null/Undefined
controls retain their original authorities.

No domain snapshot or database control SQL is introduced. This is bounded R9.3
implementation evidence, not all-profile or R9.7 same-candidate qualification.
