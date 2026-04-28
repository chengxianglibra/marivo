# Marivo Governance Reference

Use this file when the task involves **policy enforcement, predicate governance, quality gates, or approval workflows**.

Skip this file if the task is ordinary investigation execution, semantic modeling, or readiness troubleshooting without governance concerns.

This file owns governance-specific surfaces. Global transport and session rules stay in `http-contracts.md`.

## Policies

Policies restrict what analysis work may do.

Common policy families:

- `aggregate_only`
- `field_mask`
- `row_filter`
- `max_rows`

Use policies when execution must be constrained before or during runtime.

## Predicate Governance

Predicates are governed semantic objects. Their usage is validated at creation, activation, and runtime.

Key rules:

- each `predicate.*` must declare at least one `allowed_usage`: `metric_qualifier`, `carrier_row_filter`, `request_scope`, or `governance_policy`
- `predicate_ref` values must start with `predicate.`
- `subject_ref` must reference an `entity.*` or `subject.*`
- v1 predicates only support `time_policy="non_time_only"`; time-based targets are forbidden in predicate expressions
- predicate expressions use a restricted operator set: `eq`, `neq`, `in`, `not_in`, `gt`, `gte`, `lt`, `lte`, `between`, `is_null`, `is_not_null`
- `or` and `not` combinators are out of scope for v1; only `and` conjunctions are supported

### Compiler Pipeline Gates

The compiler pipeline enforces predicate governance through three gates:

1. **Predicate contract gate**: validates that the predicate expression references valid semantic targets and uses allowed operators. Target refs must use allowed prefixes (`dimension.*`, `entity.*`, `key.*`, `enum.*`, etc.) and must not use forbidden prefixes (`time.*`, `metric.*`, `process.*`, `binding.*`, `predicate.*`).

2. **Usage-level gate**: validates that `allowed_usage` values are consistent with the predicate's target refs and the consuming object's expectations. For example, a `metric_qualifier` predicate must target refs that the metric can actually consume.

3. **Scope validation gate**: validates that scope-widening semantics are correct when predicates are composed. Detects conflicting predicates with mixed operators at the same scope level.

### Predicate Lineage

Predicates participate in a lineage system:

- predicate filter lineage extraction surfaces `default_predicate_refs` in metric headers
- per-component qualifier lineage tracks predicate scope fingerprints
- predicate conflict gates detect and report scope-level conflicts with mixed-operator support
- predicate lineage reuse resolution supports `compare` and `test` intents

## Quality Rules

Quality rules assert expectations about freshness and completeness.

Common rule families:

- `freshness`
- `null_rate`
- `row_count_min`

Use quality rules when freshness, completeness, or minimum-data expectations should be checked independently of one specific session.

## Governance Check

Treat governance check as a preflight validation surface for candidate execution.

Use it when you want to know whether an intended execution may be blocked or warned on by active governance rules.

## Approvals

Use approvals when risky recommendations require human review.

Use approvals when:

- an action proposal crosses a risk threshold
- a workflow requires human sign-off before downstream action
- you need to inspect pending approval state for a session

## Read Next

- Read `http-contracts.md` when the question is really about cross-surface request behavior.
- Read `steps.md` when the task is ordinary typed investigation rather than governance control.
- Read `semantic-layer.md` for predicate dependency order and usage category heuristics.
