# Business time data and external calendar ownership

Business calendars, holidays, compensatory working days and event schedules are
owned by the harness or an authoritative external data service. Marivo consumes
explicit observation ranges and ordinary semantic fields. It does not certify,
store or resolve business-calendar authority.

## Ordinary business data

Model dates, fiscal period keys, occurrence IDs and working-day states with
ordinary Entity, TimeDimension, Dimension and Relationship declarations.
Analysis trusts declared identity and relationship cardinality; explicit
source_check can validate selected data expectations. No full date-spine audit
is added automatically. Missing working-day rows or NULL states are unknown,
not rest days.

Fiscal grouping aggregates the original Metric contributions by the declared
business dimension. Completed daily means, distinct counts or ratios cannot
generally be summed into a fiscal result.

## Explicit analysis inputs

The harness chooses the authoritative calendar, version, timezone and comparison
policy. It supplies exact ranges through session.time_scope(...) and existing
field or member filters. Non-contiguous working days require explicit membership;
a bounding interval cannot replace that membership. Overlaps and repeated uses
must follow the chosen analysis contract.

Natural report buckets use mv.grain(...) and scope.by(grain). Fiscal cumulative
or holiday-aligned windows require explicit ranges or mappings chosen outside
Marivo; they do not acquire natural Grain semantics from their names. Unsupported
combinations can be decomposed into explicit observations without adding an
implicit calendar service.

[Temporal semantics](../temporal-semantics.md) owns exact boundaries, natural
calendar execution, windows and fixed-result correspondence.

## Persistence

New computations have no calendar certificates, snapshot stores or readiness
dependencies on business calendars. Preview reads bounded source evidence and
does not publish business-time authority.

This is a breaking removal. Historical named-calendar descriptors, saved results
requiring them and executable business-calendar inputs are unsupported. No
compatibility reader, migration or producer replay is provided. Natural-calendar
results retain their current fixed-data and source-free cold-read contracts.
