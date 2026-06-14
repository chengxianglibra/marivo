# Brief Status Actions

Each `project.prepare_*` API returns a typed Brief. The Brief's **fields** are
the contract — read them from the library, not from this file:

```python
import marivo.semantic as ms
ms.help('EntityBrief')          # fields, types, and per-field gloss
ms.help('DomainBrief')          # one per kind: DomainBrief, EntityBrief,
                                # DimensionBrief, TimeDimensionBrief, MetricBrief,
                                # RelationshipBrief, CrossEntityMetricBrief,
                                # DerivedMetricBrief
```

This file covers only the **process**: how to react to a Brief's `status`, and
the order in which to prepare objects.

## Status Actions

Every Brief carries `status`, `issues`, `questions`, and (for most kinds)
`matches`. Branch on `status`:

| Status | Agent action |
| --- | --- |
| `blocked` | Fix the blocker (access, scope, missing prerequisite) or abandon the candidate with `authoring_abandoned`. |
| `needs_input` | Answer blocking `AuthoringQuestion`s from documented project knowledge, or ask the user; record the answer as a ledger confirmation. |
| `sufficient` | Author exactly one semantic object, then call `project.verify_object(ref)`. |

When `matches` is non-empty, the candidate may already be registered — reconcile
before authoring a duplicate.

## Ladder Order

Prepare and author objects in dependency order, one object per cycle:

```
domain -> entity -> dimension -> time_dimension -> metric
       -> relationship -> cross-entity metric -> derived metric
```

For each step, call the matching `project.prepare_*` API, branch on the returned
Brief `status`, author one object, then `verify_object` before advancing. See
`workflow.md` for the end-to-end loop.
