# marivo-mcp Release Checklist

Use this before cutting or handing off a releasable `marivo-mcp` build.

## 1. Configuration

- `MARIVO_MODE` is set intentionally, or `auto` behavior is acceptable.
- For remote explicit connection, `MARIVO_BASE_URL` points at the target Marivo HTTP service.
- For local auto-managed connection, `MARIVO_WORKSPACE_ROOT` points at the workspace root.
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
- tool and resource registration
- executable inventory drift checks
- envelope and error-shape stability
- resource canonical mirror behavior
- smoke workflow logic

## 3. Optional Live Smoke

Run only when a reachable Marivo HTTP service is available:

```bash
cd marivo-mcp
MARIVO_BASE_URL=http://127.0.0.1:8000 .venv/bin/marivo-mcp-smoke
```

Expect the smoke output to confirm:

- service connectivity
- OpenAPI discovery
- session creation
- session-state read
- validation error wrapping

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
