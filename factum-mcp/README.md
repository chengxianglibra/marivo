# factum-mcp

External MCP adapter scaffold for Factum.

This subproject exists to keep the MCP runtime separate from Factum's core HTTP
service. Factum remains HTTP-only. The MCP server is a client-side adapter that
will call the HTTP API in later tasks.

## Current Scope

T2 only provides:

- a standalone Python package
- `stdio` and Streamable HTTP MCP server entrypoints
- environment-driven configuration loading
- placeholder tool and resource registration

It does not yet provide:

- real Factum HTTP transport
- canonical tool result envelopes
- production tool implementations

## Environment

The server reads these environment variables:

- `FACTUM_BASE_URL` (required)
- `FACTUM_API_TOKEN` (optional)
- `FACTUM_MCP_TRANSPORT` (optional, default `stdio`)
- `FACTUM_TIMEOUT_MS` (optional, default `10000`)
- `FACTUM_OPENAPI_CACHE_TTL_SEC` (optional, default `300`)
- `FACTUM_DEFAULT_SOURCE_ID` (optional)
- `FACTUM_MCP_HOST` (optional, default `127.0.0.1`)
- `FACTUM_MCP_PORT` (optional, default `8000`)
- `FACTUM_MCP_STREAMABLE_HTTP_PATH` (optional, default `/mcp`)
- `FACTUM_MCP_STATELESS_HTTP` (optional, default `true`)
- `FACTUM_MCP_JSON_RESPONSE` (optional, default `true`)

Missing or invalid required configuration fails at startup with a clear error.
No implicit fallback base URL is used.

## Install

```bash
cd factum-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Run

```bash
FACTUM_BASE_URL=http://127.0.0.1:8000 factum-mcp
```

The entrypoint starts a local `stdio` MCP server. If the Python MCP SDK is not
installed, startup fails with an explicit dependency error.

Run the Streamable HTTP transport:

```bash
FACTUM_BASE_URL=http://127.0.0.1:8000 factum-mcp-http
```

Or select it via the shared entrypoint:

```bash
FACTUM_BASE_URL=http://127.0.0.1:8000 \
FACTUM_MCP_TRANSPORT=streamable-http \
factum-mcp
```

With the current defaults and the official Python MCP SDK, clients should
connect to `http://127.0.0.1:8000/mcp`.
