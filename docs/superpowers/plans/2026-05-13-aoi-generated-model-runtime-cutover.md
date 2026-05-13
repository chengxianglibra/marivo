# AOI Generated Model Runtime Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 将 Marivo atomic intent 的 HTTP/MCP/runtime/artifact commit 主路径切到 AOI generated model 约束，并用 `ExecutionEnvelope` 承载 Marivo 平台元数据。

**Architecture:** 新增一个窄的 AOI runtime adapter 层，集中定义 AOI request/artifact union、operation registry、request lowering、artifact validation 和 `ExecutionEnvelope` 构造规则。HTTP 直接接收 AOI request shape 并返回 `ExecutionEnvelope`；MCP 保留 agent-friendly DTO，但必须先转换为 AOI generated request model 再调用 runtime；runtime 主路径不再以未校验 dict 作为契约。

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI, existing Marivo runtime ports, generated models in `marivo/contracts/generated/aoi.py`, repository commands via `.venv/bin/pytest` or `make`.

---

## Scope Check

这份 spec 覆盖 HTTP、MCP、runtime、artifact store、derived compatibility intent 和测试迁移，但它们共享同一个目标契约：AOI generated model 是分析语义边界，`ExecutionEnvelope` 是 Marivo 返回边界。它们不是独立产品子系统，适合放在同一个 cutover plan 中按层推进。

非目标保持不变：

- 不修改 `marivo/contracts/generated/aoi.py` 或 AOI spec。
- 不新增 batch resolver / session artifact cache。
- 不保留旧 flat HTTP atomic response 作为目标契约。
- 不用旧测试要求反向约束 AOI 目标实现。

## File Structure

### New Files

- `marivo/contracts/aoi_runtime.py`
  定义 `AoiAtomicRequest`、`AoiArtifact`、`RuntimeIntentEnvelope`、`AoiOperationDefinition`、`AOI_OPERATION_REGISTRY`，以及 request/artifact validation helpers。

- `marivo/runtime/aoi_lowering.py`
  将 AOI generated request model 转成当前 intent runner 暂时仍需要的 normalized dict。这个文件是迁移桥，避免 HTTP/MCP/runtime 到处手写转换逻辑。

- `tests/contracts/test_aoi_runtime_contract.py`
  覆盖 AOI union、registry mismatch、artifact validation、`ExecutionEnvelope` shape、`to_legacy_dict()` 非目标路径。

- `tests/adapters/test_artifact_store_artifact_id_lookup.py`
  覆盖 `resolve_artifact_by_id(session_id, artifact_id)` 的同 session 成功、missing failure、cross-session failure。

- `tests/runtime/test_aoi_intent_execution.py`
  覆盖 runtime typed boundary、request lowering、registry dispatch、artifact id reference regression。

- `tests/transports/test_http_aoi_intents.py`
  覆盖 HTTP atomic request shape、`ExecutionEnvelope` response shape、旧 flat response 不泄漏。

- `tests/transports/test_mcp_aoi_adapter.py`
  覆盖 MCP DTO 到 AOI request model 的转换路径。

### Modified Files

- `marivo/contracts/envelope.py`
  保留 `ExecutionEnvelope`，把 `to_legacy_dict()` 标记为 migration-only，并确保目标路径测试不依赖它。

- `marivo/ports/artifact_store.py`
  在 port 上新增 `resolve_artifact_by_id(session_id, artifact_id)`。

- `marivo/adapters/local/file_artifact_store.py`
  实现 file-backed artifact id lookup。

- `marivo/adapters/server/artifact_store.py`
  实现 metadata-backed artifact id lookup。

- `marivo/runtime/runtime.py`
  新增 `resolve_artifact_by_id()` facade；intent 方法接收 AOI request model，并委托到 typed `intent_execution`。

- `marivo/runtime/intent_execution.py`
  用 AOI operation registry 替换散落的 dict dispatcher 主路径。

- `marivo/runtime/intents/_helpers.py`
  增加 AOI artifact commit helper：commit 前校验 generated artifact model，返回 `ExecutionEnvelope`。

- `marivo/runtime/intents/compare.py`、`decompose.py`、`correlate.py`、`forecast.py`、`test.py`
  下游引用改为使用 AOI artifact id，内部通过 `runtime.resolve_artifact_by_id(session_id, artifact_id)` 解析。

- `marivo/runtime/intents/observe.py`、`detect.py`
  runner 输出改为 AOI artifact payload，并通过新 helper 返回 `ExecutionEnvelope`。

- `marivo/runtime/intents/attribute.py`、`diagnose.py`、`validate.py`
  保留接口，但输出改为 derived envelope/bundle：bundle 内只组合 AOI generated artifact dump，产品语义进入 `product_metadata`。

- `marivo/transports/http/sessions.py`
  HTTP atomic routes 使用 AOI generated request model 作为 body；response model 使用 `ExecutionEnvelope`。

- `marivo/transports/http/models.py`
  移除 atomic intent 的旧 hand-written response 主契约；保留非 atomic session/status models。

- `marivo/transports/mcp/tools/intents.py`
  DTO 构造 AOI request model 后调用 runtime，不再传原始 dict。

- `tests/test_envelope.py`、`tests/test_intent_api.py`、`tests/runtime/test_runtime_intent_dispatch.py`、`tests/adapters/test_file_artifact_store.py`、`tests/runtime/test_runtime_construction.py`、`tests/runtime/test_runtime_session_ops.py`
  调整旧测试到新 AOI/Envelope/ArtifactStore port 目标契约。

## Task 1: AOI Runtime Contract Layer

**Files:**
- Create: `marivo/contracts/aoi_runtime.py`
- Create: `tests/contracts/test_aoi_runtime_contract.py`
- Modify: `marivo/contracts/envelope.py`
- Test: `tests/contracts/test_aoi_runtime_contract.py`

- [x] **Step 1: Write failing contract tests**

Create `tests/contracts/test_aoi_runtime_contract.py`:

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from marivo.contracts.aoi_runtime import (
    AOI_OPERATION_REGISTRY,
    RuntimeIntentEnvelope,
    artifact_to_envelope_result,
    assert_request_matches_intent,
    validate_aoi_artifact,
)
from marivo.contracts.envelope import ExecutionEnvelope, StepRef
from marivo.contracts.generated import aoi


def test_runtime_envelope_accepts_generated_observe_request() -> None:
    request = aoi.Observe1(
        metric="metric.dau",
        time_scope={
            "field": "event_time",
            "start": "2026-05-01T00:00:00+00:00",
            "end": "2026-05-02T00:00:00+00:00",
        },
        filter=None,
        granularity=None,
        dimensions=None,
    )

    envelope = RuntimeIntentEnvelope(session_id="s1", actor="u1", request=request)

    assert envelope.session_id == "s1"
    assert envelope.request is request


def test_registry_rejects_request_for_wrong_intent() -> None:
    request = aoi.Forecast(source_artifact_id="art_obs_1", horizon=7, profile="level")

    with pytest.raises(ValueError, match="AOI_OPERATION_MISMATCH"):
        assert_request_matches_intent("compare", request)


def test_registry_contains_all_target_atomic_operations() -> None:
    assert sorted(AOI_OPERATION_REGISTRY) == [
        "compare",
        "correlate",
        "decompose",
        "detect",
        "forecast",
        "observe",
        "test",
    ]


def test_validate_success_artifact_uses_generated_model() -> None:
    artifact = validate_aoi_artifact(
        {
            "artifact_id": "art_1",
            "result": {"value": 42.0},
            "failure": None,
        }
    )

    assert isinstance(artifact, aoi.Artifact1)
    assert artifact.artifact_id == "art_1"


def test_validate_failure_artifact_uses_generated_model() -> None:
    artifact = validate_aoi_artifact(
        {
            "artifact_id": "art_1",
            "result": None,
            "failure": {"code": "not_comparable", "message": "inputs cannot be compared"},
        }
    )

    assert isinstance(artifact, aoi.Artifact2)
    assert artifact.failure.code == "not_comparable"


