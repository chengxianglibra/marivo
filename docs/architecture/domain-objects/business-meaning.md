# Business Meaning

## Business domain and semantic model

A **Business Domain** is an area of business meaning with an accountable owner,
such as sales, subscription, or fulfilment. It provides a context for naming and
interpreting business concepts.

A **Semantic Model** is the set of governed definitions that connects business
concepts to data and computation. It establishes identity, attributes,
relationships, quantities, and calculations. A **Semantic Catalog** is a
discoverable collection of these definitions within a known scope.

A **Semantic Identity** identifies a named concept by its kind and qualified
name within that scope. A **Definition Identity** identifies the captured
definition consumed for that concept. Its provenance records the declaration's
origin. One Semantic Identity can acquire a changed formula while earlier
results retain the Definition Identity they consumed.

New analysis resolves named inputs within its completed definition scope and
checks the required identity, grain, unit, and time contracts. Captured
definition identities record what was used. An existing result retains its
captured facts as the model evolves.

## Entity and identity

An **Entity** defines a kind of business object or fact at a declared grain.
Customer, Order, and Payment are examples. An **Entity Instance** is one
particular customer, order, or payment.

An **Entity Identity** is the complete, non-null key that consistently identifies
one instance within its Entity. A composite identity includes every required key
component. Identity equality depends on the Entity and the complete key. An
order numbered 42 and a customer numbered 42 have their own identities.

An **Entity Version** is a representation of an instance at a particular point
or period in its history. A customer can retain the same identity while their
region changes. Historical selection determines which version supplies the
attributes needed by an analysis.

The Entity's grain states what one represented record means, such as one order
or one customer's daily profile. Identity and version coordinates together
explain how historical records represent instances. Selecting an identity and
reading its attributes require their own source and temporal context.
An Entity used only as a computation source can omit a declared identity.
Using its instances as an Identity Population requires a declared complete key.

## Attributes and quantities

An **Attribute** describes an Entity Instance in the applicable version context.
Its analytical role determines how it is used.

| Term | Definition | Example |
| --- | --- | --- |
| **Dimension** | An attribute used to classify instances or contributions, or to form conditions. | Customer region or order status. |
| **Time Dimension** | An attribute whose values have an explicit temporal interpretation. | Payment time, order date, or a profile's effective date. |
| **Measure** | A row-level quantity with governed units and aggregation permissions. | An order amount or a delivery duration. |
| **Unit** | The scale and kind in which a quantity is expressed. | CNY, items, or seconds. |

A number can serve as a category, an identity component, or a quantity. The role
is part of its meaning. For example, a customer number identifies a customer,
while an order amount measures money. Currency conversion requires a defined
conversion calculation, including its rate and applicable time.

## Metric and expression

A **Metric** defines a calculation that answers a quantitative business
question under an Analysis Context. Its meaning includes the contributing
facts, reduction rules, units, conditions, time roles, and any internal sample
units. Revenue can be defined as the sum of qualifying paid order amounts.
Average order amount uses the total and count of the same effective order
amounts. If missing amounts are ignored, the denominator counts the valid
amounts. Revenue divided by every qualifying order defines revenue per
qualifying order, including those with missing amounts in its denominator.
Revenue per selected customer uses the selected customer count.

A **Metric Value** is the outcome of evaluating a Metric at a particular
coordinate in a specified context. Its interpretation includes that coordinate
and context as well as the numerical value and unit.

An **Expression** composes values and operations into a calculation. Its stage
matters. A row expression computes on one contribution, a reduction combines
contributions, and a Metric expression combines completed reductions or further
reduces internally observed samples. Adding a fee to every order before summing
charges the fee once per order. Adding it to total revenue charges it once per
output coordinate.

**Additivity** is permission to sum a quantity over specified coordinates while
preserving its intended business meaning. Revenue may be additive over disjoint
orders. Inventory at a point in time may be additive over warehouses while
requiring an endpoint or average rule across dates. A calculation that sums
daily inventory values produces a defined numerical result whose business
interpretation must name that sum.

Unit compatibility and additivity are separate obligations. Multiplying two
row quantities can establish a new unit while its sum permission still
requires a business definition. A ratio's denominator and any allocation
weights likewise retain their own meaning.

Metric identity, unit compatibility, and numerical equality carry different
information. Two metrics can produce the same number for one dataset and still
answer different questions. Each Metric's contribution and reduction definition
establishes what its value means.

## Relationship, path, and role

A **Relationship** is a directed relation between Entity Instances through
declared endpoint keys. A relation can associate one instance with several
targets. An Order can relate to its purchasing Customer.
**Cardinality** describes the multiplicity permitted by that mapping in the
applicable version context. Whether referenced instances actually have source
records is a separate match-coverage question.

A **Path** composes directed Relationships to reach another Entity or one of its
attributes. A **Role** describes how an Entity or attribute participates along
that path. Shipping City and Billing City can contain identical city names and
still describe different roles. An analysis must retain the intended role when
classifying, filtering, or matching coordinates.

When a Relationship can reach several targets, the contribution rule determines
how those targets participate in a calculation. For example, an order with two
campaign tags can participate in both tag groups. Splitting its amount equally
requires an explicit allocation rule. Relationship cardinality alone describes
the mapping's multiplicity.

## Contextual knowledge

An **Ontology** organizes contextual knowledge about semantic concepts. A
**Contextual Association** records why concepts are relevant to one another. A
**Driver Hypothesis** proposes a directional influence worth investigating.
For example, return rate may be proposed as a driver of customer retention.

These statements help formulate questions. Relationship keys establish
executable mappings, observations establish calculated values, and a causal
claim requires an appropriate study design and evidence. Those responsibilities
remain explicit when a hypothesis becomes an investigation.

The [semantic object model](../semantic/semantic-object-model.md) owns the
declaration contracts, and [semantic expressions](../semantic/expressions.md)
owns computational meaning. The [ontology contract](../../api/ontology.rst)
describes the available contextual associations.
