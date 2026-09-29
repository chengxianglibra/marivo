# R5.2 member and scalar-read evidence

Candidate baseline: `panda` / `75d573e87c6a48aa337c3be170c87baa87d5a15c`.
Initial worktree was clean. This records the pre-commit source-tree candidate, not a
release or installed-wheel acceptance. The manifest identifies changed sources,
independent input/oracle test code, method definitions, dependencies and logs.

## Qualification and independent results

| Cell | Executed cases in `tests/test_analysis_members_r52.py` | Oracle and actual route |
| --- | --- | --- |
| V01 | complete_key_and_four_read_kinds; exact_versions_and_independent_read; duplicate_members_versions_and_no_business_preflight; exact_boundary_null_and_corrupt_subject | Ordered `(tenant, id)` keys `(1,A),(1,B)` survive identical first components. August snapshot matches exactly; the next missing day is empty. September validity instant gives `[30,20]`, its left limit `[10,20]`, a noon left limit `[30,20]`; open-ended B remains. Duplicate/overlapping selected identities reject. DuckDB table and local Parquet via SourceSession/Ibis. |
| V01 | root_projection_reads_one_source_without_distinct | Instrumented physical origin reads contain one root projection and no Distinct/Aggregate. Temporary graph staging tables are distinguished from source-origin reads. No construction-time business uniqueness scan. |
| V01 | complete_subject_set_image_source_and_fixed | Independently constructed three-instance Subject mapping produces the two complete tuples `(1,A),(1,B)`. Real Parquet/Ibis source distinct and artifact_python local set image agree; input validation precedes image construction. This exercises the common graph rule, not a new public multi-root observation capability. |
| V02 | complete_key_and_four_read_kinds; read_path_coverage_null_and_consumed_scope; wrong_role_many_mapping_and_missing_coverage_reject_before_io | Numeric `[10,20]`, Category `[west,east]`, Boolean `[true,false]`, native aware timestamps and dates. Integer Dimensions stay categorical. Independent attribute anchors, complete-key direct/explicit to-one paths, represented Null versus absent mapping. Wrong role/cardinality, missing path, missing coverage and actual multiplicity reject. Unrelated duplicate owner rows outside the consumed domain do not poison a valid read. |
| V02 | temporal_literals_fixed_recovery_and_foreign_predicates; duplicate_members_versions_and_no_business_preflight | Foreign Session predicates, fixed external reads and static invalid inputs reject with source binding blocked; mixed dependencies reject before Run creation in the included `test_public_mixed_fixed_and_live_rejects_before_run` regression. Static rejection leaves Run count unchanged. Naive timestamp predicates reject on source and fixed relations. |
| V10 (R5.2 slice) | cold_typed_read_continuation_without_source; temporal_literals_fixed_recovery_and_foreign_predicates; exact_boundary_null_and_corrupt_subject | New process after source/model deletion blocks Semantic loading, SourceSession and DuckDB, then executes temporal filtering and complete member projection through artifact_python and verifies exact fixed hit. Corrupt Subject payload is rejected; graph publication regression covers missing/invalid parts and receipts. |
| V12 (R5.2 slice) | public surface, agent drift, lazy disclosure, analysis public static/typing, cutover bilingual examples; broad check-agent | Export snapshot, positive/negative typing, native Help reachability/budgets, dynamic contracts and EN/ZH example synchronization. API/Sphinx and Astro site builds. CLI uses the same native Help owner; no independent member/read CLI signature was introduced. |

The combined Runtime run includes existing `test_analysis_dsl_public.py` J1-J4,
R4.5 member preflight and R4.4 common publication/fault regression. Every new
R5.2 fixture case runs for both native table and Parquet, except the standalone
multi-instance graph set-image case (Parquet source plus local fixed).

Qualification is limited to string/int64 identity components, direct-column or
bound row-expression int64/float64 Measures, string/int64/boolean Dimensions,
native date/aware timestamp TimeDimensions and declared native temporal version
axes. Computed Measure source evaluation uses the Semantic/Ibis binder over the
scoped owner rows, with the root and bound dependency fingerprints frozen into
BindProject. Broader temporal
parsing/conversion, grids/folds and numeric Decimal/Duration are not promoted by
these results. See the owning analysis design for the exact rejection boundary.

## Verification record

### Adversarial repair candidate

A subsequent read-only review found that the first implementation rejected a
valid computed `@ms.measure` row expression and gave an unhelpful repair for a
date/datetime predicate mismatch. The repair uses the loaded expression binder
on scoped owner rows, freezes the root and nested `ms.bind` body hashes in the
graph, and retains only evaluated values for fixed continuation. The independent
fixture oracle starts from `[10,20]`, expects `[9,18]` for `amount * 0.9`, and
`[14,23]` after `ms.bind(discounted, rows) + 5`. Both table and Parquet routes
and a new process with Semantic/DuckDB blocked exercise the result. A wrong
datetime literal now reports the supplied value and instructs the user to pass
a date literal. Review repair logs are listed in `commands.json`; the earlier
logs remain as evidence of the initial candidate, not of this repaired one.

`commands.json` records commands and exit status; retained logs are linked there.
The final broad gate includes lint/import boundaries, typing, default tests and
API documentation. Default skips retain their existing status; they are not
Runtime passes. The final Runtime log is a separate execution gate.

An earlier combined Runtime attempt had 78 passes and two timestamp-integrity
failures: `terminal predates admission` and `Session update predates creation`.
The retained failure log is `runtime-clock-failure.log`. Read-only inspection of
the failed Store found admission `2026-09-28T16:44:59.774742Z` followed by terminal
`2026-09-28T16:44:58.099580Z`. This is evidence of backward recorded wall time,
not proof of the external cause. No timestamp integrity check was relaxed.
All six related parameter cases passed on targeted rerun; the entire affected
Runtime scope was then rerun. Earlier development failures (obsolete graph-key
snapshot hashes, bilingual example count and an RST heading underline) were
repaired before final gates. No skip or xfail was added.

## Migration and disclosure boundary

The existing member/read builder, BindProject lowering, physical registrations,
common publication/receipt and fixed executor were extended in place. Replaced
single-key and string-only member/read guards were removed. No parallel executor,
new Store generation, raw SQL business route, or compatibility decoder was added.
The version selector lives under `compiler/` to respect import-layer contracts.

New frozen version/read-family fields are strict v7 snapshot contracts: older
snapshots lacking those fields are rejected, not reconstructed from current
Semantic. This is an intentional breaking change without a compatibility path.
Current snapshots preserve complete identity, versions, path/definition binding,
Cell and Subject receipts. Fixed continuation consumes retained facts only.

M05-M10/M13/M15-M16 legacy observation, time, retained, private Dataset and worker
consumers remain assigned to R5.3-R5.7/R6-R8 in the migration ledger. They are not
ownerless replaced R5.2 routes and were not broadly deleted. R5.7 owns installed
wheel acceptance, reverse consumer scan and full V10/V12 closure. SQLite temporal
and fold debts, multi-root observation, full grouping and numerical reductions
remain open in their named packages. D01-D22 historical debt is not reclassified.

Packaged skills were inspected for member/read-specific signatures or contrary
workflow guidance; no necessary change was found. They and AGENTS.md are unchanged.
No release-check, MinIO, six-backend, real-Agent, push or publication was performed.
