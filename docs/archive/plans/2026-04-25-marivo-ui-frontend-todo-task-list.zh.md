# Marivo UI 前端实施 Todo Task List

## 概述

本文将 [`docs/ui/frontend-design.zh.md`](/Users/lichengxiang/source/oss/marivo/docs/ui/frontend-design.zh.md) 拆解为 Marivo UI v1 的可实施开发任务清单，目标是在 **保持 Marivo agent-first 定位、坚持 HTTP-only 边界、按角色任务闭环组织页面** 的前提下，建设一个独立前端控制台。

Marivo UI 的目标不是把 Marivo 改造成 BI 产品，也不是给人提供自由 raw SQL workbench。它是 agent-first 分析运行时的人类控制台，帮助三类用户完成各自的工作闭环：

- Marivo 管理员：看清 source / engine / mapping / governance / runtime 是否可用，并能定位 routing、sync、job 与 readiness blocker。
- 业务专家 / 语义建模者：围绕 semantic lifecycle、readiness、blocking requirements、dependency refs 与 capabilities 修复语义对象。
- 分析人员：围绕 session state 与 proposition context 阅读证据闭包，理解证据、判断、缺口与运行失败原因。

一句话结论：

- v1 先做“**独立 React 前端工程 + OpenAPI 约束的 HTTP client + 三类角色任务闭环页面 + readiness / evidence / runtime 统一状态表达 + 前端测试与交付文档**”。
- 不恢复旧 FastAPI 内置 `/ui` 或 `/admin`，不引入 MCP 假设，不把 `marivo.yaml` 作为 source / engine / mapping 管理入口，不设计自由 raw SQL 主入口。
- 当前 API 未提供真实认证 / RBAC，因此 v1 的角色只用于导航结构、信息优先级和操作边界，不作为安全边界。

## 文档依据

- [`docs/ui/frontend-design.zh.md`](/Users/lichengxiang/source/oss/marivo/docs/ui/frontend-design.zh.md)
- [`docs/api/README.md`](/Users/lichengxiang/source/oss/marivo/docs/api/README.md)
- [`docs/api/sources.md`](/Users/lichengxiang/source/oss/marivo/docs/api/sources.md)
- [`docs/api/engines.md`](/Users/lichengxiang/source/oss/marivo/docs/api/engines.md)
- [`docs/api/mappings.md`](/Users/lichengxiang/source/oss/marivo/docs/api/mappings.md)
- [`docs/api/semantic.md`](/Users/lichengxiang/source/oss/marivo/docs/api/semantic.md)
- [`docs/api/session-state.md`](/Users/lichengxiang/source/oss/marivo/docs/api/session-state.md)
- [`docs/api/context-surface.md`](/Users/lichengxiang/source/oss/marivo/docs/api/context-surface.md)
- [`docs/api/runtime-status.md`](/Users/lichengxiang/source/oss/marivo/docs/api/runtime-status.md)
- [`docs/api/jobs.md`](/Users/lichengxiang/source/oss/marivo/docs/api/jobs.md)
- [`docs/api/governance.md`](/Users/lichengxiang/source/oss/marivo/docs/api/governance.md)
- [`docs/agent-guide.md`](/Users/lichengxiang/source/oss/marivo/docs/agent-guide.md)

## 当前基线对照

当前仓库基线的关键事实：

- 已有 UI 产品设计文档定义三类用户、页面结构、v1 范围和前端技术基线。
- Marivo 当前核心边界是 HTTP-only；UI 不应假设 MCP 层存在，也不应依赖 MCP-only 信息。
- source、engine、mapping 的管理入口是 HTTP API，不是 `marivo.yaml`。`marivo.yaml` 只作为 runtime 配置入口。
- 当前 API 未提供真实认证 / RBAC；UI 的角色分组只能作为导航和操作体验分区，不能表达安全隔离承诺。
- Jobs / Runtime 是只读排障面；Jobs 不应被设计成 submit / cancel / retry 控制面。
- 分析读面应以 session state 和 proposition context 为主，runtime status 只解释运行进度、等待发布和失败原因。
- 仓库当前没有要恢复的内置 `/ui` 或 `/admin` 目标；后续实现应按独立前端应用规划。

