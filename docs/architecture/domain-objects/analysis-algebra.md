# Analysis Algebra

Analysis algebra describes operations on selected domains, calculation
definitions, and coordinate-indexed results. Every operation has an input
domain, a meaning for its output, and conditions under which that meaning is
valid. Composition must preserve identities, roles, contribution multiplicity,
time interpretation, units, and required information according to each
operation's declared transformation. An equivalence rewrite must also preserve
the question and its admitted inputs.

## Evaluation and admissibility

A **Data Instance** is the actual input state against which definitions are
evaluated. An Analysis Definition combines a Metric expression `m` and Analysis
Context `Γ`. On an admitted Data Instance `I`, evaluation produces values
`q: K → V` over the output keys `K`. The mathematical model holds `I` fixed
when comparing equivalent calculations. In execution, separate source reads
have their own captured facts. A shared definition or observation period alone
establishes no common database snapshot.

The **Admissible Input Domain** contains inputs satisfying the calculation's
type, binding, time, coverage, and method requirements. Evaluation is partial
over all possible inputs. For example, an explicit output domain missing a
required contribution key fails its coverage requirement. A later result filter
cannot erase that requirement from the pending calculation. Operators such as
division explicitly return a missing value for particular inputs, including a
zero denominator, under their declared value policies.

## Population operations

Population operations transform sets of complete keys. Their results retain
the coordinate meanings established by their inputs and explicit bindings.

| Operation | Domain meaning | Example |
| --- | --- | --- |
| **Selection** | Retain members satisfying a predicate on existing coordinates. | Keep customers classified in East. |
| **Projection** | Retain selected axes and deduplicate complete projected keys. | Obtain distinct customers from customer-day pairs. |
| **Classification** | Add single-valued attributes or identity roles to existing tuples while retaining the original coordinates. | Add region to each selected customer's identity. |
| **Union, Intersection, Difference** | Combine compatible coordinate domains using set membership. | Find customers who bought in August and also in July. |
| **Product** | Form complete combinations of disjoint coordinate domains. | Request every selected customer on every selected day. |
| **Image** | Obtain distinct target identities reachable through a declared Path. | Find customers referenced by selected orders. |
| **Preimage** | Retain original members that can reach a selected target through a Path. | Retain orders that reference selected customers. |

Classification followed by projection can collapse identities. Two customers
classified in East remain two customer-region tuples. Projecting to Region
produces one region member. A path's declared cardinality and temporal
interpretation determine which classification is single-valued.

A multi-valued Path has existence-based preimage semantics. If one order
references both campaigns A and B, it belongs to the preimage of each campaign.
The target sets A and B have an empty intersection, whose preimage is empty.
Consequently, combining target sets and combining their preimages require the
appropriate single-valuedness premises. Following an image and a preimage can
also reach additional members sharing the same target.

## Reduction and calculation stages

A **Reduction** combines a collection of inputs into one value according to a
declared rule. Sum, count, mean, minimum, and quantile have different rules and
information requirements. The input collection's meaning is part of the
operation.

| Stage | Input unit | Meaning of a mean |
| --- | --- | --- |
| **Source reduction** | A qualifying Contribution Occurrence. | Average qualifying order amount. |
| **Sample reduction** | A completed child Metric at an internal sample key. | Average customer revenue over the selected customers. |
| **Result reduction** | A represented value or row in an Analysis Frame. | Average of the displayed monthly revenue values. |

