# Marivo Semantic-Layer Validation And Usability Reference

Use this file when the task is to decide whether a semantic model document is usable now, why a
draft is blocked, or what to repair first.

Skip this file if the real task is still creating the first draft from scratch.

## Tool Routing

| Need | Tool |
| --- | --- |
| Read the stored model | `marivo-get_semantic_model` |
| Export stored document JSON | `marivo-export_osi_semantic_models` |
| Validate draft document JSON | `marivo-validate_osi_semantic_models` |
| Inspect live datasource metadata | `marivo-datasource` |

## Inspection Order

1. Confirm the metric contract is approved by the user.
2. Export or read the current document.
3. Validate the draft or exported document.
4. Identify the earliest validation issue by `json_pointer`.
5. Inspect the owning dataset, field, metric, or relationship section.
6. Use datasource browse when the issue concerns physical grounding.
7. Repair the document and revalidate before attempting analysis.

## Common Repair Patterns

- datasource extension missing or wrong: repair the dataset MARIVO extension data
- `dataset.source` no longer matches the live relation: browse schemas and tables, then update the
  relation FQN
- field expression is invalid: repair `field.expression.dialects[]`
- metric refers to missing fields: repair the dataset fields or metric expression
- relationship refers to missing columns: repair the endpoint datasets or relationship definition
- user-approved business rules changed: repair the contract and affected document sections before
  resuming analysis

## Non-Tool Blockers

Treat these as blockers even if document validation passes:

- no user-approved definition for metric grain, population, or time semantics
- user business material conflicts with live metadata or current semantic objects
- the model is technically valid, but the intended reusable metric is still only a draft

## Guardrails

- validation success is not user approval
- technical validity alone is not business approval
- repair physical grounding issues before debugging downstream analysis
- do not switch to session analysis as a workaround for an unapproved or invalid reusable contract