因此，下一阶段重点不是“平铺所有 API 资源”，而是把 UI 设计文档中的任务闭环拆成可交付的前端工程、数据访问、页面、状态表达、测试与文档任务。

## 实施范围

### 本次必须覆盖

- 建立独立前端应用的 v1 工程边界与技术选型。
- 用 React + TypeScript + Vite + TanStack Query + Ant Design 作为前端技术基线。
- 通过 Marivo HTTP API 访问后端，API client 受 OpenAPI 契约约束。
- 建立 Overview / Operations / Semantic Layer / Analysis / API Contract 的导航结构。
- 完成管理员、业务专家、分析人员三类角色的 v1 页面任务拆解。
- 建立统一的 readiness、failure、runtime、gap、evidence 状态展示规范。
- 建立前端单元测试、集成测试、交互测试和人工验收方案。
- 同步前端 README、运行说明、设计边界与后续任务记录。

### 本次明确不做

- 真实认证 / RBAC。
- 自由 raw SQL workbench 或以 SQL 作为外部主契约的查询页面。
- 类 BI 的报表搭建、拖拽仪表盘或自由可视化探索。
- 复杂拖拽式建模画布。
- 任意 DAG 手工编排。
- 恢复旧 FastAPI 内置 `/ui` 或 `/admin`。
- 在 UI 中直接编辑 `marivo.yaml` 的 source / engine / mapping inventory。
- Jobs submit / cancel / retry 控制面。
- MCP 依赖、MCP-only 页面或 MCP 与 HTTP 并行管理通道。

## 交付原则

- 粒度以“单个 owner 可独立完成并验收”为准，避免泛泛的“实现 UI”。
- 页面按用户任务闭环组织，不按 API 资源机械平铺。
- 管理员页面优先展示 `readiness_status`、`failure_code`、routing blocker、sync / job 状态和可修复路径。
- 业务专家页面优先展示 semantic lifecycle / readiness、blocking requirements、dependency refs 和 capabilities。
- 分析人员页面优先展示 session state、proposition context、evidence closure 和 gaps；runtime status 只作为诊断侧栏。
- 每个页面任务都必须有明确交付物和验收标准，避免“页面有了但不知道是否完成闭环”。
- 前端不新增后端能力假设；如果 API 当前不能支持某个交互，UI v1 只能做只读展示、禁用态或后续依赖记录。

## 建议实施顺序

1. T1 冻结 Scope / Contract 与角色边界
2. T2 建立前端工程基线
3. T3 建立 API Client 与数据访问层
4. T4 完成全局布局、导航和状态表达
5. T5 交付管理员 Operations 页面
6. T6 交付业务专家 Semantic Layer 页面
7. T7 交付分析人员 Analysis 页面
8. T8 补齐错误、空状态、加载态与诊断体验
9. T9 补齐前端测试与验证矩阵
10. T10 更新文档、交付说明与后续边界

说明：

- T1 到 T4 是所有页面的共同前置，尤其是 API client、状态标签和导航边界。
- T5、T6、T7 可以分 owner 并行推进，但必须复用同一套 data access、status rendering 和 layout 规范。
- T8 和 T9 不应留到最后一次性补洞；每个页面完成时都要增量接入错误态、空态和测试。

## Todo Task List

## 一、Scope / Contract 冻结

- [x] 任务 1.1：冻结 UI v1 产品边界
  - 交付物：frontend scope note / decision record
  - 关键内容：UI 是 agent-first 系统的人类控制台；不是 BI、不是 raw SQL workbench、不是安全边界、不是旧内置 `/ui` 或 `/admin`
  - 验收标准：后续任务不会把 UI 扩写成报表平台、SQL 工作台或后端内置静态页面

- [x] 任务 1.2：冻结 HTTP-only 接入边界
  - 交付物：frontend access contract
  - 关键内容：前端只通过 Marivo HTTP API 访问后端；不假设 MCP；不引入 MCP-only 页面或管理通道
  - 验收标准：代码、文档和页面文案中不会把 MCP 描述成 UI 依赖

