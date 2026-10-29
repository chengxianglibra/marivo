# Runtime performance diagnosis

Read this for slow tests, concurrent timeouts, or an explicitly requested full-suite
investigation. The [fixture skill](../SKILL.md) owns setup and isolation rules.

## Establish a comparable measurement

- State what the target covers: one node, a producer/continuation/cold journey, or the
  complete invocation. A single-hotspot latency target is not a blanket deadline for
  intentional timeout or fresh-process recovery tests.
- Record selected node IDs, pass/fail/skip counts, source revision and relevant dirty
  changes, worker count, and other test invocations sharing the host. Compare unchanged
  inputs and concurrency settings; separate cold construction from warm template reuse.
- Repair functional failures before claiming a healthy-suite speedup. An admission failure
  can skip the expensive path. Report reduced coverage separately from faster execution
  of the same cases, and treat a shared-host before/after run as an observation, not an SLA.
- When other agents are editing the checkout during a long diagnostic run, use an isolated
  source snapshot with required untracked files and Git metadata for tests that inspect
  the revision. Verify the candidate did not change. Ordinary targeted development does
  not require a snapshot or evidence bundle.

For one Runtime case, substitute its real node ID:

```bash
.venv/bin/pytest 'tests/test_file.py::test_case' -m runtime -n 0 --durations=20
```

For a bounded module set, use `-m runtime -n 2 --durations=20` and optionally
`--junitxml=docs/testing/evidence/runtime-profile.xml`. Compare another worker count sequentially on the
same set. For an explicitly requested complete diagnostic, `--maxfail=0 --durations=0`
allows all selected cases to be attempted; keep normal fail-fast defaults during iteration.

## Find the layer that consumes time

- Rank both individual cases and cumulative module costs. Record setup/call/teardown,
  command wall time, and tail latency. Summed parallel case durations are cumulative work,
  not wall time. Small setup time does not rule out expensive preparation inside test bodies.
- Split a slow journey into bootstrap/import, fixture seeding, construction/compilation,
  validation queries, execution/publication, result reads, and cleanup. Count calls as well
  as timing them. Avoid adding overlapping parent/child or nested API timers together.
- Follow descendant workers. Parent SQL timing omits child execution; a parent waiting on
  a reader does not prove that the database query is slow. Measure worker import/startup
  separately from request work before proposing worker lifecycle changes.
- Inspect assertion and evidence helpers for repeated `to_pandas()` calls. Reusing one
  collected DataFrame within a phase avoids repeated full reads. Keep deliberate rereads
  across corruption, isolation, or cold-process boundaries.
- Distinguish repeated compiler/validation work from SQL execution. If compilation dominates,
  more fixture caching or database tuning will not address it. Production batching or import
  changes need their own contract-preserving implementation and focused regression checks.
- Treat resource probes as part of the measurement cost. Avoid frequent subprocess-based
  probes in the hot path. CPU samples can miss short-lived workers; summed RSS may double-count
  shared pages. Sparse low service CPU does not rule out request latency.

## Diagnose concurrency and timeout failures

- Reproduce a failing node serially first. A deterministic placement/type/fixture rejection
  is not a concurrency timeout. Label intentional fault-injection waits separately.
- Budget xdist workers together with nested execution/read processes and simultaneous pytest
  invocations. The Make worker limit is per invocation, not a machine-wide coordinator.
- Compare suite wall time and individual-case latency together before raising worker counts.
  Check process count, CPU, memory pressure, database locks, and external-service cost only
  where applicable. Do not infer resource saturation from wall time alone.
- Preserve worker killability, Session ownership, resource guards, and recovery behavior.
  Removing process separation, adding a shared mutable worker pool, or relaxing deadlines
  needs regression evidence for the affected ownership and failure boundaries.

## Validate the improvement

For every removed or merged case, identify its distinct failure mode, oracle and boundary,
then name the retained owner or the explicit contract retirement. Similar names, shared
line coverage, or equal final values do not establish redundancy. Retain real native,
source-offline, crash and independent-process witnesses where those are the assertion.
Moving a case out of the daily gate changes feedback coverage; report it separately from
speeding up the same selection. Do not relax assertions or classify a setup problem as
extended acceptance merely to meet a timing target.

Rerun the affected behavior and comparable timing scope after the change. For shared fixture
changes, include relevant parallel isolation checks and the daily broad gate. For removed or
migrated cases, check collected IDs and confirm that each unique contract still has an owner.

Keep profile artifacts under the Git-ignored `docs/testing/evidence/` directory. Report
measured improvements, remaining costs and changed coverage explicitly. Reuse valid evidence
during commit preparation rather than restarting a full Runtime run for unchanged files.
