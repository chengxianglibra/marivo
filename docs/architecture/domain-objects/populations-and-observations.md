# Populations and Observations

## Population and membership

A **Population** is a typed coordinate domain that evaluates to a finite set of
distinct complete tuples. It can describe customers, regions, customer-day
pairs, or another analytical domain. A **Domain Expression** specifies how to
obtain its keys from explicit inputs and context. A **Fixed Population** retains
the complete keys from one evaluation. Re-evaluating a source-dependent Domain
Expression can select different members when its inputs change.

A **Member** is one complete tuple in a Population. **Membership** is the
condition that a tuple belongs to that set. An **Identity Population** has
exactly one axis, carrying Entity Identity, so each member denotes one instance.
The same customer in two customer-day tuples is two members of the customer-day
Population. Projecting to Customer and deduplicating yields one customer.

Population keys can come from source records, explicit selections, relationships,
or result coordinates with an explicit semantic binding. A referenced identity
establishes which instance is requested. Reading that instance's current or
historical attributes requires the corresponding source representation.

A Population used as a cohort retains its declared selection meaning. “Customers
who bought in August” can be selected once and reused for July revenue. Selecting
“customers who bought in the observed month” separately for July produces a
different Population. A complement such as “customers who never bought” requires
a declared reference Population and sufficient purchase-history coverage.

## Axis, coordinate, and grain

An **Axis** is one component of an analytical coordinate system. Its meaning
includes its role, value type, and any required time interpretation. An axis
can represent an Entity Identity, a Dimension, a calendar period, or an
interval.

A **Coordinate** identifies a position on an axis. Its **Coordinate Key** is the
value or complete tuple that encodes that position. A **Complete Key** combines
the keys of all axes. A Customer axis can have a composite identity key while
still representing one axis.

The **Output Grain** states what one result row represents. For revenue by
region and month, the axes are Region and Month, and one row represents one
region-month pair. Complete keys distinguish result rows. The roles and
interpretations of axes determine how their keys can be used together.

A **Semantic Binding** identifies the Entity or Dimension, Path role, and
version interpretation of a semantic coordinate. Result axes retain their
captured key structure and interpretation. Reusing result keys in a source
question requires an explicit binding to the intended semantic coordinates.
Lineage records their origins, while the new binding declares their use in the
new question. Equal labels and compatible key types alone establish no shared
business identity.

A nullable Dimension can use null as a category in a complete key. Entity
Identity requires every key component to be non-null. Membership compares
complete tuples, matching null categories consistently. Field predicates can
evaluate to unknown when values are missing, and selection retains only true
conditions. Negating such a predicate can still yield unknown, so it can differ
from taking the complement of the selected set.

With zero axes, a domain can contain the single empty tuple, representing a
scalar position, or contain no tuples. A scalar total and an empty result
therefore have distinct domains.

## Contribution and allocation

A **Contribution** is an input participating in a particular reduction. A
**Contribution Occurrence** identifies one participation of that input after
the declared conditions, relationships, time rules, and allocation have been
applied. The **Contribution Domain** is the collection of those occurrences.

Contribution multiplicity is meaningful. Two orders for 10 CNY each contribute
20 CNY even though their amounts are equal. A customer's identity appears once
in an Identity Population while all of that customer's qualifying orders can
contribute to revenue.

**Allocation** specifies how a fact participates in output groups. With
overlapping campaign membership, one order can contribute its full amount to
each of two campaign groups. Its participation in each group is a distinct
occurrence. Recovering overall order revenue requires the original fact union
or other sufficient information. Summing the campaign totals counts both
participations. Fractional allocation additionally requires declared weights
and their business interpretation.

An existence-based membership condition selects a root fact once when any
related complete key qualifies. Allocation determines its participations after
selection. If an order belongs to campaigns A and B, selecting orders that
belong to A can retain that order's participation in both campaign groups.

A Population retains coordinate definitions and selected keys. The allocation
rule is an independent part of the calculation. When one rule supplies related
fields, their actual joint tuples must remain intact. For observed pairs
(campaign A, email) and (campaign B, search), separately combining each field's
values would invent two additional pairs.

