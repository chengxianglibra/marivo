# Analysis Session Redesign

## Context

The current analysis session lifecycle is split across several stores:

- `.marivo/analysis/index.db` stores session rows.
- `.marivo/analysis/active` stores the active session name.
- `.marivo/analysis/sessions/<session_id>/meta.json` repeats lifecycle fields.
- Process state keeps `_CURRENT_SESSION`.
- Jobs, frames, evidence, scripts, and reports are discovered through a mix of
  JSON files, parquet files, and per-session SQLite evidence state.

That split makes the public API harder to explain and the internal lifecycle
harder to reason about. This redesign intentionally ignores compatibility and
migration. Existing local session state may be discarded.

## Goals

- Keep one public session entrypoint for create-or-resume behavior.
- Keep one public probe for the current session.
- Keep one public list operation and one public destructive delete operation.
- Remove archive, rename, create, attach, switch, and active as public or
  semi-public APIs.
- Route tests through the same public session APIs used by agents.
- Make SQLite the source of truth for session lifecycle and artifact/report
  metadata.
- Keep large data and publishable report assets on the file system.
- Persist only session metadata that has real readers; drop write-only
  diagnostic fields.
- Guarantee that store rows never reference missing bytes: bytes are written
  before rows on create, and rows are removed before bytes on delete.
- Move report package materialization and publishing to session-scoped public
  APIs. Existing directory-taking public publish helpers are removed or made
  internal as part of this breaking change.

## Non-goals

- No compatibility shims for old `.marivo/analysis/index.db`,
  `.marivo/analysis/active`, or duplicated `meta.json` lifecycle state.
- No archive or soft-delete state.
- No public rename API. `name` remains the stable idempotency key.
- No pure file-system session database.
- No change to analysis intent semantics beyond session resolution.
- No requirement to merge the per-session evidence `judgment.db` into the new
  store in this phase.

## Public API

The public surface under `mv.session` becomes:

```python
mv.session.get_or_create(name=..., ...)
mv.session.current()
mv.session.list()
mv.session.delete(name)
```

The following names are removed from `mv.session` entirely:

```python
archive
create
attach
switch
active
rename
```

They should not appear in `mv.session.__all__`, `dir(mv.session)`,
`mv.help("session")`, agent skills, examples, or public tests.

The publish surface is also broken intentionally where it accepts arbitrary
package directories. Report package materialization and publishing become
session-scoped operations, described in [Reports](#reports). The exact export
list for `mv.publish` is set during implementation, but directory-taking
entrypoints such as `write_report_artifact(root=...)`,
`materialize_html_adapter(root=...)`, `materialize_mcp_adapter(root=...)`, and
`publish_report_package(package_dir, ...)` must not remain the public path for
new Marivo report packages.

## API Semantics

### `get_or_create`

`get_or_create(name=...)` is the only public create-or-resume operation.

- If `name` does not exist, create a session, mark it current, and return a
  live `Session`.
- If `name` exists, load that session, mark it current, and return a live
  `Session`.
- Repeated calls with the same name are idempotent and return the same session
  id until the session is deleted.
- `question` is written only on initial creation. A different `question`
  passed on resume is ignored; the stored question is not rewritten.
- `backends`, `backend_factory`, and `use_datasources` are runtime attachment
  options. They are not persisted and not part of session identity.
- `default_calendar` is a persisted session setting: written on creation,
  updated when a later call passes an explicit value, and restored when the
  session is resumed without one.
- Supplying both `backends` and `backend_factory` remains an error.
- The current `set_active` parameter is removed (it has no callers).
  `get_or_create` always marks the returned session current.
- `updated_at` reflects the last resume or persisted-setting change.

### `current`

`current()` is the only public current-session probe.

- If the process already has a current session, return it.
- If the process has no current session but the store has an active session id,
  load and return it.
- If the store-active id no longer resolves to a session row, clear the stale
  pointer and return `None`.
- If no current session exists, return `None`.
- Public callers should not need `try/except` for a missing session.

Internal analysis intents that need a session when the caller omits one should
use an internal throwing helper, for example
`marivo.analysis.session._runtime.require_current_session()`.

### `list`

`list()` returns all non-deleted sessions. There is no `include_archived`
argument because archive is removed.

`SessionSummary` should no longer expose lifecycle `state`. Required fields:

- `id`
- `name`
- `question`
- `created_at`
- `updated_at`
- `job_count`
- `frame_count`
- `report_count`

The count fields are part of the contract. They give agents enough context to
choose a session without opening every session directory, and they avoid
reintroducing lifecycle state as a filtering mechanism. Counts are computed
with `COUNT()` over the store rows at list time, not stored as denormalized
counters, so they cannot drift from the registry.

### `delete`

`delete(name)` remains public and destructive.

- Delete the session row and related metadata rows from the SQLite store.
- Delete `.marivo/analysis/sessions/<session_id>/` including frames, scripts,
  reports, job JSON caches, and per-session evidence state.
- If the deleted session is process-current or store-active, clear the current
  pointer. The process-current session's open resources (evidence store,
  cached backends) are closed before the directory is removed, so deletion
  works on platforms that forbid deleting open files.
