# Progressive OpenAPI Access

Factum keeps `GET /openapi.json` as the canonical full HTTP contract, but also exposes smaller OpenAPI retrieval surfaces so agents can fetch only the part of the contract they need.

## Retrieval Order

Use this lookup order by default:

1. Read [`README.md`](README.md) to identify the correct API domain and likely path
2. Fetch the minimal OpenAPI fragment needed for the task
3. Fall back to `GET /openapi.json` only when fragment retrieval is insufficient

The fragment endpoints are derived from the same FastAPI-generated schema as `openapi.json`; they are not a second contract source.

## Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /openapi/index` | List all paths, encoded path keys, operations, and component schema names |
| `GET /openapi/paths/{encoded_path}` | Read one OpenAPI path item by unpadded base64url-encoded path |
| `GET /openapi/schemas/{schema_name}` | Read one component schema and optionally expand its dependencies |
| `GET /openapi/fragment` | Read an operation-focused fragment with optional request, response, and schema expansion |

All fragment responses include:

- `revision` in the response body
- `ETag: W/"<revision>"` header
- `X-OpenAPI-Revision: <revision>` header

The `revision` value is a hash of the canonical generated OpenAPI document, so clients can detect when cached fragments may be stale.

## Path Encoding

`GET /openapi/paths/{encoded_path}` uses **unpadded base64url** of the raw OpenAPI path.

Examples:

| Raw path | Encoded path |
|----------|--------------|
| `/sessions` | `L3Nlc3Npb25z` |
| `/sessions/{session_id}/state` | `L3Nlc3Npb25zL3tzZXNzaW9uX2lkfS9zdGF0ZQ` |

## Expansion Semantics

### `expand`

Supported `expand` values:

- `request` — include the operation request body in `/openapi/fragment`
- `response` — include the operation responses in `/openapi/fragment`
- `schemas` — include referenced component schemas

`expand` may be passed either as repeated query params or as a comma-separated list.

### `depth`

`depth` controls recursive expansion of referenced component schemas:

- `0` — do not expand referenced schemas
- `1` — include direct schema refs
- `2+` — continue following refs up to the requested depth

Maximum supported depth is `5`.

## Examples

### Read the index

```bash
curl -s http://localhost:8000/openapi/index | jq .
```

### Read one path item

```bash
curl -s http://localhost:8000/openapi/paths/L3Nlc3Npb25z?expand=schemas\&depth=1 | jq .
```

### Read one component schema

```bash
curl -s http://localhost:8000/openapi/schemas/SessionCreateRequest?depth=1 | jq .
```

### Read one operation fragment

```bash
curl -s "http://localhost:8000/openapi/fragment?path=/sessions&operation=post&expand=request&expand=response&expand=schemas&depth=1" | jq .
```

## When To Use Full `openapi.json`

Use `GET /openapi.json` when:

- you need the complete schema for offline processing
- you need to inspect many unrelated paths at once
- fragment retrieval leaves a field or reference unresolved

Otherwise prefer the progressive endpoints above.