- [x] 任务 1.3：冻结角色导航边界
  - 交付物：role navigation matrix
  - 关键内容：管理员、业务专家、分析人员只影响导航分组、默认页面和操作显隐；当前不表达真实认证 / RBAC
  - 验收标准：UI 不向用户承诺“按角色授权”或“安全隔离”；角色切换只是 v1 信息架构能力

- [x] 任务 1.4：冻结暂缓范围
  - 交付物：v1 non-goals note
  - 关键内容：真实认证 / RBAC、raw SQL 主入口、BI 报表、拖拽建模画布、任意 DAG、Jobs submit / cancel / retry、`marivo.yaml` inventory 编辑、旧内置 UI、MCP 依赖全部暂缓
  - 验收标准：产品设计、任务拆解和验收用例都不要求这些能力

- [x] 任务 1.5：冻结 UI 与 API 契约关系
  - 交付物：API contract dependency note
  - 关键内容：UI 只消费当前 docs/api 与 OpenAPI 暴露的能力；缺失能力记录为后续 API 依赖，不在前端伪造状态或提交路径
  - 验收标准：前端不会模拟后端持久化、伪造 readiness 结果或绕过 typed intent / evidence surface

## 二、前端工程基线

- [x] 任务 2.1：创建独立前端应用骨架
  - 交付物：frontend app scaffold
  - 关键内容：React、TypeScript、Vite、基础目录结构、构建脚本、开发脚本、环境变量模板
  - 验收标准：前端可独立启动和构建，不挂回 FastAPI 内置 `/ui` 或 `/admin`

- [x] 任务 2.2：接入 Ant Design 基础主题
  - 交付物：theme baseline
  - 关键内容：管理台布局、紧凑表格、表单、标签、抽屉、步骤、空态、错误态和状态色规范
  - 验收标准：页面视觉适合密集控制台，不出现营销站点式 hero、装饰卡片或 BI 大屏风格

- [x] 任务 2.3：接入 TanStack Query
  - 交付物：query client setup
  - 关键内容：全局 QueryClient、request retry 策略、loading/error/refetch 模式、query key 命名规则
  - 验收标准：页面数据读取不散落手写 fetch 状态，失败和刷新行为一致

- [x] 任务 2.4：定义前端类型与模块边界
  - 交付物：frontend architecture note
  - 关键内容：api、features、routes、components、status、testing、fixtures 分层；业务页面禁止直接拼接后端 URL
  - 验收标准：新增页面能复用同一套 API client、状态组件和 layout，不形成每页一套实现

- [x] 任务 2.5：建立本地开发配置
  - 交付物：frontend env and proxy note
  - 关键内容：Marivo API base URL、Vite dev proxy、mock 开关、错误显示等级
  - 验收标准：开发者能连接本地或远程 Marivo HTTP API，不需要改源码切换环境

## 三、API Client 与数据访问层

- [x] 任务 3.1：建立 OpenAPI 约束的 API client 生成流程
  - 交付物：typed API client pipeline
  - 关键内容：从 OpenAPI 生成或校验 TypeScript 类型；记录生成命令；避免手写漂移
  - 验收标准：source / engine / mapping / semantic / session / runtime / jobs / governance 的类型来自同一契约来源

- [x] 任务 3.2：封装基础 HTTP client
  - 交付物：request wrapper
  - 关键内容：base URL、JSON parse、错误归一化、request id / trace id 透传、超时展示
  - 验收标准：页面能稳定区分网络错误、HTTP 错误和业务错误，不把所有错误显示成未知失败

- [x] 任务 3.3：建立 Operations data hooks
  - 交付物：sources / engines / mappings / routing / governance / jobs hooks
  - 关键内容：列表、详情、创建 / 编辑 / validate、sync job、routing debug、Jobs 只读查询
  - 验收标准：管理员页面不直接调用 fetch，且 Jobs hook 不暴露 submit / cancel / retry

- [x] 任务 3.4：建立 Semantic Layer data hooks
  - 交付物：semantic inventory / readiness / object detail hooks
  - 关键内容：semantic object list、detail、validate、activate、deprecate、dependency refs、capabilities、source object browser
  - 验收标准：业务专家页面能围绕 lifecycle / readiness 闭环取数，不按 API JSON 平铺

