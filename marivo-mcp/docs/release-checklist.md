# marivo-mcp Release Checklist

Use this before cutting or handing off a releasable `marivo-mcp` build.

## 1. Configuration

- `MARIVO_MODE` is set intentionally, or `auto` behavior is acceptable.
- For remote explicit connection, `MARIVO_BASE_URL` points at the target Marivo HTTP service.
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
  tests/test_marivo_mcp_transport.py \
  tests/test_marivo_mcp_resources.py \
  tests/test_marivo_mcp_inventory.py \
  tests/test_marivo_mcp_smoke.py
```

This covers:

- config loading
- HTTP entrypoint transport resolution and workspace guard behavior
- tool and resource registration
- executable inventory drift checks
- envelope and error-shape stability
- resource canonical mirror behavior
- smoke workflow logic

## 3. Optional Live Smoke

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

Expect the smoke output to confirm:

- resolved target kind and base URL
- workspace root and runtime state for local auto-managed mode
- service connectivity
- OpenAPI discovery
- session creation
- session-state read
- validation error wrapping

For local auto-managed releases, also confirm that `.marivo/runtime.json` is
created or reused in the workspace and that a missing or invalid
`MARIVO_WORKSPACE_ROOT` fails closed with `workspace_root_required`.

## 4. Documentation Sync

- `marivo-mcp/README.md` matches the currently implemented P0/P1 surface.
- `marivo_mcp.inventory` matches the currently registered tools/resources.
- Root `README.md` still reflects Marivo's typed-intent and state/context model.
- `docs/agent-guide.md` still points implementers at the MCP README and inventory
  instead of duplicating implementation details.

## 5. Known-Limitations Review

- Confirm the documented unsupported or unwrapped HTTP contracts are still
  accurate.
- Confirm resources are still canonical mirrors rather than derived evidence.
- Confirm no MCP-only business contract has been introduced by accident.
