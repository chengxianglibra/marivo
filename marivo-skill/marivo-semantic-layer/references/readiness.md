# Marivo Semantic-Layer Readiness Reference

Use this file when the task is to decide **whether a semantic model is usable now**, why it is
blocked, or what to repair first.

Skip this file if the real task is still creating the model from scratch.

## Tool Routing

| Need | Tool |
| --- | --- |
| Read readiness | `marivo-get_semantic_model_readiness` |
| Read the full model | `marivo-get_semantic_model` |
| Inspect one dataset | `marivo-get_dataset` |
| Inspect one metric | `marivo-get_metric` |
| Inspect one relationship | `marivo-get_relationship` |

## Inspection Order

1. confirm the metric contract is approved by the user
2. read `marivo-get_semantic_model_readiness`
3. identify the earliest blocker
4. inspect the owning dataset, metric, or relationship
5. repair the earliest missing dependency
6. re-run readiness before attempting analysis

## Common Repair Patterns

- datasource extension missing or wrong: repair the dataset MARIVO extension data
- `dataset.source` no longer matches the live relation: repair the dataset first
- field expression is invalid: repair `field.expression` before changing downstream metrics
- metric refers to missing fields: repair the dataset fields or metric expression
- relationship refers to missing columns: repair the endpoint datasets or relationship definition
- user-approved business rules changed: repair the contract and the affected semantic objects before
  resuming analysis

## Non-Tool Blockers

Treat these as blockers even if the service-level readiness call looks healthy:

- no user-approved definition for the metric grain, population, or time semantics
- user business material conflicts with the live metadata or current semantic objects
- the model is technically ready, but the intended reusable metric is still only a draft

## Guardrails

- model creation alone is not readiness
- technical readiness alone is not approval readiness
- repair the earliest physical grounding issue before debugging downstream analysis
- do not switch to session analysis as a workaround for an unreadiness problem