def test_validate_artifact_rejects_non_aoi_shape() -> None:
    with pytest.raises(ValidationError):
        validate_aoi_artifact({"artifact_id": "art_1", "value": 42.0})


def test_execution_envelope_keeps_aoi_artifact_under_result() -> None:
    artifact = validate_aoi_artifact(
        {
            "artifact_id": "art_1",
            "result": {"value": 42.0},
            "failure": None,
        }
    )
    env = ExecutionEnvelope(
        intent_type="observe",
        step_type="observe",
        step_ref=StepRef(session_id="s1", step_id="step_1", step_type="observe"),
        artifact_id="art_1",
        result=artifact_to_envelope_result(artifact),
        provenance={"query_hash": "abc"},
    )

    dumped = env.model_dump(exclude_none=True)

    assert dumped["artifact_id"] == "art_1"
    assert dumped["result"] == {"artifact_id": "art_1", "result": {"value": 42.0}}
    assert "value" not in dumped
    assert dumped["provenance"] == {"query_hash": "abc"}
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/contracts/test_aoi_runtime_contract.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'marivo.contracts.aoi_runtime'`.

- [x] **Step 3: Implement AOI runtime contract module**

Create `marivo/contracts/aoi_runtime.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict

from marivo.contracts.generated import aoi


AoiAtomicRequest: TypeAlias = (
    aoi.Compare
    | aoi.Decompose
    | aoi.Correlate
    | aoi.Detect
    | aoi.Test
    | aoi.Forecast
    | aoi.Observe1
    | aoi.Observe2
    | aoi.Observe3
    | aoi.Observe4
)

AoiArtifact: TypeAlias = aoi.Artifact1 | aoi.Artifact2


class RuntimeIntentEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    session_id: str
    actor: str | None = None
    request: AoiAtomicRequest


@dataclass(frozen=True)
class AoiOperationDefinition:
    intent_type: str
    request_types: tuple[type[AoiAtomicRequest], ...]


AOI_OPERATION_REGISTRY: dict[str, AoiOperationDefinition] = {
    "observe": AoiOperationDefinition(
        intent_type="observe",
        request_types=(aoi.Observe1, aoi.Observe2, aoi.Observe3, aoi.Observe4),
    ),
    "compare": AoiOperationDefinition("compare", (aoi.Compare,)),
    "decompose": AoiOperationDefinition("decompose", (aoi.Decompose,)),
    "correlate": AoiOperationDefinition("correlate", (aoi.Correlate,)),
    "detect": AoiOperationDefinition("detect", (aoi.Detect,)),
    "test": AoiOperationDefinition("test", (aoi.Test,)),
    "forecast": AoiOperationDefinition("forecast", (aoi.Forecast,)),
}


def assert_request_matches_intent(intent_type: str, request: AoiAtomicRequest) -> None:
    operation = AOI_OPERATION_REGISTRY.get(intent_type)
    if operation is None:
        raise ValueError(f"AOI_OPERATION_UNKNOWN - unsupported intent '{intent_type}'")
    if not isinstance(request, operation.request_types):
        raise ValueError(
            "AOI_OPERATION_MISMATCH - "
            f"intent '{intent_type}' does not accept request type "
            f"'{request.__class__.__name__}'"
        )


def validate_aoi_artifact(value: dict[str, object] | AoiArtifact) -> AoiArtifact:
    if isinstance(value, (aoi.Artifact1, aoi.Artifact2)):
        return value
    if value.get("failure") is None:
        return aoi.Artifact1.model_validate(value)
    return aoi.Artifact2.model_validate(value)


def artifact_to_envelope_result(artifact: AoiArtifact) -> dict[str, object]:
    return artifact.model_dump(exclude_none=True)
```

Modify `marivo/contracts/envelope.py` docstring on `to_legacy_dict()`:

```python
    def to_legacy_dict(self) -> dict[str, Any]:
        """Produce the old flat dict shape for explicit migration callers only.

        Target-state HTTP/MCP/runtime paths must return ``ExecutionEnvelope``
        directly and must not call this method.
        """
```

Keep the method body unchanged in this task so old callers fail only when later tasks remove target-path usage.

- [x] **Step 4: Run contract test**

Run:

```bash
.venv/bin/pytest tests/contracts/test_aoi_runtime_contract.py -q
```

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add marivo/contracts/aoi_runtime.py marivo/contracts/envelope.py tests/contracts/test_aoi_runtime_contract.py
git commit -m "feat: add AOI runtime contract layer"
```

## Task 2: ArtifactStore Artifact ID Lookup Port

**Files:**
- Modify: `marivo/ports/artifact_store.py`
- Modify: `marivo/adapters/local/file_artifact_store.py`
- Modify: `marivo/adapters/server/artifact_store.py`
- Modify: `marivo/runtime/runtime.py`
- Modify: `tests/adapters/test_file_artifact_store.py`
- Create: `tests/adapters/test_artifact_store_artifact_id_lookup.py`
- Test: `tests/adapters/test_artifact_store_artifact_id_lookup.py`, `tests/adapters/test_file_artifact_store.py`

- [x] **Step 1: Write failing adapter tests**

Create `tests/adapters/test_artifact_store_artifact_id_lookup.py`:

```python
from __future__ import annotations

from pathlib import Path

from marivo.adapters.local.file_artifact_store import FileArtifactStore
from marivo.contracts.ids import ArtifactId, SessionId, StepId


def test_resolve_artifact_by_id_returns_content_for_same_session(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    artifact_id = store.insert_artifact(
        SessionId("s1"),
        StepId("step_1"),
        "aoi_artifact",
        "observe",
        {"artifact_id": "art_inner", "result": {"value": 42.0}},
    )

    resolved = store.resolve_artifact_by_id(SessionId("s1"), artifact_id)

    assert resolved == {"artifact_id": "art_inner", "result": {"value": 42.0}}


def test_resolve_artifact_by_id_returns_none_for_missing_artifact(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)

    resolved = store.resolve_artifact_by_id(SessionId("s1"), ArtifactId("art_missing"))

    assert resolved is None


def test_resolve_artifact_by_id_is_session_scoped(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    artifact_id = store.insert_artifact(
        SessionId("s1"),
        StepId("step_1"),
        "aoi_artifact",
        "observe",
        {"artifact_id": "art_inner", "result": {"value": 42.0}},
    )

    resolved = store.resolve_artifact_by_id(SessionId("s2"), artifact_id)

    assert resolved is None
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/adapters/test_artifact_store_artifact_id_lookup.py -q
```

Expected: FAIL with `AttributeError: 'FileArtifactStore' object has no attribute 'resolve_artifact_by_id'`.

- [x] **Step 3: Add port method and local adapter implementation**

Modify `marivo/ports/artifact_store.py`:

```python
    def resolve_artifact_by_id(
        self,
        session_id: SessionId,
        artifact_id: ArtifactId,
    ) -> dict[str, Any] | None: ...
```

Add to `marivo/adapters/local/file_artifact_store.py`:

```python
    def resolve_artifact_by_id(
        self,
        session_id: SessionId,
        artifact_id: ArtifactId,
    ) -> dict[str, Any] | None:
        """Return committed artifact content by portable artifact id within one session."""
        for entry in self._read_index(session_id):
            if entry.get("artifact_id") != str(artifact_id):
                continue
            if entry.get("lifecycle") != "committed":
                continue
            step_path = self._artifact_path(session_id, StepId(entry["step_id"]))
            if not step_path.is_file():
                return None
            record: dict[str, Any] = json.loads(step_path.read_text(encoding="utf-8"))
            if record.get("lifecycle") != "committed":
                return None
            if record.get("artifact_id") != str(artifact_id):
                return None
            return record.get("content", {})
        return None
```

- [x] **Step 4: Add server adapter and runtime facade**

