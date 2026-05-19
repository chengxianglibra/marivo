# Agent Auditability Trace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the minimal agent-facing session trace contract from `docs/specs/analysis/evidence-engine/agent-auditability-v1.md` without duplicating the canonical session state or proposition context evidence APIs.

**Architecture:** Runtime owns pure trace materialization from existing session, step, and artifact ports. HTTP and MCP expose the same trace dictionary through one new route/tool, while evidence conclusions remain in `SessionStateView` and `PropositionContextView`. The trace view is deliberately shallow: it explains what ran, gives stable artifact handles, emits deterministic per-step summaries, and reports per-step warnings when trace fields are unavailable.

**Tech Stack:** Python, FastAPI, Pydantic v2, FastMCP, Marivo `RuntimePorts`, repository test entrypoints via `make test`.

---

## File Structure

Create:

- `docs/api/session-trace.md`: public contract for `GET /sessions/{session_id}/trace`, `get_session_trace`, warning codes, summary whitelist, and the Agent Workflow Contract.

Modify:

- `marivo/runtime/session.py`: add pure helper functions and `get_session_trace(...)` materializer.
- `marivo/runtime/runtime.py`: add `MarivoRuntime.get_session_trace(...)` facade method.
- `marivo/transports/http/models/session_responses.py`: add Pydantic response models for `SessionTraceView`, `SessionTraceStep`, and `SessionTraceWarning`.
- `marivo/transports/http/models/__init__.py`: export the new response models.
- `marivo/transports/http/sessions.py`: add `GET /sessions/{session_id}/trace`.
- `marivo/transports/mcp/tools/session.py`: add `get_session_trace` MCP tool.
- `docs/api/README.md`: link the new session trace doc.
- `docs/user/marivo-mcp-tools-reference.md`: document the new MCP tool and agent workflow.

Test:

- `tests/runtime/test_runtime_session_ops.py`: runtime materialization tests for sorting, summary whitelist, artifact resolution, warnings, and empty sessions.
- `tests/integration/test_sessions.py`: HTTP route schema, unknown session, and parity with current session root authorization behavior.
- `tests/transports/mcp/test_tool_parity.py`: MCP registration and schema coverage.
- `tests/transports/mcp/test_stdio_mcp_e2e.py`: stdio tool list includes `get_session_trace` and the tool calls runtime with `session_id`.

## Contract Guardrails

- Do not add `GET /sessions/{session_id}/evidence-summary`.
- Do not add `get_analysis_evidence_summary`.
- Do not inline artifact payloads, AOI rows, driver rows, backing findings, assessments, or proposition contexts into trace output.
- Do not normalize every runner's provenance in this phase. Only preserve existing `Step.provenance` and warn when it is absent.
- Do not fail the whole trace because one step cannot resolve an artifact id. Emit a warning on that step.

## Task 1: Runtime Trace Tests

**Files:**
- Modify: `tests/runtime/test_runtime_session_ops.py`

- [ ] **Step 1: Import `Step` and `StepId` in the runtime test file**

Add these imports near the existing session imports:

```python
from marivo.contracts.ids import SessionId, StepId, UserId
from marivo.contracts.session import SessionEvent, SessionState, Step
```

If `SessionId` or `StepId` is already imported on a nearby line, merge imports instead of duplicating them.

- [ ] **Step 2: Extend `_make_runtime` to accept custom step and artifact stores**

Replace the existing helper signature and the two corresponding port arguments with:

```python
def _make_runtime(
    session_store=None,
    step_store=None,
    artifact_store=None,
):
    store = session_store or RecordingSessionStore()
    runtime = MarivoRuntime(
        RuntimePorts(
            model_store=StubModelStore(),
            session_store=store,
            evidence_store=StubEvidenceStore(),
            data_source=StubDataSource(),
            cache_store=StubCacheStore(),
            authz=StubAuthZ(),
            audit_log=StubAuditLog(),
            telemetry=StubTelemetry(),
            runtime_config=StubRuntimeConfig(),
            artifact_store=artifact_store or StubArtifactStore(),
            step_store=step_store or StubStepStore(),
        ),
        StubCoreEngine(),
    )
    return runtime, store
```