- [x] 任务 3.5：建立 Analysis data hooks
  - 交付物：session / state / proposition context / evidence / gaps / approvals hooks
  - 关键内容：Session Inbox、Session Detail、session state、proposition context、runtime status、Evidence Timeline、Approvals
  - 验收标准：分析人员页面以 session state 和 proposition context 为主读面，runtime status 只作为诊断数据源

## 四、全局布局、导航、状态表达

- [x] 任务 4.1：实现全局应用框架
  - 交付物：app shell
  - 关键内容：左侧导航、顶部服务状态、内容区域、详情抽屉 / modal 容器、全局错误边界
  - 验收标准：Overview / Operations / Semantic Layer / Analysis / API Contract 分组稳定可用

- [x] 任务 4.2：实现角色视图切换
  - 交付物：role-aware navigation view
  - 关键内容：按管理员、业务专家、分析人员调整默认入口和导航强调；明确不是 RBAC
  - 验收标准：角色切换不会隐藏安全敏感能力承诺，也不会阻止用户通过 URL 访问页面

- [x] 任务 4.3：实现 readiness / failure 状态组件
  - 交付物：status badge and blocker panel
  - 关键内容：`readiness_status`、`failure_code`、blocking requirements、capability gap、dependency blocker 的统一展示
  - 验收标准：source / engine / mapping / semantic object 页面显示同一套状态语义

- [x] 任务 4.4：实现 runtime / job 状态组件
  - 交付物：runtime and job status widgets
  - 关键内容：session、artifact、proposition runtime status；job status；只读排障解释
  - 验收标准：组件文案明确 runtime / Jobs 解释运行过程，不替代证据结论

- [x] 任务 4.5：实现 evidence 状态组件
  - 交付物：evidence closure widgets
  - 关键内容：seed entries、relevant findings、support / oppose、assessment、gaps、artifact refs、inference records
  - 验收标准：Proposition Detail 能把 proposition context 展示为一个可读证据闭包

## 五、管理员 Operations 页面

- [x] 任务 5.1：实现运行总览
  - 交付物：Overview page
  - 关键内容：health、metrics、source / engine / mapping ready 分布、最近失败 jobs、sync jobs、routing failures、主要 blocker
  - 验收标准：管理员进入首页能优先看到不可用对象、`readiness_status`、`failure_code` 和修复入口

- [x] 任务 5.2：实现 Sources 列表与详情
  - 交付物：Sources pages
  - 关键内容：source 列表、详情、authority 摘要、sync selections、Catalog Browser、Synced Objects、related mappings
  - 验收标准：source not_ready 时详情页顶部显示 blocker；sync selections 不要求用户手写 JSON

- [x] 任务 5.3：实现 Engines 列表与详情
  - 交付物：Engines pages
  - 关键内容：engine 列表、详情、connection 摘要、auth、capabilities、policy、related mappings
  - 验收标准：页面明确 engine readiness 是配置校验，不暗示已执行在线探测

- [x] 任务 5.4：实现 Mappings 列表与详情
  - 交付物：Mappings pages
  - 关键内容：mapping 列表、catalog mappings 表格编辑、source / engine 摘要、readiness 检查、routing impact
  - 验收标准：mapping coverage 缺失以差异视图展示，`mapping_incomplete` 等失败码有可操作解释

- [x] 任务 5.5：实现 Routing Debugger
  - 交付物：Routing Debugger page
  - 关键内容：table names 输入、routing intent 摘要、resolved engine、qualified names、selection reason、failure_code、candidates、readiness blockers
  - 验收标准：成功时解释为什么选中 engine；失败时优先展示 routing blocker

- [x] 任务 5.6：实现 Governance 页面
  - 交付物：Policies / Quality Rules pages
  - 关键内容：policies 分组、quality rules 按 table name 分组、governance check 调试抽屉
  - 验收标准：Governance 是约束与排障页面，不被展示成分析结论页

- [x] 任务 5.7：实现 Jobs / Runtime 只读页面
  - 交付物：Jobs and Runtime pages
  - 关键内容：按 session_id / status 过滤 jobs，展示 job_id、job_type、status、时间、error_message；runtime status 只读详情
  - 验收标准：页面不出现 submit、cancel、retry 操作；用户能从 session / artifact / proposition 跳入对应 runtime status

