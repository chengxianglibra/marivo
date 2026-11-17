# Data Sources

## Datasource and source

A **Datasource** is a named access context for external data. It identifies
where data can be accessed and the configuration needed to access it. A
**Source** identifies a particular readable relation within that context, such
as an orders table, a view of completed payments, or records supplied by a file
or service.

A **Source Record** is one represented record in that relation. A **Source
Schema** describes its available fields and physical value types. A **Source
Binding** connects those physical representations to the Entity and attribute
definitions that give them business meaning.

For example, a warehouse can expose an order-event source containing several
records per order. The model can define Order Event at that event grain or
derive an Order representation with a declared identity and grain. The chosen
binding determines which records become eligible inputs for a Metric.

## Facts, identity, and representation

A **Fact** is an occurrence or state represented as analytical input at a
declared grain. A payment, a shipment, and a warehouse's inventory at the end of
a day are examples. A Source Record is a physical representation of such
information, interpreted through the Source Binding.

The source supplies evidence about represented facts. The Semantic Model
declares how those facts are interpreted, including identity, units, versions,
and contribution roles. A physical record's position or a coincidentally unique
attribute supplies no business identity declaration.

Two records with equal amounts can represent two distinct orders and therefore
two contributions. Several historical records can represent versions of one
customer. The Entity's grain and identity determine how those cases are
interpreted.

Source analysis relies on the declared identity, cardinality, and version grain.
Those declarations establish the model's assumptions. Evidence that actual
records satisfy them comes from checks with explicit scope. Required operation
and output checks retain their own responsibilities.

A **Source Partition** divides stored records for access or organization. A
date partition can describe ingestion time, event time, or a business snapshot,
depending on the declared meaning. The semantic model establishes which of
these interpretations applies.

## Acquisition and evidence

**Source Inspection** examines available structural metadata, such as fields,
types, and partition information. A **Source Acquisition** reads records under
an explicit scope. **Discovery Evidence** retains the observed metadata,
profiles, coverage, and available values from that inspection or acquisition.

An **Acquisition Scope** states the selected source range and access bounds.
Selecting a partition and retaining at most a thousand records defines a
bounded acquisition. The retained records can answer questions within the
observed scope. Claims about the whole source require corresponding coverage
evidence.

Analysis retains evidence of actual access as Source Reads. Business freshness
and completeness additionally require evidence about the source's represented
business time and required coverage.

The [datasource contracts](../datasource/README.md),
[physical source contracts](../datasource/sources-and-execution.md), and
[discovery contracts](../datasource/discovery.md) define the supported access
and evidence behavior. Result-side evidence is described in
[Results and Evidence](results-and-evidence.md#evidence-and-its-scope).
