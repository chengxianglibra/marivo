# Evidence and Ledger

## Two Output Levels

- **Fact**: An observed, verifiable piece of evidence (column type, null count,
  sample top values). Facts live on typed Brief fields, not generic lists.
- **Assessment**: A rule-based evaluation of whether authoring can proceed.
  Each `*Brief` carries `issues` and `questions`. Issues have severity
  (`blocker`/`warning`/`info`); questions represent unresolved business
  decisions.

## Brief DTOs

Every `prepare_*` call returns a typed Brief with `status`, `issues`,
`questions`, and kind-specific fact fields. Use `ms.help('<Brief>')` for field
names, types, and descriptions; use `references/object-briefs.md` for status
actions and ladder process.

| DTO | Purpose |
| --- | ------- |
| `DomainBrief` | Domain preparation with reuse matches |
| `EntityBrief` | Entity preparation with table metadata and column profiles |
| `DimensionBrief` | Dimension preparation with value shape |
| `TimeDimensionBrief` | Time dimension preparation with format inference |
| `MetricBrief` | Metric preparation with measure profiles |
| `RelationshipBrief` | Relationship preparation with join-key probe |
| `CrossEntityMetricBrief` | Cross-entity metric preparation with join paths |
| `DerivedMetricBrief` | Derived metric preparation with component facts |
| `VerifyResult` | Post-authoring verification result |
| `AuthoringQuestion` | An unresolved business decision |
| `RegisteredMatch` | An already-registered candidate with match basis |

## Collecting Evidence

Evidence is collected automatically by `prepare_*` APIs. `prepare_entity`
calls `md.inspect_table` and `md.inspect_columns` internally; `prepare_relationship`
calls `md.probe_join_keys` internally. The agent does not need to call these
datasource APIs manually before preparing an object.

For exploratory source inspection outside the prepare cycle, use `md` APIs
directly:

```python
metadata = md.inspect_table("warehouse", md.table("orders", database="sales_mart"))
columns = md.inspect_columns("warehouse", md.table("orders"), columns=("status", "amount"))
```

## AuthoringQuestion Mapping

When the agent cannot answer a question from documented project knowledge, ask
the user through the question tool:

Use `ms.help('AuthoringQuestion')` for the field contract. For the question
tool, combine the prompt and reason into the question body, map the decision
kind to a short header, offer the top evidence-supported options plus free
text, and list the default option first as recommended.

Questions with `readiness_effect="blocks"` must be resolved before authoring.
Advisory questions may proceed on defaults.

## Confirmation Recording

When the agent resolves a blocking `AuthoringQuestion` (from knowledge or user
answer), record the resolution as a decision-ledger confirmation so reruns are
traceable:

```python
project.record_decision(
    decision_kind="entity_primary_key",
    subject="sales.orders",
    chosen="order_id",
    agreement_confidence="high",
    qualifying_sources=("user_confirmation",),
)
```

## Abandon Protocol

When a candidate cannot reach sufficiency -- the user cannot answer a blocking
question, or required evidence is unobtainable -- record abandonment:

```python
project.record_decision(
    decision_kind="authoring_abandoned",
    subject="sales.refund_amount",
    chosen="abandoned",
    agreement_confidence="high",
    qualifying_sources=("user_confirmation",),
    materiality="low",
    blast_radius=0,
)
```

Then skip the object and continue the ladder. Dependents are naturally stopped
by hard gates with structured errors naming the missing prerequisite.

Abandoned candidates appear in `ReadinessReport.abandoned` for transparency. A
later session may re-prepare the same candidate; abandonment is not a permanent
block.

## Auto-Recorded Decisions

On load, Marivo auto-records `metric_decomposition` and
`time_dimension_identity` decisions for authored metrics and time dimensions.
These happen during `verify_object`, replacing the manual "reload after
authoring" rule. The auto-recorded entries satisfy the
`dangerous_decision_recorded` check in `verify_object`.