Different Metric components can have their own Contribution Domains. Order
revenue and refund amount can be reduced independently and then combined on
compatible output coordinates. Their contribution rules establish the intended
multiplicity of each component.

## Observation and analysis context

An **Analysis Context** supplies the choices needed to interpret and evaluate
an analytical question. These can include population constraints, predicates,
relationship roles, version selections, time scopes, groupings, and an output
domain.

An **Observation** evaluates one or more Metric expressions in that context.
It binds the eligible contributions to output coordinates, applies the
reductions, and composes the values at each coordinate. The result is an
Analysis Frame.

An explicit identity Population is needed when the question selects members.
An ordinary total over qualifying orders can bind its contributions directly.
Omitting a population constraint leaves those contributions eligible, while an
explicitly empty population constraint selects none.

A **Grouping** specifies how contributions are classified onto axes. A
**Predicate** is a condition used to select from a specified domain. For
example, paid status can select order contributions, and positive revenue can
select rows of an already observed result. These conditions act at different
stages of the question.

The **Output Domain** is the set of complete coordinates represented in the
answer. It can be derived from actual contributions or supplied as an explicit
Population. An explicit customer-day domain can include customers with no
orders on a day. The Metric's empty-input rule then determines their values.

The three domains in a question have independent responsibilities.

| Domain | Example | Responsibility |
| --- | --- | --- |
| Selected Population | Customers Ada, Bo, and Cy. | Declares selected analytical units. Its use determines whether it constrains contributions, output, or internal samples. |
| Contribution Domain | Ten qualifying orders placed by Ada and Bo. | Supplies the occurrences consumed by the revenue reduction. |
| Output Domain | One row for each of Ada, Bo, and Cy. | Determines which customer coordinates the answer contains. |

In Marivo, an explicit output domain must cover the required contribution keys.
Restricting contributions to selected members requires its own condition.
Coverage concerns complete joint keys. Merely containing every region and every
date in separate projections can leave required region-date pairs uncovered.
Ordinary grouping uses the keys supported by the observed components. Multiple
components can support different keys, so adding a Metric can enlarge the
result domain.

## Sample unit and sample domain

A **Sample Unit** states what one input to a statistic represents. A **Sample
Domain** specifies which such inputs participate. An **Effective Sample** is
the collection retained under the statistic's validity and missing-value rules.
For paired statistics, it consists of valid pairs under one common pairing rule.

An internal sample calculation first completes a child Metric for each sample
key, then reduces those child values. The sample grain can be finer than the
final output grain. Sampling here describes computational units. Statistical
inference additionally requires a suitable sampling design.

An explicit internal Sample Domain can select a subset and retain selected
samples with empty contributions under the child Metric's empty-input rule.
It is interpreted jointly with each parent output key. A customer can be an
internal sample in several regions. A complete external Output Domain supplies
the requested result keys, while an internal Sample Domain determines the
inputs to its sample reduction. Completing output dates alone supplies no
customer sample grid for each date.

Suppose Ada places one order for 100 CNY, Bo places nine orders for 10 CNY each,
and selected customer Cy places no orders. All amounts are known and qualify
for revenue within the same period.

| Question | Sample and reduction | Answer |
| --- | --- | --- |
| Average order amount | Ten order amounts, averaged. | 19 CNY |
| Revenue per buying customer | Two customer revenue totals, averaged. | 95 CNY |
| Revenue per selected customer | Three customer totals, including Cy's zero, averaged. | 190/3 CNY |
| Mean of buying customers' average order amounts | Ada's 100 CNY and Bo's 10 CNY averages, averaged equally. | 55 CNY |

All four answers can occupy one period-level output coordinate. The Sample Unit
and reduction order explain their different meanings. Member count is three,
contribution count is ten, and a complete customer-level result has three rows.
A period-level total has one result row.

The [Population and observation contract](../analysis/populations-and-observation.md)
owns the operational rules. The [algebra reference](../analysis/algebra-theory.md)
describes domains, contribution occurrences, and nested reductions formally.