- [ ] **Step 3: Add recording stores for trace tests**

Add these helper classes after `StubStepStore`:

```python
class RecordingStepStore(StubStepStore):
    def __init__(self, steps: list[Step]):
        self.steps = steps

    def list_steps(self, session_id):
        return [step for step in self.steps if step.session_id == session_id]


class RecordingArtifactStore(StubArtifactStore):
    def __init__(
        self,
        artifact_ids_by_step: dict[tuple[str, str], str | None] | None = None,
        artifacts_by_session: dict[str, list[dict[str, object]]] | None = None,
        failing_steps: set[tuple[str, str]] | None = None,
    ):
        self.artifact_ids_by_step = artifact_ids_by_step or {}
        self.artifacts_by_session = artifacts_by_session or {}
        self.failing_steps = failing_steps or set()

    def resolve_artifact_id_for_step(self, session_id, step_id):
        key = (str(session_id), str(step_id))
        if key in self.failing_steps:
            raise RuntimeError("artifact index unavailable")
        return self.artifact_ids_by_step.get(key)

    def list_artifacts(self, session_id):
        return self.artifacts_by_session.get(str(session_id), [])
```

- [ ] **Step 4: Add trace fixture builders**

Add these functions after `_make_runtime`:

```python
def _session_created_event(session_id: str = "sess_trace") -> SessionEvent:
    return SessionEvent(
        session_id=SessionId(session_id),
        event_type="session_created",
        timestamp="2026-05-18T00:00:00+00:00",
        payload={"goal": "Explain revenue change"},
        actor=UserId("alice"),
    )


def _step(
    step_id: str,
    *,
    created_at: str,
    result: dict[str, object] | None = None,
    provenance: dict[str, object] | None = None,
    semantic_metadata: dict[str, object] | None = None,
    session_id: str = "sess_trace",
) -> Step:
    return Step(
        step_id=StepId(step_id),
        session_id=SessionId(session_id),
        step_type="observe",
        summary=f"ran {step_id}",
        result=result or {},
        provenance=provenance,
        semantic_metadata=semantic_metadata,
        created_at=created_at,
    )
```

- [ ] **Step 5: Add failing runtime tests**

Add these tests near the runtime status tests:

```python
def test_get_session_trace_returns_empty_trace_for_session_without_steps():
    session_store = RecordingSessionStore()
    session_store.append_event(SessionId("sess_trace"), _session_created_event())
    runtime, _ = _make_runtime(session_store=session_store)

    trace = runtime.get_session_trace(SessionId("sess_trace"))

    assert trace == {
        "session_id": "sess_trace",
        "goal": "Explain revenue change",
        "lifecycle_status": "active",
        "created_at": "2026-05-18T00:00:00+00:00",
        "updated_at": "2026-05-18T00:00:00+00:00",
        "steps": [],
        "artifact_ids": [],
        "schema_version": "session_trace.v1",
    }


def test_get_session_trace_sorts_steps_and_dedupes_artifact_ids():
    session_store = RecordingSessionStore()
    session_store.append_event(SessionId("sess_trace"), _session_created_event())
    step_store = RecordingStepStore(
        [
            _step("step_b", created_at="2026-05-18T00:03:00+00:00"),
            _step("step_a2", created_at="2026-05-18T00:01:00+00:00"),
            _step("step_a1", created_at="2026-05-18T00:01:00+00:00"),
        ]
    )
    artifact_store = RecordingArtifactStore(
        artifact_ids_by_step={
            ("sess_trace", "step_a1"): "art_1",
            ("sess_trace", "step_a2"): "art_1",
            ("sess_trace", "step_b"): "art_2",
        }
    )
    runtime, _ = _make_runtime(
        session_store=session_store,
        step_store=step_store,
        artifact_store=artifact_store,
    )

    trace = runtime.get_session_trace(SessionId("sess_trace"))

    assert [step["step_id"] for step in trace["steps"]] == ["step_a1", "step_a2", "step_b"]
    assert trace["artifact_ids"] == ["art_1", "art_2"]


def test_get_session_trace_prefers_result_artifact_id_over_fallback():
    session_store = RecordingSessionStore()
    session_store.append_event(SessionId("sess_trace"), _session_created_event())
    step_store = RecordingStepStore(
        [
            _step(
                "step_1",
                created_at="2026-05-18T00:01:00+00:00",
                result={"artifact_id": "art_from_result", "row_count": 10},
            )
        ]
    )
    artifact_store = RecordingArtifactStore(
        artifact_ids_by_step={("sess_trace", "step_1"): "art_from_fallback"}
    )
    runtime, _ = _make_runtime(
        session_store=session_store,
        step_store=step_store,
        artifact_store=artifact_store,
    )

    trace = runtime.get_session_trace(SessionId("sess_trace"))

    assert trace["steps"][0]["artifact_id"] == "art_from_result"
    assert trace["artifact_ids"] == ["art_from_result"]


def test_get_session_trace_falls_back_to_artifact_store_and_warns_per_step_on_failure():
    session_store = RecordingSessionStore()
    session_store.append_event(SessionId("sess_trace"), _session_created_event())
    step_store = RecordingStepStore(
        [
            _step("step_ok", created_at="2026-05-18T00:01:00+00:00"),
            _step("step_bad", created_at="2026-05-18T00:02:00+00:00"),
        ]
    )
    artifact_store = RecordingArtifactStore(
        artifact_ids_by_step={("sess_trace", "step_ok"): "art_ok"},
        failing_steps={("sess_trace", "step_bad")},
    )
    runtime, _ = _make_runtime(
        session_store=session_store,
        step_store=step_store,
        artifact_store=artifact_store,
    )

    trace = runtime.get_session_trace(SessionId("sess_trace"))

    assert trace["steps"][0]["artifact_id"] == "art_ok"
    assert trace["steps"][0]["warnings"] == [
        {
            "code": "provenance_missing",
            "message": "Step provenance is unavailable.",
            "field": "provenance",
        }
    ]
    assert trace["steps"][1]["artifact_id"] is None
    assert {
        "code": "artifact_id_unresolved",
        "message": "Artifact id could not be resolved for this step.",
        "field": "artifact_id",
    } in trace["steps"][1]["warnings"]
    assert trace["artifact_ids"] == ["art_ok"]


def test_get_session_trace_output_summary_uses_deterministic_whitelist():
    session_store = RecordingSessionStore()
    session_store.append_event(SessionId("sess_trace"), _session_created_event())
    step_store = RecordingStepStore(
        [
            _step(
                "step_1",
                created_at="2026-05-18T00:01:00+00:00",
                result={
                    "intent_type": "observe",
                    "status": "success",
                    "artifact_type": "observation",
                    "row_count": 3,
                    "candidate_count": 2,
                    "rows": [{"region": "US", "revenue": 100}],
                    "large_payload": {"nested": "value"},
                },
                provenance={"runner": "observe"},
                semantic_metadata={"metric": "revenue"},
            )
        ]
    )
    runtime, _ = _make_runtime(
        session_store=session_store,
        step_store=step_store,
        artifact_store=RecordingArtifactStore(
            artifact_ids_by_step={("sess_trace", "step_1"): "art_1"}
        ),
    )

    trace = runtime.get_session_trace(SessionId("sess_trace"))

    assert trace["steps"][0]["output_summary"] == {
        "intent_type": "observe",
        "status": "success",
        "artifact_type": "observation",
        "row_count": 3,
        "candidate_count": 2,
    }
    assert trace["steps"][0]["warnings"] == []
    assert trace["steps"][0]["provenance"] == {"runner": "observe"}
    assert trace["steps"][0]["semantic_metadata"] == {"metric": "revenue"}


def test_get_session_trace_warns_when_output_summary_is_unavailable():
    session_store = RecordingSessionStore()
    session_store.append_event(SessionId("sess_trace"), _session_created_event())
    step_store = RecordingStepStore(
        [
            _step(
                "step_1",
                created_at="2026-05-18T00:01:00+00:00",
                result={"rows": [{"region": "US"}]},
            )
        ]
    )
    runtime, _ = _make_runtime(session_store=session_store, step_store=step_store)

    trace = runtime.get_session_trace(SessionId("sess_trace"))

    assert trace["steps"][0]["output_summary"] is None
    assert {
        "code": "output_summary_unavailable",
        "message": "No whitelisted scalar output summary fields are available.",
        "field": "output_summary",
    } in trace["steps"][0]["warnings"]
```

