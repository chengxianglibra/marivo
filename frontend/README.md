# Marivo Frontend

Independent React console for Marivo UI v1.

The frontend is a human console for the HTTP API. It does not restore the old FastAPI `/ui` or
`/admin` surface, does not assume MCP, does not expose a raw SQL workbench, and does not treat roles
as a service-side security boundary.

## Stack

- React + TypeScript + Vite
- TanStack Query for data access
- Ant Design for dense console UI
- OpenAPI-constrained client typing through `openapi-typescript`
- Vitest and Testing Library for unit/integration checks
- Playwright for browser flow checks

## Setup

```bash
npm install
cp .env.example .env.local
npm run dev
```

By default `VITE_MARIVO_USE_MOCKS=true`, so the console can render without a running Marivo service.
To connect to a real service:

```bash
VITE_MARIVO_USE_MOCKS=false VITE_MARIVO_API_BASE_URL=/api npm run dev
```

## Scripts

```bash
npm run dev
npm run build
npm run typecheck
npm run lint
npm run test
npm run test:browser
MARIVO_OPENAPI_URL=http://localhost:8000/openapi.json npm run openapi:types
```

`npm run openapi:types` writes `src/api/openapi.generated.ts`. The checked-in file is a placeholder
so the UI can build before a Marivo service is available.

## Directory Layout

- `src/api/`: API config, error taxonomy, HTTP client, TanStack Query hooks, OpenAPI placeholder
- `src/components/`: shared readiness, failure, runtime, evidence, empty and diagnostic components
- `src/fixtures/`: mock API responses for local development and tests
- `src/pages/`: Overview, Operations, Semantic Layer, Analysis, API Contract pages
- `src/testing/`: test environment setup
- `tests/e2e/`: Playwright scenarios

## Runtime Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `VITE_MARIVO_API_BASE_URL` | `/api` | HTTP API base URL or Vite proxy prefix. Use `/api` for local dev so Vite proxies requests to Marivo without requiring service-side CORS. |
| `VITE_MARIVO_USE_MOCKS` | `true` | Use deterministic local fixtures instead of network requests |
| `VITE_MARIVO_REQUEST_TIMEOUT_MS` | `15000` | HTTP request timeout |

## v1 Boundaries

- Roles are navigation views only; no real RBAC is implied.
- Jobs and runtime pages are read-only diagnostic surfaces.
- Source, engine, and mapping inventory is managed by the HTTP API, not `marivo.yaml`.
- SQL may appear only as folded provenance detail when the API returns it; it is not a primary UI entry.
- API capability gaps are tracked in the API Contract page and docs instead of being faked in state.
