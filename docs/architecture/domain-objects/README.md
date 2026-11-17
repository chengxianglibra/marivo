# Marivo Domain Language

This guide defines the ubiquitous language for domain-driven development in
Marivo's data analysis domain. Users, requirements, designs, and code can use
these terms to describe the same business meaning and analytical behavior.
Each concept has a preferred English name, a
definition, relationships to other concepts, and examples of its use.

The vocabulary follows the analysis model already used by Marivo. A concept can
be realized by several software components. Its definition describes what it
means in an analytical question and which distinctions an implementation must
preserve.

## Reading the model

| Topic | Questions it answers |
| --- | --- |
| [Business meaning](business-meaning.md) | What exists in the business? What identifies it? What do its attributes and quantities mean? |
| [Data sources](data-sources.md) | Where are facts represented? How do source records acquire business meaning? |
| [Populations and observations](populations-and-observations.md) | Which analytical units are selected? Which contributions enter the calculation? Which coordinates appear in the answer? |
| [Time](time.md) | When does a contribution belong, which historical representation is read, and how are periods formed? |
| [Analysis algebra](analysis-algebra.md) | How are domains, expressions, and results transformed and combined? What information does an operation require? |
| [Results and evidence](results-and-evidence.md) | What does the answer contain? How do we interpret missingness, retain results, and assess the evidence? |

Business meaning establishes Entity identities, Dimensions, Measures, Metrics,
and Relationships. An Observation applies Metrics in an Analysis Context to
produce an Analysis Frame. Populations describe selected coordinate domains.
Contribution rules connect source facts to the calculation. Analysis operations
then transform those domains, definitions, or results with explicit meanings.

## Canonical vocabulary

Use the full term wherever a shorter word could have more than one meaning.
The public names Domain and Grain retain their established meanings below;
qualify their meaning in context when it could be confused with another kind
of domain or grain. Capitalization in this guide identifies a defined concept.
Ordinary prose can use sentence case, and code can follow its language's naming
conventions.