- Store rows are removed before file-system bytes. If the process is
  interrupted after the row delete and before directory removal, the leftover
  directory is accepted as an unreachable orphan. This phase does not add
  garbage collection.
- Unknown names are a no-op. Delete is a cleanup operation and should be safe in
  repeated maintenance scripts.
- A later `get_or_create(name=...)` after delete creates a new session id.

## Persistence Model

Use a project-level SQLite store as the session lifecycle and metadata source of
truth:

```text
.marivo/analysis/session_store.db
```

Use the file system for large or publishable assets:

```text
.marivo/analysis/sessions/<session_id>/
  frames/<artifact_id>/data.parquet
  frames/<artifact_id>/meta.json
  jobs/<job_id>.json
  scripts/*.py
  reports/<report_id>/
  judgment.db
```

SQLite determines existence, ownership, listing, active session, and
relationships. File paths store bytes. JSON files beside data are caches or
package files, not lifecycle truth.

### Suggested Tables

```text
sessions(
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  question TEXT,
  cwd TEXT NOT NULL,
  default_calendar TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)

runtime_state(
  key TEXT PRIMARY KEY,
  value TEXT
)

artifacts(
  session_id TEXT NOT NULL,
  artifact_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  meta_path TEXT,
  content_hash TEXT,
  created_at TEXT NOT NULL,
  produced_by_job TEXT,
  PRIMARY KEY (session_id, artifact_id)
)

jobs(
  session_id TEXT NOT NULL,
  job_id TEXT NOT NULL,
  intent TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  output_artifact_id TEXT,
  record_path TEXT,
  PRIMARY KEY (session_id, job_id)
)

reports(
  session_id TEXT NOT NULL,
  report_id TEXT NOT NULL,
  package_dir TEXT NOT NULL,
  entrypoint TEXT,
  package_hash TEXT,
  created_at TEXT NOT NULL,
  published_url TEXT,
  PRIMARY KEY (session_id, report_id)
)
```

The exact schema can be adjusted during implementation, but the boundary is
fixed: metadata and relationships live in SQLite; large data and report package
assets live on disk.

Schema conventions:

- `artifacts.path`, `artifacts.meta_path`, `jobs.record_path`, and
  `reports.package_dir` store paths relative to the project root so a project
  checkout can move.
- There is no `project_root` column. The store lives at
  `<project_root>/.marivo/analysis/session_store.db`, so the root is implied
  by the store's own location. `cwd` stays as creation-time information.
- The store opens with WAL mode and a busy timeout, matching the concurrent
  multi-process agent scripts that already share today's `index.db`.

### Persisted Session Metadata

Today `meta.json` also persists `tz`, `previous_tz`, `tz_resolution`,
`tz_warning`, `known_calendars`, and `known_datasources`. `Session.tz` is
re-resolved from the system timezone on every attach and the persisted copy is
never read back; the remaining fields are written but never read. All of them
are removed rather than migrated:

- `tz` stays a runtime attribute resolved at attach time. Runtime timezone
  behavior and frame-level `tz_resolution` metadata are unchanged; only the
  session-meta persistence is dropped. This amends the session-meta
  persistence sentence in
  `docs/specs/analysis/timezone-and-calendar-design.md`.
- `previous_tz`, `tz_resolution`, and `tz_warning` are deleted outright,
  along with the `_ensure_v1_2_meta` upgrade path.
- `known_calendars` and `known_datasources` are deleted, including the
  `Session` constructor parameters and the `_persist_known_datasources` hook
  in observe. Datasource usage stays recoverable from job records.

The only persisted session settings are `question` and `default_calendar`,
both columns on `sessions`.

### Write Ordering

Store rows are the reference entry points, so a row must never point at bytes
that do not exist:

- Create and record flows write file-system bytes first, then insert store
  rows.
- Delete flows remove store rows first, then file-system bytes.
- A crash between the two steps leaves orphan files only. Orphans are
  invisible because discovery goes through the store. They are accepted in this
  phase; no garbage collection or retry cleanup is required.
- Job rows are written once at intent completion, mirroring today's one-shot
  job records. There is no `running` row state.
- Readers go through the store: `session.jobs()`, `session.frame_summaries()`,
  and the existence check behind `session.get_frame(...)` become store
  queries, not directory scans, so listings and `SessionSummary` counts cannot
  disagree with the registry.

## Code Organization

Refactor session code toward:

```text
marivo/analysis/session/
  __init__.py          # public facade: get_or_create/current/list/delete
  _store.py            # SessionStore, SQLite schema/CRUD, SessionSummary
  _runtime.py          # process-current session, require_current_session,
                       # reset_process_state (test hook)
  _layout.py           # PersistenceLayout and frame/job/report file IO
  _load.py             # frame loading by artifact ref
  core.py              # Session object and methods
```

Today's `persistence.py` splits along the bytes/metadata boundary: the path
layout and frame/job/report file IO move to `_layout.py`; everything SQLite
moves to `_store.py`. `SessionSummary` is defined in `_store.py`, and the
`marivo.analysis` top-level re-export moves off
`marivo.analysis.session.attach`.

Intents stop importing `write_job_record` and `write_frame_to_disk` directly,
and the evidence pipeline's own frame/meta writer joins the same boundary:
all of them record results through one internal session helper that applies
the write-ordering rule: bytes first, then the artifact row and job row in one
store transaction.

`attach.py` should not remain as a public or semi-public module. It can exist
temporarily during refactor, but the final state should remove it or make it an
unimported compatibility-free implementation detail. The new `__init__.py` is
a plain module: no callable-module class and no attribute-deletion tricks to
hide submodules.

With archive removed, lifecycle state disappears from the `Session` object as
well: `SessionState`, the `Session.state` property, and
`ensure_session_writable` are deleted. `Session.is_read_only` stays; it
reports backend availability, not lifecycle state.

Internal callers currently importing `active()` should move to:

```python
from marivo.analysis.session._runtime import require_current_session
```

Public and high-level tests should import:

```python
import marivo.analysis as mv
```

and use only `mv.session.get_or_create`, `mv.session.current`,
`mv.session.list`, and `mv.session.delete`.

## Data Flow

### Create or Resume

1. `mv.session.get_or_create(name=...)` opens `SessionStore`.
2. Store looks up `sessions.name`.
3. Missing name creates a session id, writes the session directory first, then
   inserts the session row and active runtime state in one store transaction.
4. Existing name loads the session row.
5. Runtime attachment builds semantic project and backend cache from the current
   call options.
6. `_runtime` records the returned `Session` as process-current.

### Record Intent Result

1. The intent computes the result frame in memory.
2. Frame parquet and `meta.json` are written under `frames/<artifact_id>/`.
3. The job JSON cache is written under `jobs/<job_id>.json`.
4. One store transaction inserts the artifact row and the job row.
5. A crash before step 4 leaves orphan files that no store row references.

### Current Session

1. `mv.session.current()` checks process-current.
2. If absent, store reads `runtime_state["active_session_id"]`.
3. If present, it loads that session by id and records it as process-current.
4. If the id is absent or no longer resolves to a session row, it clears the
   stale pointer and returns `None`.

### Delete

1. Store resolves `name` to `session_id`.
2. If absent, return.
3. Store removes session metadata rows in one transaction.
4. Store clears active id when it points at the deleted session.
5. Runtime clears process-current when it points at the deleted session and
   closes its open resources (evidence store, cached backends).
6. File-system session directory is removed.

SQLite truth is cleared before deleting the directory. Orphan directory cleanup
is not part of this phase; an interrupted delete may leave an unreachable
directory on disk.

## Reports

Report packages remain file-system directories because they are static,
publishable assets with relative links:

```text
.marivo/analysis/sessions/<session_id>/reports/<report_id>/
  index.html
  manifest.json
  datasets/*.json
  scripts/*.py
  adapters/*
```

Today no library code writes under `reports/`; package writers accept a
caller-chosen directory and the session linkage is only a skill convention.
The redesign intentionally breaks that publish API shape and makes the session
location a contract:

- Report materialization is session-scoped. The package directory is always
  `<session_dir>/reports/<report_id>/`, derived by the store. No public
  entrypoint accepts an output directory.
- A session-scoped save entrypoint, `session.save_report(artifact,
  report_id=...)`, writes the package files first, then inserts the `reports`
  row, following the write-ordering rule. The exact signature can be adjusted
  during implementation, but the no-directory-parameter contract is fixed.
- The existing directory-taking package writer becomes internal IO used by
  that entrypoint and leaves the public surface.
- Publishing resolves a registered report by `report_id` within the session
  and records `published_url` back onto the row. Report validation continues
  to operate on the package directory, resolved from the registry instead of
  passed in by the caller.
- The `marivo-upload-report` CLI is out of scope; it remains a generic
  directory uploader with no manifest contract.

The store tracks report registry metadata so agents can list and link reports
without scanning every package directory. The database records package path,
entrypoint, hash, publish URL, and session relationship.

## Error Handling