- [ ] **Step 6: Run runtime tests and verify they fail for missing runtime method**

Run:

```bash
make test TESTS='tests/runtime/test_runtime_session_ops.py -k session_trace'
```

Expected before implementation:

```text
FAILED ... AttributeError: 'MarivoRuntime' object has no attribute 'get_session_trace'
```

## Task 2: Runtime Trace Implementation

**Files:**
- Modify: `marivo/runtime/session.py`
- Modify: `marivo/runtime/runtime.py`

- [ ] **Step 1: Add trace constants and helper functions**

Add this block in `marivo/runtime/session.py` after `_utcnow_iso()`:

```python
TRACE_OUTPUT_SUMMARY_KEYS = frozenset(
    {
        "intent_type",
        "step_type",
        "artifact_id",
        "status",
        "result_type",
        "artifact_type",
        "artifact_schema_version",
    }
)
TRACE_OUTPUT_SUMMARY_COUNT_KEYS = frozenset(
    {
        "row_count",
        "candidate_count",
        "finding_count",
        "driver_count",
    }
)
```

Add these helper functions after `assert_session_exists(...)`:

```python
def _is_trace_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _artifact_id_for_step(runtime: MarivoRuntime, step: Any) -> tuple[str | None, bool]:
    result = step.result if isinstance(step.result, dict) else {}
    artifact_id = result.get("artifact_id")
    if artifact_id is not None:
        return str(artifact_id), False

    try:
        resolved = runtime.ports.artifact_store.resolve_artifact_id_for_step(
            step.session_id,
            step.step_id,
        )
    except Exception:
        return None, True

    return (str(resolved), False) if resolved is not None else (None, False)


def _output_summary_for_step(step: Any) -> dict[str, Any] | None:
    result = step.result if isinstance(step.result, dict) else {}
    summary: dict[str, Any] = {}
    for key in sorted(TRACE_OUTPUT_SUMMARY_KEYS | TRACE_OUTPUT_SUMMARY_COUNT_KEYS):
        if key in result and _is_trace_scalar(result[key]):
            summary[key] = result[key]
    return summary or None


def _warnings_for_step(
    step: Any,
    *,
    artifact_id: str | None,
    artifact_lookup_failed: bool,
    output_summary: dict[str, Any] | None,
) -> list[dict[str, str | None]]:
    warnings: list[dict[str, str | None]] = []
    if artifact_id is None or artifact_lookup_failed:
        warnings.append(
            {
                "code": "artifact_id_unresolved",
                "message": "Artifact id could not be resolved for this step.",
                "field": "artifact_id",
            }
        )
    if output_summary is None:
        warnings.append(
            {
                "code": "output_summary_unavailable",
                "message": "No whitelisted scalar output summary fields are available.",
                "field": "output_summary",
            }
        )
    if step.provenance is None:
        warnings.append(
            {
                "code": "provenance_missing",
                "message": "Step provenance is unavailable.",
                "field": "provenance",
            }
        )
    if step.semantic_metadata is None:
        warnings.append(
            {
                "code": "semantic_metadata_unavailable",
                "message": "Step semantic metadata is unavailable.",
                "field": "semantic_metadata",
            }
        )
    return warnings
```

- [ ] **Step 2: Add session trace materializer**

Add this function after `get_session(...)`:

```python
def get_session_trace(runtime: MarivoRuntime, session_id: SessionId) -> dict[str, Any]:
    """Return the agent-facing trace view for a session.

    Trace explains execution chronology and lightweight handles only.
    Evidence truth stays in SessionStateView and PropositionContextView.
    """
    state = assert_session_exists(runtime, session_id)
    steps = sorted(
        runtime.ports.step_store.list_steps(session_id),
        key=lambda step: (step.created_at, str(step.step_id)),
    )

    trace_steps: list[dict[str, Any]] = []
    artifact_ids: list[str] = []
    seen_artifact_ids: set[str] = set()
    for step in steps:
        artifact_id, artifact_lookup_failed = _artifact_id_for_step(runtime, step)
        output_summary = _output_summary_for_step(step)
        if artifact_id is not None and artifact_id not in seen_artifact_ids:
            artifact_ids.append(artifact_id)
            seen_artifact_ids.add(artifact_id)
        trace_steps.append(
            {
                "step_id": str(step.step_id),
                "step_type": step.step_type,
                "created_at": step.created_at,
                "summary": step.summary,
                "artifact_id": artifact_id,
                "output_summary": output_summary,
                "provenance": step.provenance,
                "semantic_metadata": step.semantic_metadata,
                "warnings": _warnings_for_step(
                    step,
                    artifact_id=artifact_id,
                    artifact_lookup_failed=artifact_lookup_failed,
                    output_summary=output_summary,
                ),
            }
        )

    return {
        "session_id": str(state.session_id),
        "goal": state.goal,
        "lifecycle_status": state.status,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "steps": trace_steps,
        "artifact_ids": artifact_ids,
        "schema_version": "session_trace.v1",
    }
```

- [ ] **Step 3: Add runtime facade method**

Add this method to `MarivoRuntime` in `marivo/runtime/runtime.py` beside `get_session_state(...)`:

```python
def get_session_trace(self, session_id: SessionId) -> dict[str, Any]:
    """Return the agent-facing session trace view."""
    return session_ops.get_session_trace(self, session_id)
```

- [ ] **Step 4: Run runtime trace tests**

Run:

```bash
make test TESTS='tests/runtime/test_runtime_session_ops.py -k session_trace'
```

Expected:

```text
passed
```

If the exact pytest summary includes deselected tests, that is acceptable as long as the selected `session_trace` tests pass.

## Task 3: HTTP Trace Route and Models

**Files:**
- Modify: `marivo/transports/http/models/session_responses.py`
- Modify: `marivo/transports/http/models/__init__.py`
- Modify: `marivo/transports/http/sessions.py`
- Modify: `tests/integration/test_sessions.py`

- [ ] **Step 1: Add HTTP response models**

Add these models to `marivo/transports/http/models/session_responses.py` after `SessionRuntimeStatusResponse`:

```python
class SessionTraceWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    field: str | None = None


class SessionTraceStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    step_type: str
    created_at: str
    summary: str | None = None
    artifact_id: str | None = None
    output_summary: JsonObject | None = None
    provenance: JsonObject | None = None
    semantic_metadata: JsonObject | None = None
    warnings: list[SessionTraceWarning]


class SessionTraceView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    goal: str | None = None
    lifecycle_status: str
    created_at: str
    updated_at: str
    steps: list[SessionTraceStep]
    artifact_ids: list[str]
    schema_version: str
```

- [ ] **Step 2: Export trace models**

In `marivo/transports/http/models/__init__.py`, add these names to the `from .session_responses import (...)` block:

```python
SessionTraceStep,
SessionTraceView,
SessionTraceWarning,
```

Add the same names to `__all__`.

- [ ] **Step 3: Add HTTP route**

In `marivo/transports/http/sessions.py`, add `SessionTraceView` to the model imports:

```python
SessionTraceView,
```

Add this route after `get_session(...)` and before `get_session_runtime_status(...)`:

```python
@router.get(
    "/sessions/{session_id}/trace",
    response_model=SessionTraceView,
)
def get_session_trace(session_id: str, request: Request) -> SessionTraceView:
    try:
        result = get_services(request).runtime.get_session_trace(SessionId(session_id))
        return SessionTraceView.model_validate(result)
    except (KeyError, NotFoundError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
```

- [ ] **Step 4: Add HTTP integration tests**

In `tests/integration/test_sessions.py`, add these tests to `SessionAPITests`:

