# Semantic Layer Revision Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 semantic layer revision management 第一阶段落成 agent-friendly、server-classified、可追踪/可回溯的 `metric.*` / `binding.*` / `compiler_profile.*` 最小闭环。

**Architecture:** 以 `metric.*` 为主线先移除 agent 必填 `compatibility`，新增 server canonical diff + classification + dependency action plan；再把 breaking metric revision 的依赖处理接到 binding derive 和 compiler profile revalidate。所有快捷路径必须返回与分步流程等价的 classification、validation evidence 和 activation gate 结果。

**Tech Stack:** FastAPI route in `app/api/semantic.py`, Pydantic models in `app/api/models`, semantic services in `app/semantic_service`, readiness in `app/semantic_readiness`, metadata DDL in `app/storage/schema.py`, tests through repository entrypoints only.

---

## 背景

当前 `metric.*` 已经有 revision v1，但 `POST /semantic/metrics/{ref}/revisions` 仍要求 agent 提交 `compatibility = compatible | breaking`。这会让 agent 成为兼容性事实来源，和目标方案“server canonical diff 确定性分类并生成 dependency plan”冲突。

本计划只实施第一阶段闭环：

- `metric.*`：server canonical diff、`classified_compatibility`、revision activation gate。
- `binding.*`：基于 required action 派生 binding revision，默认复用 carrier/time/import/已满足 coverage，只提交受控 delta。
- `compiler_profile.*`：对 subject / metric / binding revision drift 生成 `reuse_after_revalidate` action，并支持 revalidate。

不在本计划内：

- 不开放 entity / dimension / time / process / predicate / enum 的完整同构 revision API。
- 不实现 operator override。
- 不实现异步 classification job。
- 不做旧 metadata 在线迁移、backfill 或双写；fresh-init 为默认边界。
- 不把 MCP 或本地 DB 私有操作作为能力前提。

## 文件结构

- Create: `app/semantic_revision/__init__.py`
  语义 revision 分类和 action plan 的 package 入口。
- Create: `app/semantic_revision/types.py`
  定义 `RevisionClassificationResult`、`RevisionDiffEntry`、`RequiredAction`、`CompletionCriteria` 等内部 typed dict / dataclass。
- Create: `app/semantic_revision/metric_diff.py`
  metric canonicalizer 和 deterministic classifier。
- Create: `app/semantic_revision/dependency_plan.py`
  第一阶段依赖发现和 action plan 生成：binding / compiler profile。
- Modify: `app/api/models/metric.py`
  删除 revision create 的必填 `compatibility`，新增 optional guardrail 和 server 输出字段。
- Modify: `app/api/models/binding.py`
  新增 binding derive request / response 所需模型。
- Modify: `app/api/models/compatibility_profile.py`
  新增 profile revalidate request 所需模型；若无需 request body，至少补充 response contract 字段说明。
- Modify: `app/api/semantic.py`
  接入 metric revision 新 contract、binding derive route、profile revalidate route。
- Modify: `app/semantic_service/typed_objects.py`
  metric revision create / validate / activate 接入 server classification 和 action gate。
- Modify: `app/semantic_service/binding.py`
  实现 binding revision derive，避免 agent 复制完整 grounding JSON。
- Modify: `app/semantic_service/compatibility_profile.py`
  实现 revision drift 下的 revalidate surface。
- Modify: `app/semantic_service/common.py`
  补充 row-to-response 字段和依赖扫描辅助函数。
- Modify: `app/storage/schema.py`
  fresh-init DDL 增补 required action / dependency plan 所需持久化字段或表。
- Modify: `docs/api/semantic.md`
  更新 metric revision、binding derive、profile revalidate HTTP contract。
- Modify: `docs/agent-guide.md`
  只增加 repo-wide 的短规则：agent 不判断 compatibility，优先 revision / derive / revalidate。
- Test: `tests/test_semantic.py`
  现有 metric revision flow 更新为 server-classified contract。
- Test: `tests/test_metric_revision_classification.py`
  metric canonical diff 的 focused unit tests。
- Test: `tests/test_semantic_revision_dependency_plan.py`
  dependency action plan focused tests。
- Test: `tests/test_semantic_typed_api.py`
  binding derive / profile revalidate API flow。
