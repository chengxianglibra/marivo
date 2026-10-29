# Semantic and analysis proposal review

Read for architecture or public-contract proposals. Apply only checks relevant to
the proposed capability; a design-only proposal need not have runtime evidence.

Before judging individual signatures, draw the intended ownership chain:

```text
physical source and temporal evidence
  -> reusable semantic identity and business meaning
  -> analysis choices and canonical materialization
  -> closed reducers, composition, quality, and evidence
```

Reject a concept placed in two owners or an execution policy embedded in a reusable semantic object. In lifecycle designs, keep normative states and transitions, observed-state decoding, projection-to-model alignment, and replay execution as separate contracts unless the proposal proves they have one lifecycle and identity.

Apply a public-contract economy pass to every constructor, operator, selector, and public row:

1. Ask whether each input supplies independent business meaning. Infer an Entity from a typed field owner, a target from a directed Relationship path, a parent from a nested typed handle, and source identity from a typed artifact. Reject repeated inputs that can disagree with those authorities.
2. Reject empty-path sentinels, `None` sentinels, numeric positions, bare state or step strings, and duplicated `(parent, child)` identity when a closed constructor or typed handle can make invalid states unrepresentable.
3. Expose a policy slot only when at least two business-distinct legal choices exist now. Put fixed v1 behavior in the operator version and document it; do not publish a one-value strategy constructor.
4. Reuse existing core vocabulary and layouts. A new dispatch should not invent synonyms for `time_scope`, `axes`, alignment, attribution modes, or frame columns unless it introduces genuinely new semantics.
5. Give public rows stable business coordinates. Keep ordering ordinals, fingerprints, repeated target definitions, and other derivable plan facts in artifact metadata.

Audit temporal and completeness semantics independently:

- require one temporal owner and define current capture, snapshot selection, change-point history, validity intervals, and historical sampling separately;
- require exact `as_of` instants or an explicit window/grain when results depend on evaluation time; never hide both behind an ambiguous bucket-or-instant field;
- distinguish requested follow-up from proof of coverage;
- treat caller completeness declarations as exact, typed, scoped assumptions, never observed datasource evidence, semantic readiness, or a repair for decoding and integrity errors.

Audit the analysis algebra, not only isolated operators:

- materialize matching or lifecycle state once, then require reducers to consume the canonical artifact without rematching, replaying, or rereading the semantic source;
- define a closed continuation matrix for every artifact shape and matching/source-kind predicate;
- compose families through typed artifacts or subject sets with identity, ownership, coverage, and lineage intact; terminal tables do not re-enter typed analysis;
- require comparison alignment to be mechanically determined or explicitly typed, and require attribution to operate on additive components, reuse established single/joint/hierarchy layouts, reconcile exactly, and avoid causal claims.

After settling the public contract, search the whole proposal for stale names and synchronize examples, row schemas, fingerprints, capability ownership, help, structured repairs, persistence/recovery, evidence, fixtures, delivery slices, and acceptance criteria. When the user explicitly authorizes a clean breaking redesign, reject compatibility aliases and dual-read or migration shapes unless another stated constraint requires them.