Add to `marivo/adapters/server/artifact_store.py`:

```python
    def resolve_artifact_by_id(
        self,
        session_id: SessionId,
        artifact_id: ArtifactId,
    ) -> dict[str, Any] | None:
        row = self._metadata.query_one(
            "SELECT content_json FROM artifacts "
            "WHERE artifact_id = ? AND session_id = ? AND lifecycle = 'committed' "
            "ORDER BY created_at DESC LIMIT 1",
            [artifact_id, session_id],
        )
        return json.loads(row["content_json"]) if row else None
```

Add to `marivo/runtime/runtime.py` near existing artifact I/O methods:

```python
    def resolve_artifact_by_id(self, session_id: str, artifact_id: str) -> dict[str, Any] | None:
        """Return committed artifact content by portable artifact id within one session."""
        return self._ports.artifact_store.resolve_artifact_by_id(
            SessionId(session_id), ArtifactId(artifact_id)
        )
```

- [x] **Step 5: Update stub stores used by runtime tests**

In these test files, add the method to each `StubArtifactStore`:

- `tests/runtime/test_runtime_intent_dispatch.py`
- `tests/runtime/test_runtime_construction.py`
- `tests/runtime/test_runtime_session_ops.py`

Use this exact method body:

```python
    def resolve_artifact_by_id(self, session_id, artifact_id):
        return None
```

- [x] **Step 6: Run artifact store tests**

Run:

```bash
.venv/bin/pytest tests/adapters/test_artifact_store_artifact_id_lookup.py tests/adapters/test_file_artifact_store.py -q
```

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add marivo/ports/artifact_store.py marivo/adapters/local/file_artifact_store.py marivo/adapters/server/artifact_store.py marivo/runtime/runtime.py tests/adapters/test_artifact_store_artifact_id_lookup.py tests/adapters/test_file_artifact_store.py tests/runtime/test_runtime_intent_dispatch.py tests/runtime/test_runtime_construction.py tests/runtime/test_runtime_session_ops.py
git commit -m "feat: resolve artifacts by AOI artifact id"
```

## Task 3: AOI Request Lowering

**Files:**
- Create: `marivo/runtime/aoi_lowering.py`
- Create: `tests/runtime/test_aoi_lowering.py`
- Test: `tests/runtime/test_aoi_lowering.py`

- [x] **Step 1: Write failing lowering tests**

Create `tests/runtime/test_aoi_lowering.py`:

```python
from __future__ import annotations

from marivo.contracts.generated import aoi
from marivo.runtime.aoi_lowering import lower_aoi_request


def test_lower_observe_request_to_runner_params() -> None:
    request = aoi.Observe1(
        metric="metric.dau",
        time_scope={
            "field": "event_time",
            "start": "2026-05-01T00:00:00+00:00",
            "end": "2026-05-02T00:00:00+00:00",
        },
        filter=None,
        granularity="day",
        dimensions=None,
    )

    params = lower_aoi_request("observe", request)

    assert params == {
        "metric": "metric.dau",
        "time_scope": {
            "field": "event_time",
            "start": "2026-05-01T00:00:00Z",
            "end": "2026-05-02T00:00:00Z",
        },
        "filter": None,
        "granularity": "day",
    }


def test_lower_compare_request_uses_artifact_ids() -> None:
    request = aoi.Compare(
        left_artifact_id="art_left",
        right_artifact_id="art_right",
        compare_type="normal",
    )

    params = lower_aoi_request("compare", request)

    assert params == {
        "left_artifact_id": "art_left",
        "right_artifact_id": "art_right",
        "compare_type": "normal",
    }


def test_lower_forecast_request_uses_source_artifact_id() -> None:
    request = aoi.Forecast(source_artifact_id="art_obs", horizon=7, profile="level")

    params = lower_aoi_request("forecast", request)

    assert params == {"source_artifact_id": "art_obs", "horizon": 7, "profile": "level"}
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/runtime/test_aoi_lowering.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'marivo.runtime.aoi_lowering'`.

- [x] **Step 3: Implement lowering module**

Create `marivo/runtime/aoi_lowering.py`:

```python
from __future__ import annotations

from typing import Any

from pydantic import AwareDatetime

from marivo.contracts.aoi_runtime import AoiAtomicRequest, assert_request_matches_intent
from marivo.contracts.generated import aoi