## 六、业务专家 Semantic Layer 页面

- [x] 任务 6.1：实现 Semantic Object Inventory
  - 交付物：Semantic inventory pages
  - 关键内容：Entities、Metrics、Processes、Dimensions、Time、Enum Sets、Predicates、Bindings、Compiler Profiles 分 tab 展示
  - 验收标准：默认突出 active + not_ready / stale 对象，列表展示 lifecycle、readiness、blocker count、capabilities、dependency count

- [x] 任务 6.2：实现 Readiness Queue
  - 交付物：Readiness Queue page
  - 关键内容：not_ready / stale 队列、blocking requirements 聚合、dependency refs、dependent refs、capability 缺口、修复入口
  - 验收标准：页面回答“为什么 not_ready”和“下一步修什么”，不只显示状态标签

- [x] 任务 6.3：实现 Semantic Object Detail
  - 交付物：Object detail page
  - 关键内容：Header / Identity、Typed Contract、Dependencies、Dependents、Readiness、Lifecycle Actions
  - 验收标准：draft 可编辑，active 提示 public contract frozen；activate 后仍展示 `readiness_status`

- [x] 任务 6.4：实现 Validate / Activate / Deprecate 操作
  - 交付物：lifecycle action flows
  - 关键内容：validate check-only、activate 状态切换、deprecate 操作确认、错误和 blocker 展示
  - 验收标准：validate 不被误导为持久状态切换；activate 不被误导为 ready

- [x] 任务 6.5：实现 Source Object Browser
  - 交付物：Source Object Browser page
  - 关键内容：synced source objects、authority locator、schema / table / column 摘要、已有 bindings、mapping readiness 摘要
  - 验收标准：业务专家能从 source object 反向理解 semantic refs 和 binding 建模入口

- [x] 任务 6.6：实现 Binding Wizard v1
  - 交付物：Binding Wizard
  - 关键内容：carrier source object、metric inputs、key refs、time bindings、dimension exports、contract preview
  - 验收标准：向导按依赖顺序组织，完成后进入对象详情执行 validate / activate

## 七、分析人员 Analysis 页面

- [x] 任务 7.1：实现 Session Inbox
  - 交付物：Session Inbox page
  - 关键内容：session_id、goal question、lifecycle、execution_identity、policy / budget、active proposition count、blocking gap count、runtime overall status
  - 验收标准：分析人员能按 status、session_id、session_user、时间范围找到自己的分析任务

- [x] 任务 7.2：实现 Session Detail
  - 交付物：Session Detail page
  - 关键内容：Session Root、Session State、Runtime Summary、Evidence Timeline、Related Jobs、Related Approvals
  - 验收标准：session state 是主读面；runtime status 只作为右侧诊断信息

- [x] 任务 7.3：实现 Proposition Detail
  - 交付物：Proposition Detail page
  - 关键内容：proposition context、seed entries、relevant findings、latest assessment、blocking gaps、non-blocking gaps、inference records、artifact refs、runtime status
  - 验收标准：页面围绕 proposition context 展示 evidence closure，不要求用户跨页面拼证据链

- [x] 任务 7.4：实现 Evidence Timeline
  - 交付物：Evidence Timeline component / page
  - 关键内容：session 创建、typed intent、artifact materialized、finding extracted、proposition seeded、assessment committed、approval events
  - 验收标准：默认按分析链路聚合，不展示成逐条运行日志；SQL 只作为折叠 provenance 细节

- [x] 任务 7.5：实现 Evidence Inspector
  - 交付物：Evidence Inspector page / drawer
  - 关键内容：artifact identity、step lineage、schema version、source lineage、projection metadata、runtime status、finding extraction result
  - 验收标准：artifact 与 finding 明确区分，provenance 不被展示成结论

- [x] 任务 7.6：实现 Gap View
  - 交付物：Gap View page
  - 关键内容：gap id、gap type、severity、blocking、related proposition、requirement summary、satisfiable by、status
  - 验收标准：blocking gaps 优先排序，gap 文案回答缺什么证据或前置条件

