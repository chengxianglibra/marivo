# marivo-mcp Release Checklist

Use this before cutting or handing off a releasable `marivo-mcp` build.

## 1. Configuration

- `MARIVO_MODE` is set intentionally, or `auto` behavior is acceptable.
- For remote explicit connection, `MARIVO_BASE_URL` points at the target Marivo HTTP service.
- Remote explicit connection must fail closed when the target is unreachable;
  it must not start or reuse a local runtime.
- For local auto-managed connection, `MARIVO_WORKSPACE_ROOT` points at the workspace root.
- For Streamable HTTP MCP, remote explicit connection is the default release path:
  `MARIVO_MODE=remote` and `MARIVO_BASE_URL` must be set for the HTTP server process.
- For Streamable HTTP MCP local mode, workspace guard must pass before startup:
  the workspace root is explicit, writable, not a system directory, and `marivo
  serve-local` is available.
- Optional auth is configured through `MARIVO_API_TOKEN` when required.
- Transport settings match the intended deployment mode:
  - `MARIVO_MCP_TRANSPORT`
  - `MARIVO_MCP_HOST`
  - `MARIVO_MCP_PORT`
  - `MARIVO_MCP_STREAMABLE_HTTP_PATH`

## 2. Offline Validation

Run from the repository root:

```bash
.venv/bin/pytest \
  tests/test_marivo_mcp_config.py \
  tests/test_marivo_mcp_target_resolution.py \
  tests/test_marivo_mcp_transport.py \
  tests/test_marivo_mcp_resources.py \
  tests/test_marivo_mcp_inventory.py \
  tests/test_marivo_mcp_smoke.py
```

This covers:

- config loading
- remote unreachable fail-closed behavior
- local runtime manifest reuse, bootstrap, and restart decisions
- HTTP entrypoint transport resolution and workspace guard behavior
- tool and resource registration
- executable inventory drift checks
- envelope and error-shape stability
- resource canonical mirror behavior
- smoke workflow logic

## 3. Semantic Entity-Centric Refactor Note

This release contains a breaking semantic object model refactor. Treat semantic
metadata as fresh-init/reset only: initialize a new metadata store or explicitly
reset local metadata before validating this build. Do not expect existing legacy
semantic metadata to be migrated online or smoothed through a compatibility
rollout.

The object model is entity-centric:

- Entity contracts own physical grounding, carrier bindings, and field/time
  refs.
- Metric, process, dimension, predicate, and time examples now reference entity
  fields and semantic refs instead of authoring their own physical locators.
- Legacy object-level binding authority for metric/process objects has been
  deleted from the authoring path; legacy `binding_scope=metric` and
  `binding_scope=process_object` writes must be rejected rather than silently
  converted.
- Documentation examples must use the entity-first shape in `docs/api/semantic.md`,
  `spec/semantic/*`, `marivo-skill/marivo/references/*`, and
  `marivo-mcp/README.md`.

Use repository entrypoints or explicit virtualenv paths for verification, for
example:

```bash
make test
make typecheck
make lint
git diff --check -- marivo-mcp/docs/release-checklist.md
rg -n "fresh-init|reset|binding_scope=metric|binding_scope=process_object|entity-centric|entity-first" \
  marivo-mcp/docs/release-checklist.md docs/api/semantic.md README.md
```

## 4. Optional Live Smoke

Run the path that matches the release target. The smoke command resolves the
Marivo target first, then runs the same minimal HTTP workflow against the
resolved endpoint.

Remote explicit `stdio` MCP, with a reachable Marivo HTTP service:

```bash
cd marivo-mcp
MARIVO_MODE=remote \
MARIVO_BASE_URL=http://127.0.0.1:8000 \
.venv/bin/marivo-mcp-smoke
```

Remote explicit Streamable HTTP MCP:

```bash
cd marivo-mcp
MARIVO_MODE=remote \
MARIVO_BASE_URL=http://127.0.0.1:8000 \
.venv/bin/marivo-mcp-http

MARIVO_MODE=remote \
MARIVO_BASE_URL=http://127.0.0.1:8000 \
.venv/bin/marivo-mcp-smoke
```

Local auto-managed `stdio` MCP:

```bash
cd marivo-mcp
MARIVO_MODE=local \
MARIVO_WORKSPACE_ROOT=/absolute/path/to/workspace \
.venv/bin/marivo-mcp-smoke
```

Fail-closed checks:

```bash
cd marivo-mcp
MARIVO_MODE=remote \
MARIVO_BASE_URL=http://127.0.0.1:9 \
.venv/bin/marivo-mcp-smoke

env -u MARIVO_WORKSPACE_ROOT \
  MARIVO_MODE=local \
  .venv/bin/marivo-mcp-http
```

The first command should fail with `remote_target_unreachable` and must not
create or reuse a local runtime. The second command should fail with
`workspace_root_required` because Streamable HTTP local mode requires an
explicit workspace root.

Expect the smoke output to confirm:

- resolved target kind and base URL
- workspace root and runtime state for local auto-managed mode
- service connectivity
- OpenAPI discovery
- session creation
- session-state read
- validation error wrapping

For local auto-managed releases, also confirm that `.marivo/runtime.json` is
created or reused in the workspace, `marivo runtime status` reports the same
endpoint, `marivo doctor` returns actionable runtime checks, and a missing or
invalid `MARIVO_WORKSPACE_ROOT` fails closed with `workspace_root_required`.

## 5. Documentation Sync

- `marivo-mcp/README.md` matches the currently implemented P0/P1 surface.
- `marivo_mcp.inventory` matches the currently registered tools/resources.
- Tool inventory and schema descriptions frame `marivo-mcp` as an HTTP adapter:
  every tool/resource description should point to the canonical HTTP route and
  payload fields, not to an MCP-owned semantic contract.
- Semantic authoring guidance is entity-first: entity payloads own physical
  grounding and field refs; metric/time/process/dimension/predicate payloads
  reference entity fields and semantic refs rather than adding physical binding
  locators of their own.
- Domain discovery, relationship repair, and compatibility/profile repair map
  to canonical HTTP endpoints such as catalog search, semantic relationship
  tools, and compiler profile tools. They must not be documented as MCP-only
  planning or repair workflows.
- Root `README.md` still reflects Marivo's typed-intent and state/context model.
- `agent-guide.md` stays limited to repository-wide coding and testing rules
  instead of duplicating product usage or MCP implementation details.
- `spec/service/agent-runtime/troubleshooting.zh.md` still
  matches the implemented local runtime, remote fail-closed, and HTTP MCP guard
  behavior.

## 6. Known-Limitations Review

- Confirm the documented unsupported or unwrapped HTTP contracts are still
  accurate.
- Confirm resources are still canonical mirrors rather than derived evidence.
- Confirm no MCP-only business contract has been introduced by accident.
- Confirm no docs or inventory text implies MCP owns separate physical-binding
  semantics, semantic lifecycle semantics, relationship semantics, or compiler
  profile semantics.
