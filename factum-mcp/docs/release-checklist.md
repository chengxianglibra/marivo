# factum-mcp Release Checklist

Use this before cutting or handing off a releasable `factum-mcp` build.

## 1. Configuration

- `FACTUM_BASE_URL` points at the target Factum HTTP service.
- Optional auth is configured through `FACTUM_API_TOKEN` when required.
- Transport settings match the intended deployment mode:
  - `FACTUM_MCP_TRANSPORT`
  - `FACTUM_MCP_HOST`
  - `FACTUM_MCP_PORT`
  - `FACTUM_MCP_STREAMABLE_HTTP_PATH`

## 2. Offline Validation

Run from the repository root:

```bash
.venv/bin/pytest \
  tests/test_factum_mcp_config.py \
  tests/test_factum_mcp_transport.py \
  tests/test_factum_mcp_resources.py \
  tests/test_factum_mcp_inventory.py \
  tests/test_factum_mcp_smoke.py
```

This covers:

- config loading
- tool and resource registration
- executable inventory drift checks
- envelope and error-shape stability
- resource canonical mirror behavior
- smoke workflow logic

## 3. Optional Live Smoke

Run only when a reachable Factum HTTP service is available:

```bash
cd factum-mcp
FACTUM_BASE_URL=http://127.0.0.1:8000 .venv/bin/factum-mcp-smoke
```

Expect the smoke output to confirm:

- service connectivity
- OpenAPI discovery
- session creation
- session-state read
- validation error wrapping

## 4. Documentation Sync

- `factum-mcp/README.md` matches the currently implemented P0/P1 surface.
- `factum_mcp.inventory` matches the currently registered tools/resources.
- Root `README.md` still reflects Factum's typed-intent and state/context model.
- `docs/agent-guide.md` still points implementers at the MCP README and inventory
  instead of duplicating implementation details.

## 5. Known-Limitations Review

- Confirm the documented unsupported or unwrapped HTTP contracts are still
  accurate.
- Confirm resources are still canonical mirrors rather than derived evidence.
- Confirm no MCP-only business contract has been introduced by accident.