def _dump_datetime(value: AwareDatetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _dump_time_scope(value: aoi.TimeScope) -> dict[str, str]:
    return {
        "field": value.field,
        "start": _dump_datetime(value.start),
        "end": _dump_datetime(value.end),
    }


def _dump_expression(value: aoi.Expression | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return value.model_dump(exclude_none=True)


def lower_aoi_request(intent_type: str, request: AoiAtomicRequest) -> dict[str, Any]:
    assert_request_matches_intent(intent_type, request)

    if isinstance(request, (aoi.Observe1, aoi.Observe2, aoi.Observe3, aoi.Observe4)):
        params: dict[str, Any] = {
            "metric": request.metric,
            "time_scope": _dump_time_scope(request.time_scope),
            "filter": _dump_expression(request.filter),
        }
        if request.granularity is not None:
            params["granularity"] = request.granularity
        dimensions = getattr(request, "dimensions", None)
        if dimensions is not None:
            dumped = dimensions.model_dump() if hasattr(dimensions, "model_dump") else dimensions
            params["dimensions"] = dumped
        return params

    if isinstance(request, aoi.Compare):
        return {
            "left_artifact_id": request.left_artifact_id,
            "right_artifact_id": request.right_artifact_id,
            "compare_type": request.compare_type,
        }

    if isinstance(request, aoi.Decompose):
        return {
            "compare_artifact_id": request.compare_artifact_id,
            "dimension": request.dimension,
            "limit": request.limit,
        }

    if isinstance(request, aoi.Correlate):
        return {
            "left_artifact_id": request.left_artifact_id,
            "right_artifact_id": request.right_artifact_id,
            "method": request.method,
        }

    if isinstance(request, aoi.Detect):
        return request.model_dump(exclude_none=True)

    if isinstance(request, aoi.Test):
        return request.model_dump(exclude_none=True)

    if isinstance(request, aoi.Forecast):
        return request.model_dump(exclude_none=True)

    raise TypeError(f"Unsupported AOI request type: {request.__class__.__name__}")
```

- [x] **Step 4: Run lowering tests**

Run:

```bash
.venv/bin/pytest tests/runtime/test_aoi_lowering.py -q
```

Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add marivo/runtime/aoi_lowering.py tests/runtime/test_aoi_lowering.py
git commit -m "feat: lower AOI requests for runtime runners"
```

## Task 4: Runtime Typed Intent Boundary and Registry Dispatch

**Files:**
- Modify: `marivo/runtime/intent_execution.py`
- Modify: `marivo/runtime/runtime.py`
- Modify: `tests/runtime/test_runtime_intent_dispatch.py`
- Create: `tests/runtime/test_aoi_intent_execution.py`
- Test: `tests/runtime/test_aoi_intent_execution.py`, `tests/runtime/test_runtime_intent_dispatch.py`

- [x] **Step 1: Write failing typed boundary tests**

Create `tests/runtime/test_aoi_intent_execution.py`:

```python
from __future__ import annotations

from unittest.mock import Mock

import pytest

from marivo.contracts.generated import aoi
from marivo.runtime import intent_execution


class _Runtime:
    def __init__(self) -> None:
        self.ports = Mock()


def test_observe_dispatch_accepts_aoi_request(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _Runtime()
    request = aoi.Observe1(
        metric="metric.dau",
        time_scope={
            "field": "event_time",
            "start": "2026-05-01T00:00:00+00:00",
            "end": "2026-05-02T00:00:00+00:00",
        },
        filter=None,
        granularity=None,
        dimensions=None,
    )
    runner = Mock(return_value={"ok": True})
    monkeypatch.setattr(intent_execution, "_assert_session_is_open", Mock())
    monkeypatch.setitem(intent_execution.AOI_RUNNERS, "observe", runner)

    result = intent_execution.observe(runtime, "s1", request)

    assert result == {"ok": True}
    runner.assert_called_once()
    _, session_id, params = runner.call_args.args
    assert session_id == "s1"
    assert params["metric"] == "metric.dau"


def test_registry_mismatch_fails_before_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _Runtime()
    request = aoi.Forecast(source_artifact_id="art_1", horizon=7, profile="level")
    runner = Mock()
    monkeypatch.setattr(intent_execution, "_assert_session_is_open", Mock())
    monkeypatch.setitem(intent_execution.AOI_RUNNERS, "compare", runner)

    with pytest.raises(ValueError, match="AOI_OPERATION_MISMATCH"):
        intent_execution.compare(runtime, "s1", request)

    runner.assert_not_called()
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/runtime/test_aoi_intent_execution.py -q
```

Expected: FAIL because `intent_execution.observe()` currently expects `params: dict[str, Any]`.

- [x] **Step 3: Implement typed dispatch**

Modify `marivo/runtime/intent_execution.py`:

```python
from marivo.contracts.aoi_runtime import AoiAtomicRequest, assert_request_matches_intent
from marivo.runtime.aoi_lowering import lower_aoi_request
```

Replace `_IntentRunner` and dispatcher map with:

```python
_IntentRunner = Callable[[Any, str, dict[str, Any] | None], dict[str, Any]]

AOI_RUNNERS: dict[str, _IntentRunner] = {
    "observe": run_observe_intent,
    "compare": run_compare_intent,
    "decompose": run_decompose_intent,
    "correlate": run_correlate_intent,
    "detect": run_detect_intent,
    "test": run_test_intent,
    "forecast": run_forecast_intent,
}

DERIVED_RUNNERS: dict[str, _IntentRunner] = {
    "attribute": run_attribute_intent,
    "diagnose": run_diagnose_intent,
    "validate": run_validate_intent,
}
```

Change atomic wrappers to typed signatures:

```python
def observe(runtime: MarivoRuntime, session_id: SessionId, request: AoiAtomicRequest) -> dict[str, Any]:
    return _run_aoi(runtime, "observe", session_id, request)


def compare(runtime: MarivoRuntime, session_id: SessionId, request: AoiAtomicRequest) -> dict[str, Any]:
    return _run_aoi(runtime, "compare", session_id, request)
```

Apply the same pattern for `decompose`, `correlate`, `detect`, `test`, and `forecast`.

Keep derived wrappers dict-based for this task:

```python
def attribute(runtime: MarivoRuntime, session_id: SessionId, params: dict[str, Any]) -> dict[str, Any]:
    return _run_derived(runtime, "attribute", session_id, params)
```

Add:

```python
def _run_aoi(
    runtime: MarivoRuntime,
    intent_type: str,
    session_id: SessionId,
    request: AoiAtomicRequest,
) -> dict[str, Any]:
    _assert_session_is_open(runtime, session_id)
    assert_request_matches_intent(intent_type, request)
    params = lower_aoi_request(intent_type, request)
    return AOI_RUNNERS[intent_type](runtime, str(session_id), params)


def _run_derived(
    runtime: MarivoRuntime,
    intent_type: str,
    session_id: SessionId,
    params: dict[str, Any],
) -> dict[str, Any]:
    _assert_session_is_open(runtime, session_id)
    return DERIVED_RUNNERS[intent_type](runtime, str(session_id), params)
```

- [x] **Step 4: Update runtime facade signatures**

Modify atomic methods in `marivo/runtime/runtime.py`:

```python
    def observe(self, session_id: str, request: AoiAtomicRequest) -> dict[str, Any]:
        return intent_execution.observe(self, SessionId(session_id), request)
```

Apply the same pattern for `compare`, `decompose`, `correlate`, `detect`, `test`, and `forecast`.

Keep `attribute`, `diagnose`, and `validate` accepting `dict[str, Any]` until Task 8.

Add import under `TYPE_CHECKING` or runtime import:

```python
from marivo.contracts.aoi_runtime import AoiAtomicRequest
```

- [x] **Step 5: Update old dispatch tests**

In `tests/runtime/test_runtime_intent_dispatch.py`, change atomic dispatch calls from dict to generated AOI request objects. Use this helper:

```python
from marivo.contracts.generated import aoi


def _observe_request() -> aoi.Observe1:
    return aoi.Observe1(
        metric="metric.dau",
        time_scope={
            "field": "event_time",
            "start": "2026-05-01T00:00:00+00:00",
            "end": "2026-05-02T00:00:00+00:00",
        },
        filter=None,
        granularity=None,
        dimensions=None,
    )
```

For tests that only assert method dispatch exists, call:

```python
rt.observe("s1", _observe_request())
```

Do not update derived calls in this task.

- [x] **Step 6: Run runtime dispatch tests**

Run:

```bash
.venv/bin/pytest tests/runtime/test_aoi_intent_execution.py tests/runtime/test_runtime_intent_dispatch.py -q
```

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add marivo/runtime/intent_execution.py marivo/runtime/runtime.py tests/runtime/test_aoi_intent_execution.py tests/runtime/test_runtime_intent_dispatch.py
git commit -m "feat: dispatch atomic intents through AOI requests"
```

## Task 5: AOI Artifact Commit Boundary and Envelope Result

**Files:**
- Modify: `marivo/runtime/intents/_helpers.py`
- Modify: `marivo/runtime/intents/observe.py`
- Modify: `marivo/runtime/intents/detect.py`
- Modify: `tests/local/test_commit_step_result.py`
- Modify: `tests/test_envelope.py`
- Test: `tests/local/test_commit_step_result.py`, `tests/test_envelope.py`

- [x] **Step 1: Write failing helper tests**

Append to `tests/local/test_commit_step_result.py`:

```python
from marivo.contracts.envelope import ExecutionEnvelope
from marivo.runtime.intents._helpers import commit_aoi_artifact_result


def test_commit_aoi_artifact_result_validates_and_returns_envelope() -> None:
    runtime = Mock()
    runtime.commit_artifact_with_extraction.return_value = "art_1"

    env = commit_aoi_artifact_result(
        runtime,
        session_id="s1",
        step_id="step_1",
        step_type="observe",
        artifact_type="aoi_artifact",
        artifact_name="observe",
        aoi_result={"value": 42.0},
        summary="observe metric.dau",
    )

    assert isinstance(env, ExecutionEnvelope)
    assert env.artifact_id == "art_1"
    assert env.result == {"artifact_id": "art_1", "result": {"value": 42.0}}
    runtime.insert_step.assert_called_once()


def test_commit_aoi_artifact_result_rejects_non_aoi_payload() -> None:
    runtime = Mock()
    runtime.commit_artifact_with_extraction.return_value = "art_1"

    with pytest.raises(Exception):
        commit_aoi_artifact_result(
            runtime,
            session_id="s1",
            step_id="step_1",
            step_type="observe",
            artifact_type="aoi_artifact",
            artifact_name="observe",
            aoi_result={"not_a_valid_result": {"nested": True}},
            summary="observe metric.dau",
        )

    runtime.insert_step.assert_not_called()
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/local/test_commit_step_result.py::test_commit_aoi_artifact_result_validates_and_returns_envelope tests/local/test_commit_step_result.py::test_commit_aoi_artifact_result_rejects_non_aoi_payload -q
```

Expected: FAIL with `ImportError` for `commit_aoi_artifact_result`.

- [x] **Step 3: Implement AOI commit helper**

Add to `marivo/runtime/intents/_helpers.py`:

```python
from marivo.contracts.aoi_runtime import artifact_to_envelope_result, validate_aoi_artifact
```

Add function:

```python
def commit_aoi_artifact_result(
    runtime: MarivoRuntime,
    session_id: str,
    step_id: str,
    step_type: str,
    artifact_type: str,
    artifact_name: str,
    aoi_result: dict[str, Any],
    summary: str,
    provenance: dict[str, Any] | None = None,
    product_metadata: dict[str, Any] | None = None,
) -> ExecutionEnvelope:
    """Validate AOI artifact, commit it, insert step, and return ExecutionEnvelope."""
    artifact_id = runtime.commit_artifact_with_extraction(
        session_id,
        step_id,
        artifact_type,
        artifact_name,
        {
            "artifact_id": "__pending__",
            "result": aoi_result,
            "failure": None,
        },
        step_type=step_type,
    )
    artifact = validate_aoi_artifact(
        {
            "artifact_id": artifact_id,
            "result": aoi_result,
            "failure": None,
        }
    )
    env = build_envelope(
        session_id=session_id,
        step_id=step_id,
        step_type=step_type,
        artifact_id=artifact_id,
        artifact_payload=artifact_to_envelope_result(artifact),
        provenance=provenance,
        product_metadata=product_metadata,
    )
    runtime.insert_step(
        step_id,
        session_id,
        step_type,
        summary,
        env.model_dump(exclude_none=True),
        provenance=provenance,
        semantic_metadata=None,
    )
    return env
```

Before moving runners to this helper, keep `commit_step_result()` available for derived migration.

- [x] **Step 4: Rewrite envelope tests away from legacy target assertions**

In `tests/test_envelope.py`, remove tests named:

- `test_to_legacy_dict_flat_merges_result`
- `test_envelope_legacy_dict_matches_old_shape`

Add:

```python
    def test_model_dump_does_not_flatten_result(self) -> None:
        env = ExecutionEnvelope(
            intent_type="observe",
            step_type="observe",
            step_ref=StepRef(session_id="s1", step_id="step_1", step_type="observe"),
            artifact_id="art_1",
            result={"artifact_id": "art_1", "result": {"value": 42.0}},
        )
        dumped = env.model_dump(exclude_none=True)
        self.assertEqual(dumped["result"], {"artifact_id": "art_1", "result": {"value": 42.0}})
        self.assertNotIn("value", dumped)
```

- [x] **Step 5: Run helper and envelope tests**

Run:

```bash
.venv/bin/pytest tests/local/test_commit_step_result.py tests/test_envelope.py -q
```

Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add marivo/runtime/intents/_helpers.py tests/local/test_commit_step_result.py tests/test_envelope.py
git commit -m "feat: validate AOI artifacts before envelope return"
```

## Task 6: Downstream Artifact References by AOI Artifact ID

**Files:**
- Modify: `marivo/runtime/intents/compare.py`
- Modify: `marivo/runtime/intents/decompose.py`
- Modify: `marivo/runtime/intents/correlate.py`
- Modify: `marivo/runtime/intents/forecast.py`
- Modify: `marivo/runtime/intents/test.py`
- Modify: `tests/test_intent_api.py`
- Create: `tests/runtime/test_aoi_artifact_references.py`
- Test: `tests/runtime/test_aoi_artifact_references.py`

- [x] **Step 1: Write failing artifact reference tests**

Create `tests/runtime/test_aoi_artifact_references.py`:

```python
from __future__ import annotations

from unittest.mock import Mock

import pytest

from marivo.runtime.intents.forecast import run_forecast_intent


def test_forecast_resolves_source_by_artifact_id() -> None:
    runtime = Mock()
    runtime.resolve_artifact_by_id.return_value = {
        "observation_type": "time_series",
        "metric": "metric.dau",
        "granularity": "day",
        "series": [
            {"bucket_start": "2026-05-01T00:00:00+00:00", "value": 10.0},
            {"bucket_start": "2026-05-02T00:00:00+00:00", "value": 12.0},
            {"bucket_start": "2026-05-03T00:00:00+00:00", "value": 14.0},
        ],
    }
    runtime.commit_artifact_with_extraction.return_value = "art_forecast"

    result = run_forecast_intent(
        runtime,
        "s1",
        {"source_artifact_id": "art_obs", "horizon": 1, "profile": "level"},
    )

    runtime.resolve_artifact_by_id.assert_called_once_with("s1", "art_obs")
    assert result["artifact_id"] == "art_forecast"


def test_forecast_rejects_missing_artifact_id() -> None:
    runtime = Mock()
    runtime.resolve_artifact_by_id.return_value = None

    with pytest.raises(ValueError, match="ARTIFACT_NOT_FOUND"):
        run_forecast_intent(
            runtime,
            "s1",
            {"source_artifact_id": "art_missing", "horizon": 1, "profile": "level"},
        )
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/runtime/test_aoi_artifact_references.py -q
```

Expected: FAIL because `run_forecast_intent()` currently requires `source_ref.step_id`.

- [x] **Step 3: Update forecast to artifact-id request path**

In `marivo/runtime/intents/forecast.py`, replace source ref extraction with:

```python
    src_artifact_id: str = str(p.get("source_artifact_id") or "").strip()
    if not src_artifact_id:
        raise ValueError("forecast: INVALID_ARGUMENT - source_artifact_id is required")
```

Replace artifact resolution block with:

```python
    source_artifact = runtime.resolve_artifact_by_id(session_id, src_artifact_id)
    if source_artifact is None:
        raise ValueError(
            f"forecast: ARTIFACT_NOT_FOUND - no committed artifact '{src_artifact_id}' "
            f"in session '{session_id}'"
        )
```

Remove the old `source_ref.step_id`, `source_ref.artifact_id`, and `source_ref.session_id` validation from the AOI path.

- [x] **Step 4: Apply same artifact-id resolution pattern to compare/decompose/correlate/test**

Use these parameter names:

- `compare.py`: `left_artifact_id`, `right_artifact_id`
- `decompose.py`: `compare_artifact_id`
- `correlate.py`: `left_artifact_id`, `right_artifact_id`
- `test.py`: keep AOI `left` and `right` slices for first cut; if current AOI `Test` has no upstream artifact ids, preserve slice-based execution and do not introduce step refs.

For compare:

```python
    left_artifact_id: str = str(p.get("left_artifact_id") or "").strip()
    right_artifact_id: str = str(p.get("right_artifact_id") or "").strip()
    if not left_artifact_id or not right_artifact_id:
        raise ValueError("compare: INVALID_ARGUMENT - left_artifact_id and right_artifact_id are required")

    left_artifact = runtime.resolve_artifact_by_id(session_id, left_artifact_id)
    if left_artifact is None:
        raise ValueError(f"compare: ARTIFACT_NOT_FOUND - no committed artifact '{left_artifact_id}'")

    right_artifact = runtime.resolve_artifact_by_id(session_id, right_artifact_id)
    if right_artifact is None:
        raise ValueError(f"compare: ARTIFACT_NOT_FOUND - no committed artifact '{right_artifact_id}'")
```

For decompose:

```python
    compare_artifact_id: str = str(p.get("compare_artifact_id") or "").strip()
    if not compare_artifact_id:
        raise ValueError("decompose: INVALID_ARGUMENT - compare_artifact_id is required")

    compare_artifact = runtime.resolve_artifact_by_id(session_id, compare_artifact_id)
    if compare_artifact is None:
        raise ValueError(f"decompose: ARTIFACT_NOT_FOUND - no committed artifact '{compare_artifact_id}'")
```

For correlate:

```python
    left_artifact_id: str = str(p.get("left_artifact_id") or "").strip()
    right_artifact_id: str = str(p.get("right_artifact_id") or "").strip()
    if not left_artifact_id or not right_artifact_id:
        raise ValueError("correlate: INVALID_ARGUMENT - left_artifact_id and right_artifact_id are required")
```

- [x] **Step 5: Update API tests that seed refs**

In `tests/test_intent_api.py`, when constructing downstream requests, replace step-ref payloads:

```python
{
    "left_artifact_id": left_artifact_id,
    "right_artifact_id": right_artifact_id,
    "compare_type": "normal",
}
```

For forecast:

```python
{
    "source_artifact_id": source_artifact_id,
    "horizon": 7,
    "profile": "level",
}
```

Keep a negative test for old cross-session step refs only if it targets derived compatibility endpoints. Atomic AOI endpoints should now reject unknown fields through generated model validation.

- [x] **Step 6: Run focused reference tests**

Run:

```bash
.venv/bin/pytest tests/runtime/test_aoi_artifact_references.py tests/test_intent_api.py -q
```

Expected: PASS after tests are aligned to artifact-id references.

- [x] **Step 7: Commit**

```bash
git add marivo/runtime/intents/compare.py marivo/runtime/intents/decompose.py marivo/runtime/intents/correlate.py marivo/runtime/intents/forecast.py marivo/runtime/intents/test.py tests/runtime/test_aoi_artifact_references.py tests/test_intent_api.py
git commit -m "feat: resolve downstream AOI refs by artifact id"
```

## Task 7: HTTP Atomic AOI Request and ExecutionEnvelope Response

**Files:**
- Modify: `marivo/transports/http/sessions.py`
- Modify: `marivo/transports/http/models.py`
- Create: `tests/transports/test_http_aoi_intents.py`
- Modify: `tests/test_openapi_schema_quality.py`
- Test: `tests/transports/test_http_aoi_intents.py`, `tests/test_openapi_schema_quality.py`

- [x] **Step 1: Write failing HTTP tests**

Create `tests/transports/test_http_aoi_intents.py`:

```python
from __future__ import annotations

from unittest.mock import Mock

from fastapi.testclient import TestClient

from marivo.main import create_app


def test_http_observe_returns_execution_envelope(tmp_path, monkeypatch) -> None:
    app = create_app(tmp_path / "http-aoi.duckdb")
    client = TestClient(app, headers={"X-Marivo-User": "test_user"})
    session_id = client.post("/sessions", json={"goal": "AOI HTTP"}).json()["session_id"]

    runtime = app.state.services.runtime
    runtime.observe = Mock(
        return_value={
            "intent_type": "observe",
            "step_type": "observe",
            "step_ref": {"session_id": session_id, "step_id": "step_1", "step_type": "observe"},
            "artifact_id": "art_1",
            "result": {"artifact_id": "art_1", "result": {"value": 42.0}},
            "provenance": {"query_hash": "abc"},
        }
    )

    response = client.post(
        f"/sessions/{session_id}/intents/observe",
        json={
            "metric": "metric.dau",
            "time_scope": {
                "field": "event_time",
                "start": "2026-05-01T00:00:00+00:00",
                "end": "2026-05-02T00:00:00+00:00",
            },
            "filter": None,
            "granularity": None,
            "dimensions": None,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["artifact_id"] == "art_1"
    assert body["result"] == {"artifact_id": "art_1", "result": {"value": 42.0}}
    assert "value" not in body
    runtime.observe.assert_called_once()


def test_http_observe_rejects_old_time_scope_shape(tmp_path) -> None:
    app = create_app(tmp_path / "http-aoi-invalid.duckdb")
    client = TestClient(app, headers={"X-Marivo-User": "test_user"})
    session_id = client.post("/sessions", json={"goal": "AOI HTTP invalid"}).json()["session_id"]

    response = client.post(
        f"/sessions/{session_id}/intents/observe",
        json={
            "metric": "metric.dau",
            "time_scope": {"kind": "range", "start": "2026-05-01", "end": "2026-05-02"},
        },
    )

    assert response.status_code == 422
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/transports/test_http_aoi_intents.py -q
```

Expected: FAIL because HTTP still uses `ObserveRequest` / `ObserveResponse`.

- [x] **Step 3: Add HTTP envelope model**

In `marivo/transports/http/models.py`, add a transport response model only for envelope serialization:

```python
from marivo.contracts.envelope import ExecutionEnvelope
```

Do not create new atomic response models. Atomic routes should return `ExecutionEnvelope` directly.

- [x] **Step 4: Switch atomic route request models to AOI generated models**

In `marivo/transports/http/sessions.py`, import:

```python
from marivo.contracts.envelope import ExecutionEnvelope
from marivo.contracts.generated import aoi
```

Change atomic route signatures:

```python
@router.post("/sessions/{session_id}/intents/observe", response_model=ExecutionEnvelope)
def intent_observe(
    session_id: str,
    payload: aoi.Observe1 | aoi.Observe2 | aoi.Observe3 | aoi.Observe4,
    request: Request,
) -> ExecutionEnvelope:
    result = _run_intent(session_id, "observe", payload, request)
    return ExecutionEnvelope.model_validate(result)
```

Apply the same target response model for:

- `compare`: `payload: aoi.Compare`
- `decompose`: `payload: aoi.Decompose`
- `correlate`: `payload: aoi.Correlate`
- `detect`: `payload: aoi.Detect`
- `test`: `payload: aoi.Test`
- `forecast`: `payload: aoi.Forecast`

Remove `_assert_same_session()` calls for atomic AOI endpoints because session scoping now happens in `resolve_artifact_by_id(session_id, artifact_id)` and generated requests no longer carry `session_id`.

- [x] **Step 5: Update `_run_intent` type**

In `marivo/transports/http/sessions.py`, change:

```python
def _run_intent(
    session_id: str, intent_type: str, payload: Any, request: Request
) -> dict[str, Any]:
```

And method lookup:

```python
        method: Callable[[str, Any], dict[str, Any]] | None = getattr(runtime, intent_type, None)
```

Call:

```python
        return method(session_id, payload)
```

Do not call `payload.model_dump()` for atomic routes.

- [x] **Step 6: Run HTTP tests**

Run:

```bash
.venv/bin/pytest tests/transports/test_http_aoi_intents.py tests/test_openapi_schema_quality.py -q
```

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add marivo/transports/http/sessions.py marivo/transports/http/models.py tests/transports/test_http_aoi_intents.py tests/test_openapi_schema_quality.py
git commit -m "feat: expose AOI atomic HTTP envelope contract"
```

## Task 8: MCP DTO to AOI Request Adapter

**Files:**
- Modify: `marivo/transports/mcp/tools/intents.py`
- Modify: `marivo/transports/mcp/tools/schemas.py`
- Create: `tests/transports/test_mcp_aoi_adapter.py`
- Test: `tests/transports/test_mcp_aoi_adapter.py`

- [x] **Step 1: Write failing MCP adapter tests**

Create `tests/transports/test_mcp_aoi_adapter.py`:

```python
from __future__ import annotations

from marivo.contracts.generated import aoi
from marivo.transports.mcp.tools.intents import (
    to_aoi_compare_request,
    to_aoi_observe_request,
)


def test_mcp_observe_dto_converts_to_aoi_request() -> None:
    request = to_aoi_observe_request(
        metric="metric.dau",
        time_scope={
            "field": "event_time",
            "start": "2026-05-01T00:00:00+00:00",
            "end": "2026-05-02T00:00:00+00:00",
        },
        granularity="day",
        dimensions=None,
        filter_expression=None,
    )

    assert isinstance(request, aoi.Observe1)
    assert request.metric == "metric.dau"
    assert request.granularity == "day"


def test_mcp_compare_dto_converts_to_aoi_request() -> None:
    request = to_aoi_compare_request(
        left_artifact_id="art_left",
        right_artifact_id="art_right",
        compare_type="normal",
    )

    assert isinstance(request, aoi.Compare)
    assert request.left_artifact_id == "art_left"
    assert request.right_artifact_id == "art_right"
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/transports/test_mcp_aoi_adapter.py -q
```

Expected: FAIL because conversion helpers do not exist.

- [x] **Step 3: Add MCP conversion helpers**

Add to `marivo/transports/mcp/tools/intents.py`:

```python
from marivo.contracts.generated import aoi
```

Add helpers above `register_observe()`:

```python
def to_aoi_observe_request(
    *,
    metric: str,
    time_scope: dict[str, Any],
    granularity: str | None,
    dimensions: list[str] | None,
    filter_expression: dict[str, Any] | None,
) -> aoi.Observe1:
    return aoi.Observe1.model_validate(
        {
            "metric": metric,
            "time_scope": time_scope,
            "filter": filter_expression,
            "granularity": granularity,
            "dimensions": dimensions,
        }
    )


def to_aoi_compare_request(
    *,
    left_artifact_id: str,
    right_artifact_id: str,
    compare_type: str = "normal",
) -> aoi.Compare:
    return aoi.Compare.model_validate(
        {
            "left_artifact_id": left_artifact_id,
            "right_artifact_id": right_artifact_id,
            "compare_type": compare_type,
        }
    )
```

Add equivalent helpers:

```python
def to_aoi_decompose_request(*, compare_artifact_id: str, dimension: str, limit: int | None) -> aoi.Decompose:
    return aoi.Decompose.model_validate(
        {"compare_artifact_id": compare_artifact_id, "dimension": dimension, "limit": limit}
    )


def to_aoi_forecast_request(*, source_artifact_id: str, horizon: int, profile: str | None) -> aoi.Forecast:
    return aoi.Forecast.model_validate(
        {"source_artifact_id": source_artifact_id, "horizon": horizon, "profile": profile}
    )
```

- [x] **Step 4: Update MCP tool calls to pass AOI requests**

In `register_observe`, replace `params` construction with:

```python
        aoi_request = to_aoi_observe_request(
            metric=metric,
            time_scope=time_scope.model_dump(),
            granularity=granularity,
            dimensions=dimensions,
            filter_expression=None,
        )
        return await call_runtime(runtime.observe, session_id=session_id, request=aoi_request)
```

In `register_compare`, change arguments to artifact ids:

```python
    async def compare(
        session_id: str,
        left_artifact_id: str,
        right_artifact_id: str,
        compare_type: Literal[
            "normal",
            "yoy",
            "mom",
            "wow",
            "holiday_aligned_yoy",
            "weekday_aligned_yoy",
            "weekday_aligned_mom",
        ] = "normal",
    ) -> dict[str, Any]:
        aoi_request = to_aoi_compare_request(
            left_artifact_id=left_artifact_id,
            right_artifact_id=right_artifact_id,
            compare_type=compare_type,
        )
        return await call_runtime(runtime.compare, session_id=session_id, request=aoi_request)
```

Update `decompose` and `forecast` similarly. Leave `attribute`, `diagnose`, and `validate` on DTO dicts until Task 9.

- [x] **Step 5: Verify async bridge supports new keyword name**

Open `marivo/transports/mcp/tools/_async_bridge.py`. If `call_runtime()` only accepts `params`, change it to forward arbitrary keyword arguments:

```python
async def call_runtime(fn: Callable[..., Any], **kwargs: Any) -> dict[str, Any]:
    try:
        result = fn(**kwargs)
        if hasattr(result, "model_dump"):
            result = result.model_dump(exclude_none=True)
        return {"data": result, "error": None}
    except Exception as exc:
        return {"data": None, "error": {"type": exc.__class__.__name__, "message": str(exc)}}
```

- [x] **Step 6: Run MCP adapter tests**

Run:

```bash
.venv/bin/pytest tests/transports/test_mcp_aoi_adapter.py -q
```

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add marivo/transports/mcp/tools/intents.py marivo/transports/mcp/tools/schemas.py marivo/transports/mcp/tools/_async_bridge.py tests/transports/test_mcp_aoi_adapter.py
git commit -m "feat: convert MCP atomic DTOs to AOI requests"
```

## Task 9: Derived Compatibility Envelopes

**Files:**
- Modify: `marivo/runtime/intents/attribute.py`
- Modify: `marivo/runtime/intents/diagnose.py`
- Modify: `marivo/runtime/intents/validate.py`
- Create: `tests/runtime/test_derived_aoi_envelopes.py`
- Test: `tests/runtime/test_derived_aoi_envelopes.py`, existing derived tests that remain in scope

- [x] **Step 1: Write failing derived envelope tests**

Create `tests/runtime/test_derived_aoi_envelopes.py`:

```python
from __future__ import annotations

from unittest.mock import Mock

import pytest

from marivo.runtime.intents.validate import run_validate_intent


def test_validate_returns_envelope_with_product_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = Mock()
    runtime.commit_artifact_with_extraction.return_value = "art_validation"
    monkeypatch.setattr(
        "marivo.runtime.intents.validate.run_test_intent",
        Mock(
            return_value={
                "intent_type": "test",
                "step_type": "test",
                "step_ref": {"session_id": "s1", "step_id": "step_test", "step_type": "test"},
                "artifact_id": "art_test",
                "result": {
                    "artifact_id": "art_test",
                    "result": {
                        "statistic": 2.1,
                        "p_value": 0.03,
                        "decision": {"reject_null": True},
                        "assumption_notes": [],
                    },
                },
            }
        ),
    )

    result = run_validate_intent(
        runtime,
        "s1",
        {
            "metric": "metric.dau",
            "left": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-05-01T00:00:00+00:00",
                    "end": "2026-05-02T00:00:00+00:00",
                },
                "filter": None,
            },
            "right": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-04-01T00:00:00+00:00",
                    "end": "2026-04-02T00:00:00+00:00",
                },
                "filter": None,
            },
        },
    )

    assert result["intent_type"] == "validate"
    assert result["result"]["bundle_type"] == "validation_bundle"
    assert result["product_metadata"]["derived_operation"] == "validate"
    assert result["product_metadata"]["aoi_artifacts"][0]["artifact_id"] == "art_test"


def test_validate_maps_orchestration_failure_to_envelope_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = Mock()
    runtime.commit_artifact_with_extraction.return_value = "art_validation_failure"
    monkeypatch.setattr(
        "marivo.runtime.intents.validate.run_test_intent",
        Mock(side_effect=ValueError("test failed")),
    )

    result = run_validate_intent(runtime, "s1", {"metric": "metric.dau", "left": {}, "right": {}})

    assert result["intent_type"] == "validate"
    assert result["result"]["bundle_type"] == "validation_bundle"
    assert result["product_metadata"]["status"] == "failed"
    assert result["product_metadata"]["issues"][0]["code"] == "derived_orchestration_failed"
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/runtime/test_derived_aoi_envelopes.py -q
```

Expected: FAIL because derived intents still return old bundle contracts.

- [x] **Step 3: Implement derived bundle shape**

In each derived runner, return this envelope-compatible dict shape:

```python
{
    "intent_type": "validate",
    "step_type": "validate",
    "step_ref": {"session_id": session_id, "step_id": step_id, "step_type": "validate"},
    "artifact_id": artifact_id,
    "result": {
        "bundle_type": "validation_bundle",
        "aoi_artifacts": [aoi_artifact_dump],
    },
    "product_metadata": {
        "derived_operation": "validate",
        "status": "succeeded",
        "issues": [],
        "aoi_artifacts": [aoi_artifact_dump],
    },
}
```

For orchestration failure, use:

```python
{
    "bundle_type": "validation_bundle",
    "aoi_artifacts": [],
}
```

And:

```python
{
    "derived_operation": "validate",
    "status": "failed",
    "issues": [
        {
            "code": "derived_orchestration_failed",
            "message": str(exc),
        }
    ],
    "aoi_artifacts": [],
}
```

Apply equivalent `bundle_type` values:

- `attribute_bundle`
- `diagnosis_bundle`
- `validation_bundle`

- [x] **Step 4: Run derived focused tests**

Run:

```bash
.venv/bin/pytest tests/runtime/test_derived_aoi_envelopes.py tests/test_intent_attribute.py tests/test_intent_validate.py -q
```

Expected: PASS after old derived assertions are rewritten to bundle/envelope semantics.

- [x] **Step 5: Commit**

```bash
git add marivo/runtime/intents/attribute.py marivo/runtime/intents/diagnose.py marivo/runtime/intents/validate.py tests/runtime/test_derived_aoi_envelopes.py tests/test_intent_attribute.py tests/test_intent_validate.py
git commit -m "feat: return derived AOI bundle envelopes"
```

## Task 10: Remove Target-Path Legacy Contract Dependencies

**Files:**
- Modify: `tests/test_intent_api.py`
- Modify: `tests/runtime/test_runtime_intent_dispatch.py`
- Modify: `tests/test_openapi_schema_quality.py`
- Modify: `marivo/transports/http/models.py`
- Modify: `marivo/transports/http/sessions.py`
- Test: focused HTTP/runtime suites

- [x] **Step 1: Search for legacy target-path usage**

Run:

```bash
rg -n "to_legacy_dict\\(|ObserveRequest|ObserveResponse|CompareRequest|CompareResponse|DecomposeRequest|DecomposeResponse|CorrelateRequest|CorrelateResponse|DetectRequest|DetectResponse|ForecastRequest|ForecastResponse|IntentTestRequest|IntentTestResponse" marivo tests
```

Expected remaining allowed matches:

- `to_legacy_dict()` only in `marivo/contracts/envelope.py`.
- Old request/response classes only if they are no longer used by atomic route decorators or target-state tests.

- [x] **Step 2: Rewrite old HTTP model tests**

In `tests/test_intent_api.py`, delete model-only tests for removed hand-written atomic request classes and replace with generated AOI validation tests:

```python
from pydantic import ValidationError
from marivo.contracts.generated import aoi


def test_aoi_observe_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        aoi.Observe1.model_validate(
            {
                "metric": "metric.dau",
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-05-01T00:00:00+00:00",
                    "end": "2026-05-02T00:00:00+00:00",
                },
                "filter": None,
                "unexpected": True,
            }
        )


def test_aoi_compare_requires_artifact_ids() -> None:
    with pytest.raises(ValidationError):
        aoi.Compare.model_validate({"left_ref": {"step_id": "step_1"}, "right_ref": {"step_id": "step_2"}})
```

- [x] **Step 3: Remove atomic imports from HTTP models if unused**

In `marivo/transports/http/models.py`, remove atomic request/response classes that are no longer imported by any runtime code:

- `ObserveRequest`, `ObserveResponse`
- `CompareRequest`, `CompareResponse`
- `DecomposeRequest`, `DecomposeResponse`
- `CorrelateRequest`, `CorrelateResponse`
- `DetectRequest`, `DetectResponse`
- `ForecastRequest`, `ForecastResponse`
- `IntentTestRequest`, `IntentTestResponse`

Keep session, state, runtime-status, proposition, and derived compatibility models that are still used.

- [x] **Step 4: Run legacy usage search again**

Run:

```bash
rg -n "to_legacy_dict\\(|ObserveRequest|ObserveResponse|CompareRequest|CompareResponse|DecomposeRequest|DecomposeResponse|CorrelateRequest|CorrelateResponse|DetectRequest|DetectResponse|ForecastRequest|ForecastResponse|IntentTestRequest|IntentTestResponse" marivo tests
```

Expected: no target-path usage outside explicitly retained derived compatibility or removed-class references.

- [x] **Step 5: Run focused suites**

Run:

```bash
.venv/bin/pytest tests/test_intent_api.py tests/transports/test_http_aoi_intents.py tests/runtime/test_runtime_intent_dispatch.py tests/test_openapi_schema_quality.py -q
```

Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add marivo/transports/http/models.py marivo/transports/http/sessions.py tests/test_intent_api.py tests/runtime/test_runtime_intent_dispatch.py tests/test_openapi_schema_quality.py
git commit -m "refactor: remove legacy atomic intent contract path"
```

## Task 11: Full Verification and Documentation Update

**Files:**
- Modify: `docs/superpowers/specs/2026-05-13-aoi-generated-model-runtime-cutover-design.md`
- Modify: affected API docs if `rg -n "/sessions/\\{session_id\\}/intents" docs marivo` shows stale examples
- Test: full focused and repository validation

- [x] **Step 1: Run stale doc search**

Run:

```bash
rg -n "left_ref|right_ref|source_ref|compare_ref|step_id|ObserveRequest|CompareRequest|to_legacy_dict|flat response|/sessions/\\{session_id\\}/intents" docs marivo tests
```

Expected: stale atomic examples are identified; step refs may remain only in non-atomic session/state docs or migration notes.

- [x] **Step 2: Update docs to AOI artifact-id examples**

In `docs/superpowers/specs/2026-05-13-aoi-generated-model-runtime-cutover-design.md`, add this implementation note under HTTP boundary:

```markdown
Implementation note:

- Atomic HTTP examples must use AOI artifact id references:
  - `compare.left_artifact_id`
  - `compare.right_artifact_id`
  - `decompose.compare_artifact_id`
  - `correlate.left_artifact_id`
  - `correlate.right_artifact_id`
  - `forecast.source_artifact_id`
- Step refs remain Marivo execution metadata and are not valid AOI atomic request fields.
```

- [x] **Step 3: Run focused verification**

Run:

```bash
.venv/bin/pytest tests/contracts/test_aoi_runtime_contract.py tests/runtime/test_aoi_lowering.py tests/runtime/test_aoi_intent_execution.py tests/runtime/test_aoi_artifact_references.py tests/transports/test_http_aoi_intents.py tests/transports/test_mcp_aoi_adapter.py tests/runtime/test_derived_aoi_envelopes.py tests/adapters/test_artifact_store_artifact_id_lookup.py -q
```

Expected: PASS.

- [x] **Step 4: Run repository validation**

Run:

```bash
make test
```

Expected: PASS. If unrelated pre-existing failures appear, record the exact failing test names and rerun the focused AOI suites to prove this change set.

- [x] **Step 5: Run typecheck**

Run:

```bash
make typecheck
```

Expected: PASS.

- [x] **Step 6: Commit docs and final cleanup**

```bash
git add docs/superpowers/specs/2026-05-13-aoi-generated-model-runtime-cutover-design.md docs tests marivo
git commit -m "docs: align AOI runtime cutover implementation notes"
```

## Self-Review

### Spec Coverage

- HTTP atomic AOI request and `ExecutionEnvelope` response: Task 7 and Task 10.
- MCP agent-friendly DTO with internal AOI conversion: Task 8.
- Runtime typed AOI boundary: Task 1, Task 3, Task 4.
- Artifact commit validation through AOI generated artifact model: Task 5.
- `artifact_id` as canonical portable handle with session-scoped lookup: Task 2 and Task 6.
- Derived `attribute` / `diagnose` / `validate` as non-AOI bundle envelopes assembled from AOI artifacts: Task 9.
- `to_legacy_dict()` not target-state: Task 1, Task 5, Task 10.
- Registry mismatch, cross-session lookup, derived orchestration failure, artifact reference regression tests: Task 1, Task 2, Task 6, Task 9.
- Performance non-goal for batch resolver/cache: preserved by Task 2 single-lookup design.

### Placeholder Scan

This plan avoids placeholder markers, open-ended validation instructions, and shortcut references to other tasks. Each implementation task names concrete files, test commands, expected failure mode, and code shape.

### Type Consistency

- AOI request union names match `marivo/contracts/generated/aoi.py`: `Compare`, `Decompose`, `Correlate`, `Detect`, `Test`, `Forecast`, `Observe1`, `Observe2`, `Observe3`, `Observe4`.
- AOI artifact names match generated code: `Artifact1`, `Artifact2`.
- New artifact lookup method name is consistently `resolve_artifact_by_id(session_id, artifact_id)`.
- HTTP and MCP atomic paths call runtime with `request=<AOI model>` instead of `params=<dict>`.