- Missing current session through public `current()` returns `None`.
- Missing current session through internal `require_current_session()` raises
  `NoActiveSessionError` with guidance to call
  `mv.session.get_or_create(name=...)`.
- Deleted session frame refs should raise `FrameRefNotFound` or
  `CrossSessionFrameError` based on available store metadata.
- Unknown `delete(name)` is a no-op.
- Duplicate create races are handled internally: the unique `sessions.name`
  constraint trips, and `get_or_create` retries as load-and-return. With
  public `create` removed, `DuplicateSessionNameError` has no raiser and is
  deleted.
- `SessionStateError` keeps only the backends/backend_factory conflict case;
  the archived-session cases disappear with archive.

## Testing Plan

Public surface tests:

- `mv.session.__all__` contains only `current`, `delete`, `get_or_create`, and
  `list`.
- `mv.session` has no `archive`, `create`, `attach`, `switch`, `active`, or
  `rename` attribute.
- `mv.help("session")` advertises only the public session functions.
- `tests/test_analysis_imports.py` and help/introspection tests reflect the new
  surface.

Test isolation:

- Process-current state must be reset between tests in the same pytest
  process. The only sanctioned private import is
  `_runtime.reset_process_state()`, called from a single shared autouse
  fixture in `tests/conftest.py`. Individual tests use public session APIs
  only and never import private session modules themselves.

Lifecycle tests:

- `current()` returns `None` before any session exists.
- `get_or_create(name)` creates a session and makes it current.
- Calling `get_or_create(name)` again returns the same id.
- `list()` returns created sessions with no state field and with
  `job_count`/`frame_count`/`report_count` computed from store rows.
- `delete(name)` removes store rows, session directory, and current pointer.
- An interrupted row-first delete may leave a session directory behind; that
  directory is unreachable through public APIs and no GC behavior is tested in
  this phase.
- Calling `delete(name)` twice succeeds.
- Calling `get_or_create(name)` after delete creates a different id.

Integration tests:

- Existing analysis tests use `mv.session.get_or_create(...)`, not
  `marivo.analysis.session.attach`.
- Intent functions that omit `session=` still work through internal
  `require_current_session()`.
- Frame loading and cross-session checks still reject mismatched session ids.
- After any intent completes, every artifact and job row references files that
  exist on disk.
- `session.save_report(...)` materializes under the deterministic
  `reports/<report_id>/` directory, registers the report row, and preserves
  file-system links.
- Directory-taking public publish helpers are removed from the public publish
  surface or made internal; tests that call them directly are rewritten around
  session-scoped report APIs.

Tests deleted with the legacy state they pin:

- `meta.json` lifecycle upgrade cases in
  `tests/test_analysis_session_timezone.py` asserting `previous_tz`,
  `tz_resolution`, and `tz_warning`.
- `known_datasources` persistence tests in `tests/test_analysis_observe.py`
  and `tests/test_analysis_observe_cross_dataset_phase2.py`.
- `known_calendars`/`known_datasources` surface assertions in
  `tests/test_analysis_session_surface.py`.

## Documentation Updates

Update:

- `marivo/analysis/session/__init__.py`
- `marivo/analysis/__init__.py` (re-export `SessionSummary` from the new
  store module instead of `marivo.analysis.session.attach`)
- `marivo/analysis/help.py`
- `marivo/analysis/publish/__init__.py`
- `marivo/analysis/publish/help.py`
- `marivo-skills/marivo-analysis/SKILL.md`
- `marivo-skills/marivo-analysis/references/*.md`
- analysis examples that call `mv.session.active()`
- tests and docs that import `marivo.analysis.session.attach`
- `tests/shared_fixtures.py` (imports `ensure_session_writable` and
  `write_frame_to_disk` from the old `persistence` module)

Remove references to archive, create, attach, switch, active, and rename from
agent-facing docs.

This redesign amends the following committed specs, which must be updated in
the same change:

- `docs/specs/analysis/python-track-evidence-surface.md`: the state layout
  diagram shows the removed `.marivo/analysis/active` file.
- `docs/specs/analysis/timezone-and-calendar-design.md`: the sentence that
  persists `tz`, `tz_resolution`, and `tz_warning` into session meta.

## Success Criteria

- Agents can understand session management from four public functions.
- Session lifecycle state has one source of truth.
- Public tests import no private session modules, except the shared conftest
  fixture that calls the `_runtime` reset hook.
- Persisted session metadata is limited to fields with readers; no write-only
  meta fields survive.
- No store row references missing bytes after an interrupted write; orphan
  files are unreachable through the API. This phase does not require garbage
  collection.
- Report packages are created only at the deterministic per-session location
  and are always registered in the store; no public API accepts a report
  output directory.
- Report links remain stable because report packages stay on disk under the
  session directory.
- Deleting a session fully removes local data and permits recreating the same
  session name with a new id.
