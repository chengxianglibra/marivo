# Runtime metric closeout

Use this conditional reference only when a material conclusion depends on a
`MetricFrame` whose `metric_identity.kind` is `runtime_expression`, including a
catalog/runtime comparison whose relevant side has that identity.

Before closeout, inspect the persisted frame and evidence state rather than the
constructor text alone. Disclose, at the level material to the conclusion:

- every governed measure or catalog metric dependency and each aggregate/fold
  choice used by the expression;
- the outer global slice and every branch-local slice that changes numerator,
  denominator, or another recursive branch;
- ratio zero-division policy plus missing-child, present-null, present-zero,
  and zero-denominator counts when they can affect interpretation;
- derived unit/additivity/fold limitations, key-alignment coverage, and any
  partial or unavailable quality/component state;
- that presentation labels are non-authoritative and the computation has not
  acquired a catalog `Ref[metric]`, owner, readiness status, or durable business
  definition;
- the owning analysis session/artifact scope. Do not generalize the runtime
  caliber beyond that scope without an approved semantic-authoring handoff.

For a catalog/runtime comparison, preserve the ordered direction: current is
the subject and baseline is the comparator. Do not describe matching lowered
value semantics as matching semantic authority.

If any of these facts are unavailable, weaken the claim or stop. If the caliber
must become reusable organizational truth, propose the smallest catalog metric
change at closeout and wait for explicit approval before semantic authoring.