- Test: `tests/test_step_metadata.py`
  artifact / step semantic snapshot 冻结 revision 的回归覆盖。

## Task 1: Metric Revision Public Contract

**Files:**
- Modify: `app/api/models/metric.py`
- Modify: `app/api/semantic.py`
- Test: `tests/test_semantic.py`
- Docs: `docs/api/semantic.md`

- [ ] **Step 1: 写失败测试，证明 create revision 不再要求 `compatibility`**

在 `tests/test_semantic.py` 的 metric revision flow 中新增或替换请求：

```python
revision_resp = self.client.post(
    f"/semantic/metrics/{metric_ref}/revisions",
    json={
        "base_revision": 1,
        "change_summary": "Fix display label",
        "expected_change_scope": "display_metadata",
        "replacement": replacement,
    },
)
self.assertEqual(revision_resp.status_code, 200, revision_resp.text)
self.assertEqual(revision_resp.json()["classified_compatibility"], "compatible")
self.assertNotIn("compatibility", revision_resp.json())
self.assertEqual(revision_resp.json()["required_actions"], [])
self.assertTrue(revision_resp.json()["can_activate_now"])
```

- [ ] **Step 2: 运行失败测试**

Run:

```bash
.venv/bin/pytest tests/test_semantic.py::SemanticApiTest::test_metric_revision_flow -q
```

Expected: `422`，因为 `MetricRevisionCreateRequest.compatibility` 仍是必填字段。

- [ ] **Step 3: 修改 request / response 模型**

在 `app/api/models/metric.py` 中把 request 改成：

```python
class MetricRevisionCreateRequest(BaseModel):
    """Request to create a draft revision for an existing typed metric identity."""

    base_revision: int = Field(
        ge=1,
        description="Latest active metric revision the replacement is based on.",
    )
    change_summary: str = Field(
        min_length=1,
        description="Human-readable summary of why this revision is being created.",
    )
    expected_change_scope: Literal[
        "display_metadata",
        "unit_display_metadata",
        "semantic_contract",
        "grounding_affecting",
    ] | None = Field(
        default=None,
        description="Non-authoritative guardrail; server classification remains authoritative.",
    )
    expected_compatibility: Literal["compatible", "breaking"] | None = Field(
        default=None,
        description="Non-authoritative guardrail; rejected when server classification exceeds it.",
    )
    accept_classified_compatibility: bool = Field(
        default=False,
        description="Allows persisting the draft after a previous guardrail mismatch.",
    )
    replacement: TypedMetricCreateRequest = Field(
        description="Full replacement typed metric contract for the new revision.",
    )
```

在 `TypedMetricListItem` 和 `TypedMetricResponse` 中新增：

```python
classified_compatibility: Literal["compatible", "breaking"] | None = Field(
    default=None,
    description="Server-classified compatibility for this revision.",
)
diff_summary: list[dict[str, object]] = Field(
    default_factory=list,
    description="Canonical semantic diff entries generated by the server.",
)
affected_dependents: list[dict[str, object]] = Field(
    default_factory=list,
    description="Dependents affected by this revision plan.",
)
required_actions: list[dict[str, object]] = Field(
    default_factory=list,
    description="Machine-verifiable actions required before activation.",
)
can_activate_now: bool = Field(
    default=False,
    description="Derived activation precheck for the current validation snapshot.",
)
```

保留 `revision_compatibility` 作为短期 read compatibility 字段时，description 必须改为：

```python
default=None, description="Deprecated compatibility field; mirrors server classification."
```

- [ ] **Step 4: 暂时在 service 中回填 compatible 以让 contract 测试通过**

在 `app/semantic_service/typed_objects.py::create_metric_revision` 中先用固定值打通响应：

```python
classified_compatibility = "compatible"
required_actions: list[dict[str, object]] = []
can_activate_now = True
```

插入 DB 时把 `payload.compatibility` 改为 `classified_compatibility`。

- [ ] **Step 5: 更新 `docs/api/semantic.md`**

把 v1 payload 示例改成：

```json
{
  "base_revision": 1,
  "change_summary": "Fix unit label from seconds to milliseconds.",
  "expected_change_scope": "display_metadata",
  "replacement": {
    "header": {
      "metric_ref": "metric.avg_blocked_time",
      "metric_contract_version": "v1"
    },
    "payload": {}
  }
}
```

