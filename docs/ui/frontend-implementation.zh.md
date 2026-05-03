# Marivo UI 前端实现说明

状态：v1 implementation note。

本文记录 `frontend/` 独立前端应用的实现边界、目录结构、数据访问规则、状态表达和后续扩展约束。

## 实现边界

- 前端是独立 React + TypeScript + Vite 应用，不挂回 FastAPI 内置 `/ui` 或 `/admin`。
- 前端只访问 Marivo HTTP API，不假设 MCP 层存在。
- `marivo.yaml` 仍然只作为 runtime 配置入口；source、engine、mapping inventory 通过 HTTP API 管理。
- v1 角色只用于导航分组、默认入口和信息优先级，不表达真实认证 / RBAC。
- Jobs 与 runtime status 是只读排障面；UI 不提供 submit / cancel / retry 操作。
- 不提供自由 raw SQL workbench。SQL 如由后端作为 provenance 返回，只能折叠为审计细节。

## 目录结构

- `frontend/src/api/`：API base URL、统一 HTTP client、错误归一化、TanStack Query hooks、OpenAPI 类型生成占位。
- `frontend/src/components/`：readiness / failure、runtime、evidence closure、empty state、diagnostic drawer 等共享组件。
- `frontend/src/fixtures/`：mock API 数据，覆盖 ready / not_ready source、mapping blocker、semantic stale、session gaps、proposition evidence、runtime failure、empty jobs 等闭环。
- Operations / Sources 使用 datasource live browse 查看 schema、table、column 与 preview 信息；前端不再展示本地同步对象缓存或 sync 操作入口。
- `frontend/src/pages/`：Overview、Operations、Semantic Layer、Analysis、API Contract 页面。
- `frontend/tests/e2e/`：Playwright 关键流程。

## API Client 规则

- 页面禁止直接调用 `fetch` 或拼接后端 URL。
- 所有网络访问必须经过 `apiClient` 和 `src/api/hooks.ts`。
- OpenAPI 类型通过以下命令生成：

```bash
MARIVO_OPENAPI_URL=http://localhost:8000/openapi.json npm run openapi:types
```

- 当前仓库提交的是最小 `openapi.generated.ts` 占位文件，避免没有后端服务时构建失败。
- 缺失字段、过滤、详情或 action 记录到 API Contract 页面和 backlog，不在前端伪造服务端状态。

## Query Key 规范

- 全局服务面：`health`、`metrics`、`openapi/index`。
- 管理员面：`sources`、`engines`、`mappings`、`jobs(filters)`、`policies`、`quality-rules`。
- 语义面：`semanticList(kind)`。
- 分析面：`sessions(filters)`、`sessionState(sessionId)`、`sessionRuntime(sessionId)`、`propositionContext(sessionId, propositionId)`、`propositionRuntime(sessionId, propositionId)`、`approvals(filters)`。

## 状态表达

- `StatusBadge` 统一展示 ready / not_ready / stale / blocked / pending / failed 等状态。
- `BlockerPanel` 统一展示 `failure_code`、`blocking_requirements`、`readiness_blockers`。
- `RuntimeStatusWidget` 明确 runtime status 只解释运行过程，不替代 evidence conclusion。
- `EvidenceClosure` 围绕 proposition context 展示 seed entries、support / oppose findings、assessment、gaps、inference records。
- `TaskEmpty` 为 source、engine、mapping、semantic、session、evidence、jobs 提供差异化空态，不诱导用户写 raw SQL 或编辑 `marivo.yaml`。
- `DiagnosticDrawer` 提供原始 API 摘要和 copy JSON 能力，但主页面仍展示任务摘要。

## 页面 Ownership

- Overview：管理员系统可用性首页。
- Operations：Sources、Engines、Mappings、Routing Debugger、Governance、Jobs / Runtime。
- Semantic Layer：Semantic Inventory、Readiness Queue、dataset-native grounding 摘要、对象详情与 lifecycle actions。
- Analysis：Session Inbox、Session Detail、Proposition Detail、Evidence Timeline、Evidence Inspector、Gap View、Approvals。
- API Contract：OpenAPI index、前端 API 依赖缺口、v1 交付边界。

## API 依赖缺口

| 区域 | 缺口 | 类型 |
| --- | --- | --- |
| Operations | source / engine / mapping list 需要稳定返回 `readiness_status` 与 `failure_code` | blocker |
| Semantic Layer | semantic list 最好统一返回 `dependency_refs`、`dependent_refs`、`capabilities`、`blocking_requirements` | enhancement |
| Analysis | session list 需要更完整的 `session_user` 和时间范围过滤 | enhancement |
| Evidence Inspector | artifact 身份、step lineage、finding extraction detail 需要更明确的只读 API surface | enhancement |

## 验证

```bash
cd frontend
npm run typecheck
npm run lint
npm run test
npm run build
npm run test:browser
```

Playwright 覆盖桌面和窄屏视口下的管理员 mapping blocker / routing 排障，以及分析人员 proposition context 阅读流程。