- [x] 任务 7.7：实现 Approvals 页面
  - 交付物：Approvals page
  - 关键内容：request id、session id、recommendation id、status、reviewer、reason、risk、关联证据和 session context
  - 验收标准：approve / reject 前能看到 recommendation、risk 和证据上下文；不把审批列表藏在 session 详情里

## 八、错误、空状态、加载态与诊断体验

- [x] 任务 8.1：定义全局错误分类
  - 交付物：frontend error taxonomy
  - 关键内容：网络错误、HTTP 错误、validation error、not_ready、runtime blocked、permission-like placeholder、unknown error
  - 验收标准：页面错误文案能指向可行动作，不把所有错误都显示为“请求失败”

- [x] 任务 8.2：实现空状态规范
  - 交付物：empty state components
  - 关键内容：无 source、无 engine、无 mapping、无 semantic object、无 session、无 evidence、无 jobs 的差异化空态
  - 验收标准：空态说明下一步动作或依赖页面，不诱导用户写 raw SQL 或编辑 `marivo.yaml`

- [x] 任务 8.3：实现加载与刷新规范
  - 交付物：loading / refetch policy
  - 关键内容：表格 skeleton、详情页局部 loading、手动刷新、后台 refetch、长任务状态轮询
  - 验收标准：sync、runtime、Jobs 等长任务页面能解释“正在等待什么”

- [x] 任务 8.4：实现诊断抽屉
  - 交付物：diagnostic drawer
  - 关键内容：原始 API 摘要、request id、failure_code、blocker detail、runtime detail、copy debug payload
  - 验收标准：高级用户可排障，但默认主页面仍展示面向任务的摘要

- [x] 任务 8.5：实现安全边界提示
  - 交付物：security boundary notices
  - 关键内容：当前无真实 RBAC、角色只是导航分区、敏感字段折叠展示、认证能力后续接入
  - 验收标准：UI 不误导用户认为 v1 已经提供服务端权限隔离

## 九、前端测试与验证

- [x] 任务 9.1：建立前端单元测试基线
  - 交付物：unit test setup
  - 关键内容：状态组件、错误归一化、API hook wrapper、readiness/failure renderer、evidence grouping
  - 验收标准：核心状态表达有可重复测试，不依赖真实 Marivo 服务

- [x] 任务 9.2：建立 API mock / fixture 契约
  - 交付物：frontend fixtures
  - 关键内容：ready / not_ready source、mapping blocker、semantic stale、session with gaps、proposition with evidence、runtime failure、empty jobs
  - 验收标准：每个 fixture 都对应一个页面任务闭环或回归风险

- [x] 任务 9.3：建立页面集成测试
  - 交付物：integration tests
  - 关键内容：Operations、Semantic Layer、Analysis 三组主页面的加载、过滤、跳转、错误态和空态
  - 验收标准：三类角色的默认入口都能在 mock API 下稳定渲染

- [x] 任务 9.4：建立浏览器交互测试
  - 交付物：Playwright scenarios
  - 关键内容：管理员排查 mapping blocker、业务专家修复 readiness queue、分析人员阅读 proposition context
  - 验收标准：关键流程在桌面视口和窄屏视口下无明显遮挡、错位或不可点击控件

- [x] 任务 9.5：建立人工验收清单
  - 交付物：manual QA checklist
  - 关键内容：角色入口、任务闭环、readiness/failure 可读性、evidence closure、runtime 与 evidence 边界、Jobs 只读边界
  - 验收标准：产品验收不只检查页面存在，还检查用户是否能完成对应工作流

## 十、文档、交付与后续边界

- [x] 任务 10.1：补前端 README
  - 交付物：frontend README
  - 关键内容：安装、启动、构建、配置 API base URL、连接本地 Marivo、测试命令、常见问题
  - 验收标准：新开发者不读源码也能启动 UI 并连接 HTTP API

- [x] 任务 10.2：补 UI 实现说明
  - 交付物：UI implementation note
  - 关键内容：目录结构、API client 生成、query key 规范、状态组件、页面 ownership
  - 验收标准：后续页面不会绕过统一 data access 和状态表达

- [x] 任务 10.3：同步 API 依赖缺口
  - 交付物：API dependency backlog
  - 关键内容：记录 UI 需要但当前 API 未满足的字段、过滤、分页、详情或 action，区分 blocker 和 enhancement
  - 验收标准：前端不会通过伪造数据补 API 缺口，后端需求有清晰来源