```python
    def test_get_session_trace_empty_session(self):
        create_response = self.client.post(
            "/sessions",
            json={"goal": "Explain revenue change"},
        )
        session_id = create_response.json()["session_id"]

        response = self.client.get(f"/sessions/{session_id}/trace")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["session_id"], session_id)
        self.assertEqual(body["goal"], "Explain revenue change")
        self.assertEqual(body["lifecycle_status"], "active")
        self.assertEqual(body["steps"], [])
        self.assertEqual(body["artifact_ids"], [])
        self.assertEqual(body["schema_version"], "session_trace.v1")

    def test_get_session_trace_unknown_session_returns_404(self):
        response = self.client.get("/sessions/sess_missing/trace")

        self.assertEqual(response.status_code, 404)

    def test_get_session_trace_matches_current_session_root_auth_boundary(self):
        create_response = self.client.post(
            "/sessions",
            json={"goal": "Explain revenue change"},
        )
        session_id = create_response.json()["session_id"]
        other_client = TestClient(
            create_app(self.db_path),
            headers={"X-Marivo-User": "other_user"},
        )

        session_response = other_client.get(f"/sessions/{session_id}")
        trace_response = other_client.get(f"/sessions/{session_id}/trace")

        self.assertEqual(trace_response.status_code, session_response.status_code)
```

- [ ] **Step 5: Run HTTP session tests**

Run:

```bash
make test TESTS='tests/integration/test_sessions.py -k trace'
```

Expected:

```text
passed
```

## Task 4: MCP Trace Tool

**Files:**
- Modify: `marivo/transports/mcp/tools/session.py`
- Modify: `tests/transports/mcp/test_tool_parity.py`
- Modify: `tests/transports/mcp/test_stdio_mcp_e2e.py`

- [ ] **Step 1: Add MCP tool**

In `marivo/transports/mcp/tools/session.py`, add this tool after `get_session(...)`:

```python
    @server.tool()  # type: ignore
    async def get_session_trace(session_id: str) -> dict[str, Any]:
        """Read the agent-facing execution trace via GET /sessions/{session_id}/trace; use state/context tools for evidence conclusions."""
        return await call_runtime(runtime.get_session_trace, session_id=session_id)
```

- [ ] **Step 2: Extend `FakeRuntime` in tool parity tests**

In `tests/transports/mcp/test_tool_parity.py`, add this method to `FakeRuntime` near `get_session(...)`:

```python
    def get_session_trace(self, **kw):
        return {"session_id": kw["session_id"], "steps": [], "schema_version": "session_trace.v1"}
```

- [ ] **Step 3: Add MCP schema test**

Add this test near the other session tool tests in `tests/transports/mcp/test_tool_parity.py`:

```python
def test_session_trace_tool_is_registered_in_both_modes() -> None:
    stdio_server = FastMCP("test-stdio")
    http_server = FastMCP("test-http", stateless_http=True, json_response=True)
    register_tools(stdio_server, FakeRuntime(), transport="stdio")
    register_tools(http_server, FakeRuntime(), transport="http")

    for server in (stdio_server, http_server):
        tools = {tool.name: tool for tool in server._tool_manager.list_tools()}
        trace_tool = tools["get_session_trace"]
        assert set(trace_tool.parameters["properties"]) == {"session_id"}
        assert trace_tool.parameters["required"] == ["session_id"]
        assert "execution trace" in trace_tool.description
```

- [ ] **Step 4: Update stdio e2e fake runtime**

In `tests/transports/mcp/test_stdio_mcp_e2e.py`, add this method to `FakeRuntime` near `get_session(...)`:

```python
    def get_session_trace(self, **kw):
        return {"session_id": kw["session_id"], "steps": [], "schema_version": "session_trace.v1"}
```

- [ ] **Step 5: Add `get_session_trace` to the expected stdio tool list**

In `tests/transports/mcp/test_stdio_mcp_e2e.py`, add this entry under the session tools list:

```python
        "get_session_trace",
```

- [ ] **Step 6: Add stdio call-through test**

In `tests/transports/mcp/test_stdio_mcp_e2e.py`, add this async test near the session/context tests:

```python
@pytest.mark.asyncio
async def test_stdio_get_session_trace_calls_runtime() -> None:
    from marivo.transports.mcp.tools import register_tools

    runtime = FakeRuntime()
    server = _make_server("marivo")
    register_tools(server, runtime, transport="stdio")
    tools = {t.name: t for t in server._tool_manager.list_tools()}

    result = await tools["get_session_trace"].run({"session_id": "sess_trace"})

    assert result == {
        "data": {
            "session_id": "sess_trace",
            "steps": [],
            "schema_version": "session_trace.v1",
        },
        "error": None,
    }
```

