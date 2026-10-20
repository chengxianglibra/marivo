# Public class surface implementation progress

## Baseline and decisions

- Source baseline: `41a49e0b049b55afd6a15fa83c650ee9a4068051`, 2026-10-21.
- Preserve the existing staged design documents and untracked attribute draft.
- Scope: the 20 primary root-export candidates in
  [the plan](public-class-surface-optimization-plan.md). Keep the three temporal
  kind markers as public annotation types for specific `Ref[K]` signatures.
- Preserve every concrete result class, native type/field Help route, serialized
  representation, fingerprint, source operation and arithmetic implementation.
- Implementation owner: coordinator; stages proceed serially.
- No commit, release, protected instruction or packaged-skill edit is included.

## Work ledger

| Stage | Dependencies | Acceptance | Status |
| --- | --- | --- | --- |
| S0: current consumers and baseline | None | A01, A02 | Complete: 22/31/32/2 non-error root classes; direct construction consumers are tests |
| S1: datasource six exports | S0 | A03, A04, A07 | Implemented; 109 focused tests and 4-module typing passed |
| S2: semantic five exports | S1 | A03, A05, A07 | Implemented; 289 focused tests and 3-target typing passed |
| S3: analysis nine exports | S2 | A03, A06, A07 | Complete: 616 combined surface/analysis tests and 3-target typing passed |
| S4: frozen-source unified acceptance | S1–S3 | A01–A08 | Complete: final unrestricted gate passed; no unresolved P2+ findings |

## Failures and review

- No focused-check failures occurred. Existing pandas FutureWarnings in analysis
  checks were retained in raw logs.
- Unified gate round 1: 5,201 tests passed and one independent export consistency
  test failed. Root bindings had been removed, but the analysis DSL provider still
  advertised nine nested records through its `exports` table. This was an actual
  disclosure migration omission, not a stale test or numerical regression. Remove
  those nine `ExportInput` entries while preserving every TypeInput and binding.
  The original failure remains in `marivo-class-unified-check-round1.log`.
- Independent review round 1: no P2+ findings. The reviewer independently checked
  all 20 absent root bindings, type-object/string Help parity, dataclass field
  Help reachability, concrete union narrowing, current guidance and semantic scope.
  No changes or broad test runs were performed by the reviewer. The independent
  consistency failure subsequently required round 2 review of the export repair;
  the round 1 review did not catch this omission.
- Independent review round 2: no unresolved P2+ findings. The reviewer verified
  exact parity of all 38 analysis provider export names and root names, their
  canonical bindings, all ten retained fact TypeInputs, field Help, and the shared
  show owner. This completes the two formal review rounds.
- Registry repair acceptance: 54 targeted disclosure/export/attribute tests passed.
  Production source was refrozen and the unrestricted gate restarted against the
  repaired snapshot. Original round 1 evidence was preserved.
- During work, HEAD advanced to `4420f7b3a683d72ba8bb3279103d793b7b2234ba`
  through an unrelated commit of the preexisting staged design documents. The
  implementation remained unstaged. Source hashes were compared with the original
  baseline and the current reviewed snapshot; no unrelated source delta was found.

## Final evidence

| Acceptance | Evidence | Status |
| --- | --- | --- |
| A01 | 87 to 67 non-error root classes; all 20 removed bindings absent | Passed |
| A02 | 20 stable type targets, 91 original field targets and exact analysis registry export parity | Passed |
| A03 | Specific temporal/context annotations and method-union narrowing; full 315-target typing | Passed |
| A04 | 109 datasource/scope checks; execution and record definitions unchanged | Passed |
| A05 | 289 semantic authoring, readiness, source-health and Help checks | Passed |
| A06 | Attribute/oracle and persisted fact reading in 616 combined checks; storage and arithmetic source hashes unchanged | Passed |
| A07 | Native Help, Sphinx API build and English/Chinese latest guides aligned; 381 site files verified | Passed |
| A08 | Two formal review rounds complete; 54 repair checks and final unrestricted gate passed | Passed |

