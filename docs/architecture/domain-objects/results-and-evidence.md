# Results and Evidence

## Analysis frame and values

An **Analysis Frame** is an analytical result organized by coordinate axes and
named values, with the interpretation required to read them. A **Result Row**
represents one Complete Key. A **Value** is a quantity or other computed value
at that coordinate. A scalar result has one coordinate with zero axes.

The term Analysis Frame also covers a pending result description whose values
await evaluation. Its structure and explicit dependencies describe the result
to be produced. A completed frame carries the fixed values of its evaluation.

For revenue by customer and month, a row identifies one customer-month pair and
carries its revenue value. A result column contains one named value across the
represented rows. The column's label is a display name. Its meaning comes from
the calculation, units, coordinates, and retained context.

**Presence** states whether a row from an input exists at a coordinate. A
**Missing Value**, commonly represented as null or NA, records the absence of
an ordinary value under the operation's value policy. A present row can contain
a missing value, and a missing aligned row has its own Presence state.

| Situation | Domain interpretation |
| --- | --- |
| A selected customer has no qualifying orders, with complete required input coverage. | The revenue sum is zero at that requested coordinate. |
| A customer has qualifying orders, all with missing amounts. | Revenue is missing under the ordinary sum policy. Contribution count can still be positive. |
| A customer appears only on one side of an alignment. | The other side has an absent row and a false Presence indicator. |
| A zero baseline is used for relative change. | The division policy gives a missing value. |
| A required source read fails. | The requested evaluation has failed. |

These interpretations depend on the operation and evidence available before
the value was produced. An ordinary missing value does not retain every cause
that could have produced it. Count and sum can complete empty inputs to zero,
while an empty mean has a missing value. The detailed rules belong to the
[nullable-value contract](../analysis/operators-and-frames.md#null-empty-and-arithmetic-policies).

## Definition, evaluation, and retention

An **Analysis Definition** describes a question through expressions and context.
**Evaluation** applies that definition to its explicit inputs and produces
complete values. A **Fixed Result** retains the values and coordinates from a
particular completed evaluation. Reusing it preserves those facts even as
source data or business definitions change.

**Persistence** retains a fixed result for later use. A **Result Reference**
identifies that particular saved answer. A **Reopening** recovers its captured
values and context. Obtaining fresh source facts requires a new explicit
evaluation. Evaluation and commit times record different lifecycle events.

A fixed result carries captured interpretation and evidence. A fresh source
question requires explicit source definitions and bindings. Lineage can identify
the consumed definitions, while the saved values remain usable through their
own captured structure.

An **Analysis Session** groups related questions and results within a common
investigation and project scope. A **Project** establishes the shared scope of
model definitions and analytical work. Reusing a definition or continuing an
investigation can produce new results. Exact reuse of an earlier answer relies
on its fixed data and result identity.

A saved result retains the information explicitly captured with it. Whether it
can support a follow-up calculation depends on
[information sufficiency](analysis-algebra.md#re-observation-roll-up-and-information-sufficiency).
Recovering a revenue total and recovering the list of contributing orders have
different information requirements.

## Evidence and its scope

**Evidence** is a captured fact used to support an interpretation or claim.
Its scope and origin determine what conclusion it can support.

| Term | What it establishes |
| --- | --- |
| **Lineage** | The semantic origins of a value and its selection context, with both Semantic Identity and consumed Definition Identity. It records logical dependencies, including those with no executed source read. |
| **Source Read** | Actual source access, including its completion time, captured logical scope, and returned row count when known. |
| **Method Facts** | The targets, coordinates, method, and retained calculation evidence of a completed investigation such as attribution. |
| **Coverage** | Which requested members, periods, source records, or output coordinates are accounted for by a stated acquisition or computation. Name the domain to which the claim applies. |
| **Freshness** | How current the represented business information is relative to the question's required time. |
| **Validation** | A check of an explicit declaration, physical binding, or data expectation within the checked scope. |
| **Readiness** | Static readiness of selected definitions and their physical dependency closures, derived from captured declarations. Current source types, connectivity, and actual numerical behavior require their respective validation or execution evidence. |
| **Preview** | Bounded observed values used to inspect behavior and inputs within a stated scope. |

A complete Output Domain can contain every requested customer-day even when the
source has incomplete order history. A recent Source Read can return old
business data. A passed bounded validation establishes the checked condition
within its scope. The explanation of a result should preserve these evidence
boundaries.

**Accuracy** describes the calculation's exact or approximate method and its
numerical policy. Exact and approximate reducers are distinct choices, and an
exact reducer still follows its declared arithmetic precision. **Uncertainty**
describes variation under an identified model or sampling procedure. An
attribution's permutation sampling error, for example, measures a different
effect from approximation in an underlying distinct count. Its interpretation
must retain those scopes and any changing-input limitations.

An attribution may evaluate a fresh source question while explaining a retained
result. Its original observed value and its newly evaluated total refer to
their respective inputs. Keeping both visible makes data changes interpretable.

The [result metadata contract](../analysis/result-and-method-facts.md),
[result lifecycle contract](../analysis/session-state-and-runtime.md), and
[validation boundaries](../semantic/loading-validation-introspection.md#validation-boundaries)
define the captured facts and their operational scope.