- [ ] **Step 7: Run MCP tests**

Run:

```bash
make test TESTS='tests/transports/mcp/test_tool_parity.py -k session_trace'
make test TESTS='tests/transports/mcp/test_stdio_mcp_e2e.py -k "stdio_server_registers_tools or get_session_trace"'
```

Expected for both commands:

```text
passed
```

## Task 5: Documentation

**Files:**
- Create: `docs/api/session-trace.md`
- Modify: `docs/api/README.md`
- Modify: `docs/user/marivo-mcp-tools-reference.md`

- [ ] **Step 1: Create API documentation**

Create `docs/api/session-trace.md` with:

````markdown
# Session Trace

Session trace is the agent-facing execution chronology for one analysis session.
It answers "what ran?" and deliberately does not answer "what should I conclude?"

Use `GET /sessions/{session_id}/trace` or the MCP `get_session_trace` tool to inspect steps, stable artifact handles, lightweight deterministic summaries, provenance, semantic metadata, and per-step trace warnings.

## HTTP

```http
GET /sessions/{session_id}/trace
```

Response schema:

```json
{
  "session_id": "sess_123",
  "goal": "Explain revenue change",
  "lifecycle_status": "active",
  "created_at": "2026-05-18T00:00:00+00:00",
  "updated_at": "2026-05-18T00:01:00+00:00",
  "steps": [
    {
      "step_id": "step_1",
      "step_type": "observe",
      "created_at": "2026-05-18T00:01:00+00:00",
      "summary": "Observed revenue",
      "artifact_id": "art_1",
      "output_summary": {
        "intent_type": "observe",
        "status": "success",
        "artifact_type": "observation",
        "row_count": 10
      },
      "provenance": {"runner": "observe"},
      "semantic_metadata": {"metric": "revenue"},
      "warnings": []
    }
  ],
  "artifact_ids": ["art_1"],
  "schema_version": "session_trace.v1"
}
```

`steps` are sorted by `created_at ASC, step_id ASC`. `artifact_ids` is a deduplicated list in first-seen trace order.

## Output Summary

Trace summaries are deterministic and shallow. Only these scalar fields may appear in `output_summary`:

- `intent_type`
- `step_type`
- `artifact_id`
- `status`
- `result_type`
- `artifact_type`
- `artifact_schema_version`
- `row_count`
- `candidate_count`
- `finding_count`
- `driver_count`

Artifact rows, AOI artifacts, driver rows, backing findings, assessments, proposition contexts, and large nested result payloads are not inlined.

## Warning Codes

- `artifact_id_unresolved`: the step has no stable artifact id in its result, and the artifact store fallback did not resolve one.
- `output_summary_unavailable`: the step result contains no whitelisted scalar summary fields.
- `provenance_missing`: `Step.provenance` is absent.
- `semantic_metadata_unavailable`: `Step.semantic_metadata` is absent.

Warnings are step-local. A warning on one step does not make the entire trace fail.

## Agent Workflow Contract

Before producing a final evidence-based answer, an agent must read:

1. `get_session_trace(session_id)` to understand which steps ran and which artifacts exist.
2. `get_session_state(session_id, ...)` to read active propositions, backing findings, blocking gaps, and artifact references.
3. `get_proposition_context(session_id, proposition_id)` for every proposition cited as evidence.

The trace explains execution. Session state and proposition context support conclusions. If trace warnings affect cited evidence, mention the relevant caveat instead of presenting the conclusion as fully verified.
````

- [ ] **Step 2: Link from API README**

Add this bullet to the session/runtime section of `docs/api/README.md`:

````markdown
- [Session Trace](session-trace.md) - agent-facing execution chronology for sessions.
````

- [ ] **Step 3: Update MCP tool reference**

In `docs/user/marivo-mcp-tools-reference.md`, add `get_session_trace` beside the other session tools:

````markdown
### `get_session_trace`

Read the agent-facing execution trace for a session.

Input:

```json
{"session_id": "sess_123"}
```