Source freeze: 310 production Python modules on HEAD
`4420f7b3a683d72ba8bb3279103d793b7b2234ba` plus this unstaged implementation.
Only nine production files differ from the original baseline: the four root
export modules, three native registry/disclosure owners, and the two rejected-scope messages.
All other production modules, including record definitions, arithmetic, temporal
kernels, provider execution and serialization/persistence implementations, retain
the original hashes. The two scope-validation modules are AST-identical after
normalizing only the changed repair strings.

| Group | Before | After | Removed root classes |
| --- | ---: | ---: | --- |
| Datasource | 22 | 16 | `PhysicalExtent`, `Partitioning`, `ExecutionCapabilities`, `DatasourceFailure`, `PartitionScope`, `UnprunedScope` |
| Semantic | 31 | 26 | `ReadinessInputSummary`, `ReadinessIssue`, `SourceHealthCheckResult`, `PeriodCorrespondence`, `AiContextValue` |
| Analysis | 32 | 23 | `FrameMethodValue`, `FrameMethodCoordinate`, `FrameMethodSupport`, `FrameAdditiveMethod`, `FrameRatioMethod`, `FrameLinearTerm`, `FrameLinearMethod`, `FrameMethodMetric`, `FrameMethodParent` |
| Ontology | 2 | 2 | None |
| Total | 87 | 67 | 20 |

All 20 removed root names are absent from bindings, `dir` and `__all__`.
The existing classes keep their original implementation modules and signatures;
all original type Help identities and 91 original attribute targets resolve.
Twelve of the 20 type pages are byte-identical; eight gain owning-result or
existing-typing-path acquisition guidance. No type target is removed.

`PeriodCalendarKind`, `TemporalSetKind` and `WorkScheduleKind` are retained because
the root markers provide public, specific `Ref[K]` annotation paths. Static
acceptance verifies these paths and `ms.typing.AiContextValue`. Analysis static
acceptance reads nested method variants through their discriminants without a
new private import, cast or ambiguous type.

Focused evidence:

- S1: 109 datasource/scope/surface tests passed; four-module typing passed.
- S2: 289 authoring/report/Help tests passed; three-target typing passed.
- S3: 616 surface, attribute arithmetic/oracle and method-fact persistence/read
  tests passed; three-target typing passed. Existing pandas warnings remain in logs.
- Site content validation: 381 required files verified.
- Current English and Chinese guides, Sphinx API source, owning specs, native
  type guidance and export snapshots are aligned. Packaged skills and historical
  releases are unchanged.

Unified gate round 2: unrestricted `make check-agent` passed (exit 0):
formatting and lint, import boundaries, typing across 315 targets, all 5,202
ordinary tests, and the Sphinx API build. The 763 existing warning emissions
remain in the raw log. The final 310-module source freeze was rechecked after
completion and is unchanged. `git diff --check` passed. A01–A08 are complete;
no P2+ review findings remain unresolved.

No provider Runtime or release acceptance is claimed.
No decrease in the number of result variants or measured agent effort is implied
by removing root imports.

Raw evidence is retained under
`/var/folders/cx/bmfg_x3j0qsfvgsb2v8s2x5m0000gn/T/marivo-class-surface-20261021-ga1pyv6k/`
(`baseline.json`, `type-contracts-before.json`, `contract-parity.json`,
`source-freeze.json`, and the original staged patch). Check logs are
`/private/tmp/marivo-class-s1-tests.log`, `/private/tmp/marivo-class-s2-tests.log`,
`/private/tmp/marivo-class-s3-tests.log`, corresponding `*-typecheck.log` files,
`/private/tmp/marivo-class-registry-repair-tests.log`,
`/private/tmp/marivo-class-unified-check-round1.log` and
`/private/tmp/marivo-class-unified-check-round2.log`.