并明确 `classified_compatibility` 是 server 输出，不是 request 字段。

- [ ] **Step 6: 运行验证**

Run:

```bash
.venv/bin/pytest tests/test_semantic.py::SemanticApiTest::test_metric_revision_flow -q
make typecheck
```

Expected: targeted test passes; typecheck passes.

## Task 2: Metric Canonical Diff and Classification

**Files:**
- Create: `app/semantic_revision/__init__.py`
- Create: `app/semantic_revision/types.py`
- Create: `app/semantic_revision/metric_diff.py`
- Modify: `app/semantic_service/typed_objects.py`
- Test: `tests/test_metric_revision_classification.py`

- [ ] **Step 1: 写 canonical diff 单测**

Create `tests/test_metric_revision_classification.py`:

```python
from __future__ import annotations

import copy
import unittest

from app.semantic_revision.metric_diff import classify_metric_revision


def _base_metric() -> dict[str, object]:
    return {
        "header": {
            "metric_ref": "metric.avg_blocked_time",
            "display_name": "Avg blocked time",
            "description": "Original description",
            "metric_family": "average_metric",
            "population_subject_ref": "entity.user",
            "observed_entity_ref": "entity.user",
            "observation_grain_ref": "grain.day",
            "sample_kind": "event",
            "value_semantics": "duration",
            "aggregation_scope": "per_subject",
            "primary_time_ref": "time_surface.event_date",
            "additivity_constraints": {"dimension_policy": "subset"},
            "default_predicate_refs": ["predicate.active_user"],
            "metric_contract_version": "metric.v1",
        },
        "payload": {
            "metric_family": "average_metric",
            "measure": {
                "measure_ref": "measure.blocked_time",
                "semantics": "blocked time",
                "unit": {"semantic_unit": "millisecond", "display_label": "ms"},
            },
        },
    }


class MetricRevisionClassificationTest(unittest.TestCase):
    def test_display_metadata_change_is_compatible(self) -> None:
        base = _base_metric()
        replacement = copy.deepcopy(base)
        replacement["header"]["description"] = "Clearer description"

        result = classify_metric_revision(base=base, replacement=replacement)

        self.assertEqual(result.classified_compatibility, "compatible")
        self.assertEqual(result.required_actions, [])
        self.assertTrue(result.can_activate_now)

    def test_required_input_contract_change_is_breaking(self) -> None:
        base = _base_metric()
        replacement = copy.deepcopy(base)
        replacement["payload"]["required_inputs"] = [
            {"input_ref": "metric_input.denominator", "required": True}
        ]

        result = classify_metric_revision(base=base, replacement=replacement)

        self.assertEqual(result.classified_compatibility, "breaking")
        self.assertTrue(any(item.path == "payload.required_inputs" for item in result.diff_summary))
        self.assertFalse(result.can_activate_now)

    def test_default_predicate_order_is_normalized(self) -> None:
        base = _base_metric()
        replacement = copy.deepcopy(base)
        base["header"]["default_predicate_refs"] = [
            "predicate.beta",
            "predicate.alpha",
        ]
        replacement["header"]["default_predicate_refs"] = [
            "predicate.alpha",
            "predicate.beta",
        ]

        result = classify_metric_revision(base=base, replacement=replacement)

        self.assertEqual(result.classified_compatibility, "compatible")
        self.assertEqual(result.diff_summary, [])
```

- [ ] **Step 2: 运行失败测试**

Run:

```bash
.venv/bin/pytest tests/test_metric_revision_classification.py -q
```

Expected: import failure for `app.semantic_revision.metric_diff`.

- [ ] **Step 3: 新增 typed result**