Use this before final evidence synthesis to understand what ran and which artifact handles exist. Pair it with `get_session_state` and `get_proposition_context`; trace alone is not evidence truth.
````

- [ ] **Step 4: Run documentation smoke search**

Run:

```bash
rg -n "get_session_trace|Session Trace|Agent Workflow Contract|evidence-summary|get_analysis_evidence_summary" docs marivo tests
```

Expected:

```text
get_session_trace appears in runtime/MCP/tests/docs.
Session Trace appears in docs.
Agent Workflow Contract appears in docs.
evidence-summary and get_analysis_evidence_summary do not appear as new API/tool names outside rejected-scope discussion in the design spec.
```

## Task 6: Full Verification

**Files:**
- No new files.

- [ ] **Step 1: Run focused test suite**

Run:

```bash
make test TESTS='tests/runtime/test_runtime_session_ops.py -k session_trace'
make test TESTS='tests/integration/test_sessions.py -k trace'
make test TESTS='tests/transports/mcp/test_tool_parity.py -k session_trace'
make test TESTS='tests/transports/mcp/test_stdio_mcp_e2e.py -k "stdio_server_registers_tools or get_session_trace"'
```

Expected:

```text
passed
```

- [ ] **Step 2: Run broader session and MCP regression tests**

Run:

```bash
make test TESTS='tests/runtime/test_runtime_session_ops.py tests/integration/test_sessions.py tests/transports/mcp/test_tool_parity.py tests/transports/mcp/test_stdio_mcp_e2e.py'
```

Expected:

```text
passed
```

- [ ] **Step 3: Run typecheck**

Run:

```bash
make typecheck
```

Expected:

```text
Success: no issues found
```

- [ ] **Step 4: Run lint**

Run:

```bash
make lint
```

Expected:

```text
All checks passed
```

- [ ] **Step 5: Inspect final diff**

Run:

```bash
git diff -- docs/specs/analysis/evidence-engine/agent-auditability-v1.md docs/specs/analysis/README.md docs/superpowers/plans/2026-05-18-agent-auditability-trace.md docs/api/session-trace.md docs/api/README.md docs/user/marivo-mcp-tools-reference.md marivo/runtime/session.py marivo/runtime/runtime.py marivo/transports/http/models/session_responses.py marivo/transports/http/models/__init__.py marivo/transports/http/sessions.py marivo/transports/mcp/tools/session.py tests/runtime/test_runtime_session_ops.py tests/integration/test_sessions.py tests/transports/mcp/test_tool_parity.py tests/transports/mcp/test_stdio_mcp_e2e.py
```

Expected:

```text
Diff includes one new trace API/tool and no evidence-summary API/tool.
Runtime helpers are named and pure.
Tests cover sorting, artifact fallback, warnings, summary whitelist, HTTP route, and MCP exposure.
```

## Self-Review Notes

Spec coverage:

- Minimal new surface: Task 3 and Task 4 add only `/trace` and `get_session_trace`.
- No duplicated evidence summary: contract guardrails and Task 5 smoke search prevent new evidence-summary APIs.
- Existing canonical evidence truth: Task 5 Agent Workflow Contract keeps `get_session_state` and `get_proposition_context` as conclusion surfaces.
- Per-step warnings: Task 1 failing tests and Task 2 implementation cover all warning codes.
- Deterministic summary whitelist: Task 1 whitelist test and Task 2 helper enforce scalar-only allowed keys.
- Artifact fallback degradation: Task 1 fallback failure test and Task 2 `_artifact_id_for_step` keep failure step-local.
- Auth boundary inheritance: Task 3 parity test checks trace follows current session root access behavior.
- MCP exposure: Task 4 adds registration, schema, stdio listing, and call-through tests.

Placeholder scan:

- No deferred implementation markers are present.
- Every code-changing task includes concrete code blocks and exact repository commands.

Type consistency:

- Runtime public method is `get_session_trace(self, session_id: SessionId) -> dict[str, Any]`.
- HTTP model names are `SessionTraceWarning`, `SessionTraceStep`, and `SessionTraceView`.
- MCP tool name is `get_session_trace`.
- Warning codes match the design spec exactly.