| Preferred term | Definition owner |
| --- | --- |
| Domain, Semantic Model, Semantic Catalog, Semantic Identity, Definition Identity | [Business meaning](business-meaning.md#business-domain-and-semantic-model) |
| Entity, Entity Instance, Entity Identity, Entity Version | [Business identity](business-meaning.md#entity-and-identity) |
| Attribute, Dimension, Time Dimension, Measure, Unit | [Attributes and quantities](business-meaning.md#attributes-and-quantities) |
| Metric, Metric Value, Expression, Additivity | [Computational meaning](business-meaning.md#metric-and-expression) |
| Relationship, Cardinality, Path, Role | [Business relationships](business-meaning.md#relationship-path-and-role) |
| Ontology, Contextual Association, Driver Hypothesis | [Contextual knowledge](business-meaning.md#contextual-knowledge) |
| Datasource, Source, Fact, Source Record, Source Schema, Source Binding, Source Partition | [Data sources](data-sources.md) |
| Source Inspection, Source Acquisition, Acquisition Scope, Discovery Evidence | [Acquisition](data-sources.md#acquisition-and-evidence) |
| Population, Domain Expression, Fixed Population, Member, Membership, Identity Population | [Populations](populations-and-observations.md#population-and-membership) |
| Axis, Coordinate, Coordinate Key, Complete Key, Output Grain, Semantic Binding | [Coordinates](populations-and-observations.md#axis-coordinate-and-grain) |
| Contribution, Contribution Occurrence, Contribution Domain, Allocation | [Contributions](populations-and-observations.md#contribution-and-allocation) |
| Analysis Context, Observation, Output Domain, Grouping, Predicate | [Observations](populations-and-observations.md#observation-and-analysis-context) |
| Sample Unit, Sample Domain, Effective Sample | [Internal samples](populations-and-observations.md#sample-unit-and-sample-domain) |
| Instant, Civil Date, Timezone, Time Scope, Contribution Clock | [Time and clocks](time.md#instants-scopes-and-clocks) |
| Grain, Calendar Period, Calendar Axis, Window, Window Endpoint, Reporting Endpoint | [Periods and windows](time.md#calendar-grain-period-and-window) |
| Membership Time, Attribute Time, Snapshot, Validity Interval | [Historical selection](time.md#membership-time-attribute-time-and-versions) |
| Correspondence, Alignment, Comparison, Baseline, Share, Attribution | [Analysis algebra](analysis-algebra.md#alignment-comparison-and-reference) |
| Reference Value, Weight, Weighted Mean, Correlation, Attribution Target, Attribution Contribution | [Reference and method meaning](analysis-algebra.md#alignment-comparison-and-reference) |
| Data Instance, Admissible Input Domain | [Evaluation](analysis-algebra.md#evaluation-and-admissibility) |
| Reduction, Row Transformation, Pointwise Transformation | [Calculation stages](analysis-algebra.md#reduction-and-calculation-stages) |
| Pullback, Pushforward, Broadcast | [Coordinate operations](analysis-algebra.md#coordinate-operations-on-results) |
| Re-observation, Roll-up, Drill-down, Mergeable Summary, Information Sufficiency | [Follow-up calculations](analysis-algebra.md#re-observation-roll-up-and-information-sufficiency) |
| Analysis Frame, Result Row, Value, Presence, Missing Value | [Result meaning](results-and-evidence.md#analysis-frame-and-values) |
| Analysis Definition, Evaluation, Fixed Result, Persistence, Result Reference | [Result lifecycle](results-and-evidence.md#definition-evaluation-and-retention) |
| Project, Analysis Session | [Investigation context](results-and-evidence.md#definition-evaluation-and-retention) |
| Lineage, Source Read, Method Facts, Coverage, Freshness | [Evidence](results-and-evidence.md#evidence-and-its-scope) |
| Validation, Readiness, Preview, Accuracy, Uncertainty | [Evidence boundaries](results-and-evidence.md#evidence-and-its-scope) |

## Qualifying ambiguous words

| Word | Use the term that states the intended meaning |
| --- | --- |
| Domain | Domain for a business responsibility and naming boundary, Population for a selected coordinate domain, Contribution Domain for occurrences consumed by a calculation, Output Domain for result keys, Admissible Input Domain for inputs on which a calculation is defined. |
| Identity | Entity Identity for a business instance, Semantic Identity for a named model concept, Definition Identity for its captured definition, Result Reference for a particular retained answer. |
| Grain | Entity grain for what one business record represents, Output Grain for what one result row represents, Grain for the calendar unit and width of a bucket. |
| Time | Contribution Clock, Membership Time, Attribute Time, Time Scope, or Evaluation Time, according to the question being answered. |
| Filter | Population selection, contribution filtering, or result filtering. State which object is being restricted. |
| Count | Member count, contribution count, distinct-value count, or result-row count. State what one counted unit represents. |
| Average | State the Sample Unit, Sample Domain, weighting rule, and missing-value rule. |
| Contribution | Qualify source Contribution Occurrences and attribution contributions whenever both appear in an explanation. |
| Binding | Source Binding interprets physical records through a semantic model. Semantic Binding identifies the business meaning of an analytical coordinate. Correspondence declares how separate result coordinates are paired. |

## Using the language in development

A user might ask, “Compare August and July revenue for customers who bought in
August, using their regions as of August 1.” The domain description is precise
when it states that August buyers form one fixed Identity Population, both
Observations restrict revenue contributions to that population, Attribute Time
is August 1, and the Baseline uses July's Time Scope. The Comparison pairs the
corresponding region coordinates.

The design can assign responsibility for selecting that Population, resolving
the historical attributes, observing revenue in each period, and comparing the
results. Code names and behavior should preserve those responsibilities. A
change that reselects July buyers changes the Population and therefore the
question.

When introducing or changing a concept, update its definition on the owning page
and explain its relationship to existing terms. Use the same term in user
descriptions, design decisions, validation criteria, and implementation names.
Detailed execution rules live in the linked architecture contracts. The
[analysis algebra reference](../analysis/algebra-theory.md) supplies the formal
notation and the premises of mathematical laws.
