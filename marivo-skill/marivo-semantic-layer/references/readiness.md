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

1. read `marivo-get_semantic_model_readiness`
2. identify the earliest blocker
3. inspect the owning dataset, metric, or relationship
4. repair the earliest missing dependency
5. re-run readiness before attempting analysis

## Common Repair Patterns

- datasource extension missing or wrong: repair the dataset MARIVO extension data
- `dataset.source` no longer matches the live relation: repair the dataset first
- field expression is invalid: repair `field.expression` before changing downstream metrics
- metric refers to missing fields: repair the dataset fields or metric expression
- relationship refers to missing columns: repair the endpoint datasets or relationship definition

## Guardrails

- model creation alone is not readiness
- repair the earliest physical grounding issue before debugging downstream analysis
- do not switch to session analysis as a workaround for an unreadiness problem