Create `app/semantic_revision/types.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Compatibility = Literal["compatible", "breaking"]


@dataclass(frozen=True)
class RevisionDiffEntry:
    path: str
    change_type: str
    compatibility: Compatibility
    reason: str

    def to_json(self) -> dict[str, object]:
        return {
            "path": self.path,
            "change_type": self.change_type,
            "compatibility": self.compatibility,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RequiredAction:
    action_id: str
    action: str
    target_ref: str
    target_revision: int | None
    depends_on: list[str]
    blocking: bool
    action_status: Literal["pending", "satisfied", "failed"]
    completion_criteria: dict[str, object]
    validation_evidence: dict[str, object] | None = None
    reason: str = ""

    def to_json(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "action": self.action,
            "target_ref": self.target_ref,
            "target_revision": self.target_revision,
            "depends_on": self.depends_on,
            "blocking": self.blocking,
            "action_status": self.action_status,
            "completion_criteria": self.completion_criteria,
            "validation_evidence": self.validation_evidence,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RevisionClassificationResult:
    classified_compatibility: Compatibility
    diff_summary: list[RevisionDiffEntry] = field(default_factory=list)
    affected_dependents: list[dict[str, object]] = field(default_factory=list)
    required_actions: list[RequiredAction] = field(default_factory=list)

    @property
    def can_activate_now(self) -> bool:
        return not any(action.blocking and action.action_status != "satisfied" for action in self.required_actions)
```

- [ ] **Step 4: 实现 metric canonical diff**

Create `app/semantic_revision/metric_diff.py`:

```python
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .types import RevisionClassificationResult, RevisionDiffEntry

_DISPLAY_PATHS = {
    "header.display_name",
    "header.description",
    "header.owner",
    "header.tags",
    "payload.unit.display_label",
}

_BREAKING_PATHS = {
    "header.metric_ref",
    "header.metric_family",
    "header.value_semantics",
    "header.observed_entity_ref",
    "header.population_subject_ref",
    "header.primary_time_ref",
    "header.observation_grain_ref",
    "header.additivity_constraints",
    "header.default_predicate_refs",
    "payload.metric_family",
    "payload.required_inputs",
}


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        normalized = [_normalize(item) for item in value]
        if all(isinstance(item, str) for item in normalized):
            return sorted(normalized)
        return normalized
    return value


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        flattened: dict[str, Any] = {}
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(_flatten(child, path))
        return flattened
    return {prefix: value}


def _root_path(path: str) -> str:
    if path.startswith("payload.required_inputs"):
        return "payload.required_inputs"
    if path.startswith("header.additivity_constraints"):
        return "header.additivity_constraints"
    if path.startswith("header.default_predicate_refs"):
        return "header.default_predicate_refs"
    return path


def classify_metric_revision(
    *, base: dict[str, object], replacement: dict[str, object]
) -> RevisionClassificationResult:
    canonical_base = _normalize(deepcopy(base))
    canonical_replacement = _normalize(deepcopy(replacement))
    base_flat = _flatten(canonical_base)
    replacement_flat = _flatten(canonical_replacement)
    paths = sorted(set(base_flat) | set(replacement_flat))
    diffs: list[RevisionDiffEntry] = []

    for path in paths:
        if base_flat.get(path) == replacement_flat.get(path):
            continue
        canonical_path = _root_path(path)
        compatibility = "breaking" if canonical_path in _BREAKING_PATHS else "compatible"
        change_type = (
            "semantic_contract_changed" if compatibility == "breaking" else "display_metadata"
        )
        diffs.append(
            RevisionDiffEntry(
                path=canonical_path,
                change_type=change_type,
                compatibility=compatibility,
                reason=(
                    "Semantic contract change requires dependency validation."
                    if compatibility == "breaking"
                    else "Display-only change does not alter runtime semantics."
                ),
            )
        )

    deduped: list[RevisionDiffEntry] = []
    seen: set[tuple[str, str]] = set()
    for diff in diffs:
        key = (diff.path, diff.compatibility)
        if key not in seen:
            deduped.append(diff)
            seen.add(key)

    classified = "breaking" if any(diff.compatibility == "breaking" for diff in deduped) else "compatible"
    return RevisionClassificationResult(
        classified_compatibility=classified,
        diff_summary=deduped,
    )
```

- [ ] **Step 5: 把 classifier 接入 metric revision create / validate**

在 `app/semantic_service/typed_objects.py` 中将 base row 和 replacement 组装为 `{"header": ..., "payload": ...}`，调用 `classify_metric_revision(...)`。create 和 validate 都必须重新计算，不信任 create 时的旧快照。

- [ ] **Step 6: 运行验证**

Run:

```bash
.venv/bin/pytest tests/test_metric_revision_classification.py tests/test_semantic.py::SemanticApiTest::test_metric_revision_flow -q
make typecheck
```

Expected: tests pass; typecheck passes.

## Task 3: Guardrail Mismatch and Activation Gate

**Files:**
- Modify: `app/semantic_service/typed_objects.py`
- Test: `tests/test_semantic.py`

- [ ] **Step 1: 写 guardrail mismatch 测试**

Add to `tests/test_semantic.py`:

```python
def test_metric_revision_guardrail_mismatch_does_not_persist_draft(self) -> None:
    entity_ref = "entity.revision_guardrail_user"
    metric_ref = "metric.revision_guardrail"
    self._create_published_entity(entity_ref)
    create_resp = self.client.post("/semantic/metrics", json=self._metric_create_payload(metric_ref, entity_ref))
    self.assertEqual(create_resp.status_code, 200, create_resp.text)
    self.assertEqual(self.client.post(f"/semantic/metrics/{create_resp.json()['metric_contract_id']}/publish").status_code, 200)

    replacement = self._metric_create_payload(metric_ref, entity_ref)
    replacement["header"]["primary_time_ref"] = "time_surface.changed_date"
    mismatch = self.client.post(
        f"/semantic/metrics/{metric_ref}/revisions",
        json={
            "base_revision": 1,
            "change_summary": "Change time axis",
            "expected_compatibility": "compatible",
            "replacement": replacement,
        },
    )

    self.assertEqual(mismatch.status_code, 409, mismatch.text)
    detail = mismatch.json()["detail"]
    self.assertEqual(detail["error"]["code"], "revision_guardrail_mismatch")
    self.assertEqual(detail["classified_compatibility"], "breaking")
    history = self.client.get(f"/semantic/metrics/{metric_ref}/revisions")
    self.assertEqual(history.json()["total"], 1)
```

- [ ] **Step 2: 实现 mismatch error**

在 `create_metric_revision` 分类后加入：

```python
if (
    payload.expected_compatibility == "compatible"
    and classification.classified_compatibility == "breaking"
    and not payload.accept_classified_compatibility
):
    raise self._conflict_error(
        "Revision guardrail mismatch",
        field_path="expected_compatibility",
        code="revision_guardrail_mismatch",
        remediation={
            "metric_ref": metric_ref,
            "expected_compatibility": payload.expected_compatibility,
            "classified_compatibility": classification.classified_compatibility,
            "recommended_action": "retry with accept_classified_compatibility=true after inspecting dependency plan",
        },
    )
```

若 `_conflict_error` 当前不接受 `code`，先新增可选参数，默认仍为现有 `semantic_ref_conflict`，避免影响旧调用。

- [ ] **Step 3: 写 activation blocker 测试**

在 breaking revision 有 blocking required actions 时：

```python
activate_resp = self.client.post(f"/semantic/metrics/{metric_ref}/revisions/2/activate")
self.assertEqual(activate_resp.status_code, 409, activate_resp.text)
self.assertIn("required_actions", activate_resp.text)
```

- [ ] **Step 4: 实现 fail-closed gate**

在 `activate_metric_revision` 中 validate 并重新分类；如果存在 blocking action 未满足，返回 409，不更新 latest active。

- [ ] **Step 5: 运行验证**

Run:

```bash
.venv/bin/pytest tests/test_semantic.py::SemanticApiTest::test_metric_revision_guardrail_mismatch_does_not_persist_draft -q
.venv/bin/pytest tests/test_semantic.py::SemanticApiTest::test_metric_revision_rejects_stale_base_revision_without_switching -q
make typecheck
```

Expected: tests pass; typecheck passes.

## Task 4: Dependency Action Plan for Binding and Compiler Profile

**Files:**
- Create: `app/semantic_revision/dependency_plan.py`
- Modify: `app/semantic_service/common.py`
- Modify: `app/semantic_service/typed_objects.py`
- Test: `tests/test_semantic_revision_dependency_plan.py`

- [ ] **Step 1: 写 dependency plan 单测**

Create `tests/test_semantic_revision_dependency_plan.py` with a published metric, a published metric binding, and a published compatibility profile. Create a breaking metric revision that adds `metric_input.denominator`; assert:

```python
self.assertEqual(revision["classified_compatibility"], "breaking")
self.assertFalse(revision["can_activate_now"])
self.assertTrue(
    any(action["action"] == "derive_revision" for action in revision["required_actions"])
)
self.assertTrue(
    any(action["action"] == "add_binding_coverage" for action in revision["required_actions"])
)
self.assertTrue(
    any(action["action"] == "reuse_after_revalidate" for action in revision["required_actions"])
)
```

- [ ] **Step 2: 实现依赖扫描**

Create `app/semantic_revision/dependency_plan.py`:

```python
from __future__ import annotations

from .types import RequiredAction, RevisionClassificationResult


def metric_revision_dependency_actions(
    *,
    metric_ref: str,
    metric_revision: int,
    classification: RevisionClassificationResult,
    binding_refs: list[str],
    profile_refs: list[str],
    missing_metric_inputs: list[str],
) -> list[RequiredAction]:
    if classification.classified_compatibility == "compatible":
        return []

    actions: list[RequiredAction] = []
    for binding_ref in binding_refs:
        derive_id = f"act_derive_{binding_ref.replace('.', '_')}"
        actions.append(
            RequiredAction(
                action_id=derive_id,
                action="derive_revision",
                target_ref=binding_ref,
                target_revision=None,
                depends_on=[],
                blocking=True,
                action_status="pending",
                completion_criteria={
                    "kind": "derived_binding_revision_validates",
                    "expected": {
                        "source_binding_ref": binding_ref,
                        "metric_ref": metric_ref,
                        "metric_revision": metric_revision,
                    },
                    "observed": None,
                },
                reason="Existing binding must be validated against the new metric revision.",
            )
        )
        for input_ref in missing_metric_inputs:
            actions.append(
                RequiredAction(
                    action_id=f"act_cover_{binding_ref.replace('.', '_')}_{input_ref.replace('.', '_')}",
                    action="add_binding_coverage",
                    target_ref=binding_ref,
                    target_revision=None,
                    depends_on=[derive_id],
                    blocking=True,
                    action_status="pending",
                    completion_criteria={
                        "kind": "binding_revision_covers_metric_input",
                        "expected": {
                            "metric_ref": metric_ref,
                            "metric_revision": metric_revision,
                            "coverage_target": input_ref,
                            "binding_revision_source": derive_id,
                        },
                        "observed": None,
                    },
                    reason="New required metric input is not covered by the current binding revision.",
                )
            )

    for profile_ref in profile_refs:
        actions.append(
            RequiredAction(
                action_id=f"act_revalidate_{profile_ref.replace('.', '_')}",
                action="reuse_after_revalidate",
                target_ref=profile_ref,
                target_revision=None,
                depends_on=[],
                blocking=True,
                action_status="pending",
                completion_criteria={
                    "kind": "compiler_profile_revalidates",
                    "expected": {
                        "profile_ref": profile_ref,
                        "metric_ref": metric_ref,
                        "metric_revision": metric_revision,
                    },
                    "observed": None,
                },
                reason="Compiler profile subject revision must be revalidated.",
            )
        )
    return actions
```

- [ ] **Step 3: 接入 service**

在 `create_metric_revision` 和 `validate_metric_revision` 分类后：

1. 扫描 `typed_bindings` 中 `bound_object_ref = metric_ref` 的 published binding。
2. 扫描 `compiler_compatibility_profiles` 中 `subject_ref = metric_ref` 的 published profile。
3. 从 diff 中识别新增 required input，生成 `missing_metric_inputs`。
4. 把 actions 合并进 response。

- [ ] **Step 4: 运行验证**

Run:

```bash
.venv/bin/pytest tests/test_semantic_revision_dependency_plan.py -q
make typecheck
```

Expected: tests pass; typecheck passes.

## Task 5: Binding Derive Revision

**Files:**
- Modify: `app/api/models/binding.py`
- Modify: `app/api/semantic.py`
- Modify: `app/semantic_service/binding.py`
- Test: `tests/test_semantic_typed_api.py`

- [ ] **Step 1: 写 binding derive API 测试**

Add a test that calls:

```python
derive_resp = self.client.post(
    f"/semantic/bindings/{binding_ref}/revisions/derive",
    json={
        "base_revision": 1,
        "source_action_id": "act_derive_binding_avg_blocked_time_primary",
        "target_metric_ref": metric_ref,
        "target_metric_revision": 2,
        "reuse_sections": ["carrier", "time", "imports", "satisfied_field_coverage"],
        "coverage_additions": [
            {
                "coverage_target": "metric_input.denominator",
                "field_ref": "field.blocked_time_denominator",
            }
        ],
    },
)
self.assertEqual(derive_resp.status_code, 200, derive_resp.text)
self.assertEqual(derive_resp.json()["revision"], 2)
self.assertEqual(derive_resp.json()["status"], "draft")
```

- [ ] **Step 2: 新增 request model**

In `app/api/models/binding.py`:

```python
class BindingCoverageAddition(BaseModel):
    coverage_target: str = Field(description="Metric input target, e.g. metric_input.denominator.")
    field_ref: str = Field(description="Existing field surface ref used to satisfy the target.")

    @field_validator("coverage_target")
    @classmethod
    def validate_target(cls, value: str) -> str:
        return validate_ref_prefix(value, "metric_input", "coverage_target")

    @field_validator("field_ref")
    @classmethod
    def validate_field(cls, value: str) -> str:
        return validate_ref_prefix(value, "field", "field_ref")


class BindingDeriveRevisionRequest(BaseModel):
    base_revision: int = Field(ge=1)
    source_action_id: str
    target_metric_ref: str
    target_metric_revision: int = Field(ge=1)
    reuse_sections: list[Literal["carrier", "time", "imports", "satisfied_field_coverage"]]
    coverage_additions: list[BindingCoverageAddition] = Field(default_factory=list)
```

- [ ] **Step 3: 实现 route**

In `app/api/semantic.py`:

```python
@router.post("/semantic/bindings/{binding_id_or_ref}/revisions/derive")
def derive_binding_revision(
    binding_id_or_ref: str,
    request: Request,
    payload: BindingDeriveRevisionRequest = Body(...),
) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.derive_binding_revision(
            binding_id_or_ref, payload
        )
    )
```

- [ ] **Step 4: 实现 service**

In `app/semantic_service/binding.py`, load base binding by id/ref, require `base_revision` match, deep-copy interface contract, append coverage additions to the metric input field binding section, insert a new `typed_bindings` row with same `binding_ref`, next `revision`, `status='draft'`, and replace contract for the new row.

- [ ] **Step 5: 运行验证**

Run:

```bash
.venv/bin/pytest tests/test_semantic_typed_api.py -q
make typecheck
```

Expected: tests pass; typecheck passes.

## Task 6: Compiler Profile Revalidate Surface

**Files:**
- Modify: `app/api/semantic.py`
- Modify: `app/semantic_service/compatibility_profile.py`
- Test: `tests/test_semantic_typed_api.py`

- [ ] **Step 1: 写 revalidate 测试**

Create a published metric profile pinned to revision 1, activate metric revision 2, then call:

```python
resp = self.client.post(
    f"/compiler/compatibility-profiles/{profile_ref}/revalidate",
    json={"subject_revision": 2},
)
self.assertEqual(resp.status_code, 200, resp.text)
self.assertEqual(resp.json()["subject_revision"], 2)
self.assertEqual(resp.json()["readiness_status"], "ready")
```

- [ ] **Step 2: 实现 route 和 service**

Route:

```python
@router.post("/compiler/compatibility-profiles/{profile_id_or_ref}/revalidate")
def revalidate_compatibility_profile(
    profile_id_or_ref: str,
    request: Request,
    payload: dict[str, object] = Body(default_factory=dict),
) -> dict[str, Any]:
    return _run_route_action(
        lambda: get_services(request).semantic_service.revalidate_compatibility_profile(
            profile_id_or_ref, payload
        )
    )
```

Service behavior:

1. Reject builtin calendar policy profiles.
2. Resolve profile by id/ref.
3. Validate current subject exists and is active.
4. Update `subject_revision` to requested revision or current active subject revision.
5. Return normal profile response with readiness recomputed.

- [ ] **Step 3: 运行验证**

Run:

```bash
.venv/bin/pytest tests/test_semantic_typed_api.py -q
make typecheck
```

Expected: tests pass; typecheck passes.

## Task 7: Artifact / Session Snapshot Regression

**Files:**
- Modify: `tests/test_step_metadata.py`
- Modify if needed: `app/analysis_core/compiler.py`
- Modify if needed: `app/service.py`

- [ ] **Step 1: 写 regression test**

Add a test that:

1. Executes an intent against metric revision 1.
2. Activates metric revision 2.
3. Reads the original step metadata.
4. Asserts stored semantic snapshot still contains revision 1.

Expected assertion shape:

```python
snapshot = step_metadata["semantic_snapshot"]
resolved = snapshot["resolved_refs"][metric_ref]
self.assertEqual(resolved["ref"], metric_ref)
self.assertEqual(resolved["revision"], 1)
self.assertIn("object_id", resolved)
```

- [ ] **Step 2: Fix only if failing**

If the existing snapshot already freezes `revision`, do not change runtime code. If it only stores stable ref, add revision/object id from semantic resolver output where step metadata is written.

- [ ] **Step 3: 运行验证**

Run:

```bash
.venv/bin/pytest tests/test_step_metadata.py -q
make typecheck
```

Expected: tests pass; typecheck passes.

## Task 8: Docs and Agent Guidance

**Files:**
- Modify: `docs/api/semantic.md`
- Modify: `docs/agent-guide.md`
- Modify: `plan/2026-04-28-semantic-layer-revision-management-optimization.zh.md` only if implementation decisions changed the design.

- [ ] **Step 1: Update API docs**

`docs/api/semantic.md` must state:

- request no longer accepts authoritative `compatibility`;
- `expected_compatibility` / `expected_change_scope` are guardrails only;
- `classified_compatibility`, `diff_summary`, `required_actions`, `can_activate_now` are server output;
- binding derive accepts controlled delta, not arbitrary JSON patch;
- profile revalidate updates validation evidence for revision drift.

- [ ] **Step 2: Update shared agent guide only with short stable rule**

Add no more than three bullets under semantic authoring guidance:

```markdown
- For semantic object maintenance, create new refs only for new stable identities; same-identity changes use revision endpoints.
- Agents must not declare semantic revision compatibility as fact; submit optional guardrails and consume server `classified_compatibility` plus `required_actions`.
- For binding/profile drift, prefer server-guided derive/revalidate flows over copying full grounding JSON.
```

- [ ] **Step 3: 运行文档检查**

Run:

```bash
git diff --check
```

Expected: no whitespace errors.

## Task 9: Full Verification

**Files:**
- All touched files

- [ ] **Step 1: Run targeted semantic tests**

Run:

```bash
.venv/bin/pytest tests/test_metric_revision_classification.py tests/test_semantic_revision_dependency_plan.py tests/test_semantic.py tests/test_semantic_typed_api.py tests/test_step_metadata.py -q
```

Expected: all pass.

- [ ] **Step 2: Run repository checks**

Run:

```bash
make lint
make typecheck
make test
```

Expected: all pass.

- [ ] **Step 3: Inspect public contract residue**

Run:

```bash
rg -n '"compatibility": "compatible"|Caller-declared compatibility|Declared revision compatibility|payload.compatibility' app docs tests
```

Expected: no authoritative request-side `compatibility` remains. Allowed hits must explicitly say deprecated/internal/server-classified compatibility.

## 验收标准

1. `POST /semantic/metrics/{ref}/revisions` 不再要求 agent 提交 `compatibility`。
2. metric revision create / validate 都返回 server `classified_compatibility`、canonical `diff_summary`、`required_actions`、`can_activate_now`。
3. guardrail mismatch 返回结构化 409，且不持久化 draft revision。
4. breaking metric revision 在 blocking required actions 未满足前不能 activate。
5. binding derive route 支持受控 delta，不要求 agent 复制完整 grounding JSON。
6. compiler profile revision drift 可以通过 revalidate action 满足。
7. artifact / step semantic snapshot 冻结 `ref + object_id + revision`。
8. docs 和 agent guide 不再要求 agent 判断 compatible / breaking。
9. `make lint`、`make typecheck`、`make test` 通过，或记录明确阻塞原因和失败范围。