- [x] 任务 10.4：补交付边界说明
  - 交付物：release boundary note
  - 关键内容：v1 无真实 RBAC、无 raw SQL workbench、无 Jobs 控制面、无 MCP 依赖、无内置 UI 恢复
  - 验收标准：README、设计文档和任务清单对 v1 暂缓项表述一致

- [x] 任务 10.5：补后续版本候选清单
  - 交付物：post-v1 backlog
  - 关键内容：真实认证 / RBAC、更多建模向导、可视化增强、审批流增强、API Contract 浏览器增强、可观测性增强
  - 验收标准：后续能力不污染 v1 实施任务，也不会被误认为当前必须完成

## 验证方案

### 文档落地验证

```sh
test -f plan/2026-04-25-marivo-ui-frontend-todo-task-list.zh.md
rg -n "HTTP-only|MCP|raw SQL|marivo.yaml|RBAC|Jobs|readiness_status|failure_code|proposition context|TanStack Query|Ant Design" plan/2026-04-25-marivo-ui-frontend-todo-task-list.zh.md
rg -n "^## 概述|^## 文档依据|^## 当前基线对照|^## 实施范围|^## 交付原则|^## 建议实施顺序|^## Todo Task List|^## 验证方案|^## 验收标准" plan/2026-04-25-marivo-ui-frontend-todo-task-list.zh.md
```

### 前端工程验证

- `npm run dev` 或等价脚本能启动独立前端应用。
- `npm run build` 能完成 production build。
- `npm run typecheck` 能完成 TypeScript 类型检查。
- `npm run lint` 能完成前端 lint。
- OpenAPI client 生成或校验命令可重复执行，且不会产生未解释的漂移。

### 页面功能验证

- 管理员流程：从 Overview 发现 not_ready mapping，进入 Mappings 详情，查看 `failure_code` 和 routing blocker，再进入 Routing Debugger 验证 table names。
- 业务专家流程：从 Readiness Queue 找到 stale / not_ready semantic object，查看 blocking requirements、dependency refs 和 capabilities，执行 validate / activate 后仍能看到 readiness 结果。
- 分析人员流程：从 Session Inbox 进入 Session Detail，查看 session state，再进入 Proposition Detail 阅读 proposition context、evidence closure、gaps 和 runtime status。
- Jobs 验证：Jobs 页面只读展示，不出现 submit / cancel / retry。
- raw SQL 验证：UI 不提供自由 SQL 主入口；SQL 如果作为 provenance 出现，必须折叠为审计细节。

### 视觉与交互验证

- 桌面和窄屏视口下导航、表格、详情抽屉、状态标签、按钮文案不遮挡、不溢出。
- 页面默认信息密度适合管理台，不出现营销页 hero 或 BI 大屏布局。
- loading、empty、error、not_ready、runtime blocked 状态都有可读文案和下一步入口。

### 本次实现验证

本次已落地独立 `frontend/` 前端工程，因此验证以 UI 工程命令为主：

```sh
cd frontend
npm run typecheck
npm run lint
npm run test
npm run build
npm run test:browser
```

本次未修改 Python 运行时代码；除非后续改动触及 Python/API/schema 行为，否则不要求运行仓库级 `make test`。

## 验收标准

- `frontend/` 独立 React 应用存在，可独立启动、构建、测试。
- 前端保持 HTTP-only，不恢复内置 UI，不假设 MCP，不把 `marivo.yaml` 作为 inventory 管理入口。
- 页面覆盖管理员、业务专家、分析人员三类用户的 v1 工作闭环。
- Operations / Semantic Layer / Analysis 页面复用同一套 API client、hooks、状态组件和诊断组件。
- Readiness、failure、runtime、gap、evidence 状态有统一组件表达。
- Jobs / Runtime 页面只读展示，不出现 submit / cancel / retry 控制。
- API Contract 页面记录 OpenAPI 类型生成方式和 API 依赖缺口。
- `frontend/README.md` 与 `docs/ui/frontend-implementation.zh.md` 说明运行、配置、验证和 v1 边界。