A **Row Transformation** computes a value for each contribution before
reduction. A **Pointwise Transformation** computes a new value at each existing
output coordinate after its inputs have been aligned. Transformation order is
part of the formula. The [sample examples](populations-and-observations.md#sample-unit-and-sample-domain)
show how different stages produce different averages.

## Coordinate operations on results

For one result value, write `q: K → V`, where `K` is the represented complete-key
domain and `V` includes the value type and its missing-value policy. A Frame can
carry several such values on the same domain.

| Operation | Meaning |
| --- | --- |
| **Result filtering** | Restrict the represented rows using their existing values or coordinates. |
| **Value projection** | Retain or derive named values while preserving the coordinate domain. |
| **Fixing a coordinate** | Select one member of an eligible axis and remove that now-constant axis. The remaining key still uniquely identifies each selected row. |
| **Ranking and top selection** | Order represented values under a stated ranking rule and retain the selected rows. |
| **Partition reduction** | Partition rows by retained axes and reduce the values within each partition. |
| **Broadcast** | Associate a value defined at a coarser coordinate with each applicable finer coordinate. |

Three mathematical constructions explain many of these operations.

- **Pointwise map** applies a function to corresponding values on one shared
  domain.
- **Pullback** obtains values along a coordinate map. If `r: J → K`, then the
  pulled-back value at `j` is `q(r(j))`. Selection and broadcast use this idea.
- **Pushforward** combines mergeable states along a grouping map. If `h: K → J`,
  each target key receives the merged states of its source keys. The state
  merge has an associative and commutative operation and an empty identity
  state. Finalization produces the value wherever the merged state is valid.
  Mean can merge sum and valid count. Arbitrary completed means require further
  information to recover a mean over their original samples.

For example, computing a region total from customer values is a partition
reduction. Associating that total with each customer in the region is a
broadcast. A later sum over those repeated totals counts one occurrence per
customer. Mathematical maps specify the intended calculation, while the
operation contracts specify which constructions Marivo supports.

## Alignment, comparison, and reference

A **Correspondence** declares which axes and complete coordinates from separate
inputs are to be paired. Its business meaning is supplied by the analysis.
Structural compatibility checks validate the coordinate and time contracts.
The analyst establishes the intended relationship between the business roles.
For example, compatible integer keys can occur in Customer and Product
results, each with its own Semantic Identity. A **Baseline** is the reference
input chosen for a comparison, such as a previous period or a budget.

**Alignment** places values from separate results on a common coordinate
domain under a correspondence. Marivo's result alignment retains the union of
represented coordinates and records each side's Presence. Missing counterparts
therefore remain visible. This union is formed after applying an admitted
correspondence. Time matching
still requires its declared map and endpoint coverage. A legal absent period
and an unmapped required period have separate absence and failure meanings.

A **Comparison** evaluates the relationship between corresponding current and
baseline values. Absolute change is current minus baseline. Marivo expresses
relative change as that difference divided by the absolute baseline value. A
zero baseline gives a missing relative value under the division policy.
Business comparability also depends on Metric meaning, population selection,
time, and units.

A **Reference Value** supplies a denominator or other point of comparison.
A **Share** divides a value by an explicitly chosen total. When the numerator
groups form a complete, disjoint partition of the denominator's contributions,
use the same unit and value policy, and have a nonzero total, the shares sum to
one within the numerical policy. Nonnegative contributions with a positive
total also bound each share between zero and one. Filtering before computing
the denominator changes the reference. For values 1 and 3, the first group's
share of both is 1/4.
Its share of a total formed after selecting only that group is 1.

A **Weight** specifies a sample's role in a weighted calculation. A
**Weighted Mean** divides the sum of value-times-weight by the sum of weights
on the same Effective Sample of valid pairs. With (10, 1) and (missing, 1),
ignoring missing pairs yields 10. Retaining the second weight only in the
denominator would yield 5 and change the calculation. Marivo also admits signed
weights. A zero sum of valid weights yields a missing value, and a range bound
requires nonnegative weights with a positive total. Allocation weights have
their own business rule, including any required per-fact normalization.

**Correlation** measures association between paired values over a common
Effective Sample. The pairing and missing-value rules determine which pairs
enter the statistic. Correlation's domain interpretation includes the unit
represented by each pair.

**Attribution** explains an admitted Metric level or change across finer
business coordinates under a stated method. An **Attribution Target** identifies
the value or change being explained. An **Attribution Contribution** is the
amount assigned to a finer group by that method. Additive decomposition, ratio
decomposition, and Shapley allocation retain their own assumptions and evidence.
Sampled methods also retain their method-specific uncertainty. A causal
interpretation requires additional study design and evidence.

## Re-observation, roll-up, and information sufficiency

**Re-observation** evaluates a Metric under an explicitly supplied context and
source input. It can answer the original business calculation at a different
grain. A **Roll-up** coarsens coordinates while preserving the intended
quantity. A **Drill-down** obtains values on finer coordinates. Both require
the contributions or sufficient retained information for that target.

A **Mergeable Summary** retains information that can be combined across
compatible input partitions and finalized into a value. For an ordinary mean,
sum and valid count can be sufficient under fixed sample and missing-value
rules. The final mean alone loses the relative sample sizes.

**Information Sufficiency** is relative to a particular follow-up question.
Retained information is sufficient when every source situation consistent with
it gives the same answer to that question.

| Retained information | Follow-up question | Additional information needed in general |
| --- | --- | --- |
| Two group means, 10 and 20. | What is the overall mean? | Compatible group sums and valid counts. |
| Daily distinct customer counts. | How many distinct customers were active in the week? | Customer identities or suitable retained union information. |
| Revenue by region. | Which customers made up each region's revenue? | Identities and their actual classifications and contributions. |
| Monthly revenue. | What was revenue on each day? | Daily contributions or an adequate finer result. |

Result reductions answer questions about the represented values. Re-observation
answers the Metric's question with its declared contribution and sample rules.
Calling a result mean “average order amount” requires the input rows and weights
to carry that meaning.

An algebraic rewrite must preserve the admitted input domain as well as the
values, coordinates, multiplicity, units, time interpretation, missingness,
and required checks. Numerical policies also matter when changing evaluation
order. The [formal algebra reference](../analysis/algebra-theory.md) states the
laws and their premises. [Frame operations](../analysis/analysis-frame.md) and
[operator contracts](../analysis/operators-and-frames.md) own supported behavior.
