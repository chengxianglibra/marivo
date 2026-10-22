# 符号 authoring 实施计划与证据账本

日期：2026-10-22。状态：直接 `ms.ref.*` 符号方案已完成本地实施和验收，两轮实现审查无剩余 P2+。
设计修订核查基线：`d288fcdd1ab2010c22334e22d83d6d7edaa745e1`；当前实施基线及历史审查基线分别保留。
设计：[symbolic-authoring-proposal.md](symbolic-authoring-proposal.md)，D01–D15。

2026-10-22 用户已要求 agent 团队完整实施，并另行明确批准 AGENTS.md 与两个 packaged skills 的相关条款对齐。
实施基线为 `202b92421571361e4c41edeb4a7ccff7dec09d53`；实际分工、审查、验收和命令见
[实施进度与证据](symbolic-authoring-progress.md)。以下设计阶段的历史证据不代替当前实现验收。
实施验收后用户另行授权仅提交本次改动的本地 commit，不推送；本次未运行发布验收。

## 1. 范围与实施原则

目标是一条 authoring 路径：symbolic Deferred → loader binding → closed Catalog。
保留现有闭合计算图、解析身份合同、受限 Ibis 边界及 Analysis 执行语义。
不创建第二个 compiler、通用 deferred callback framework 或跨 load 全局缓存。

开始实施时重新检查 HEAD、git status、现行 owning contracts 与本设计的差异。
当前时间过滤优化涉及 compiler/materialization/datasource 及测试，是既有工作，
必须按实时 diff 协调，不能覆盖或恢复。
不为代码与设计不一致自动选择历史实现；在此账本记录具体决策变化。

## 2. 阶段、所有权与依赖

Owner 是责任区域。用户已明确要求 agent 团队实施；本次由三个模块 agent 并行负责
core、Population 和公开边界，负责人串行整合共享文件并运行最终门禁，实际分工见实施进度。

| 阶段 | Owner / 责任文件 | 交付物 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| S0 | 负责人；本设计与计划 | 审查 D01–D15、十种 ref 工厂、公开身份边界、辅助值闭合范围；记录基线 | 无 | 已完成；实施基线和授权已记录 |
| S1 | recipe / identity；`refs.py`、拟新增私有 `_symbolic.py`、共享 definition 层、`semantic/_expression_binding.py` | 内部键工厂保留、公开 ref facade、typed symbol、捕获/闭合记录、共享语法白名单 | S0 | 已实施；最终证据见实施进度 |
| S1b | 公开身份边界；`catalog.py`、`readiness.py`、`source_health.py`、`preview_scope.py`、datasource authoring/manage/inspection、`expressions.py`、设计 §2.1 的 Analysis adapters | native selector 输入提取，`.ref/.refs` 输出，Ref-keyed Python 映射迁移，内部比较/codec 隔离 | S1 | 已实施；最终证据见实施进度 |
| S2 | authoring；`_authoring_context.py`、`_authoring_decorators.py`、`_authoring_declarations.py`、`_authoring_temporal.py`、`_dsl_authoring.py`、`expressions.py` | 构造器与 ms.ref.* 返回同角色符号；身份参数提取；unit/additivity 延后绑定 | S1、S1b 共享提取合同 | 已实施；最终证据见实施进度 |
| S3 | 无 Session 参数；`analysis/dsl/population.py`、`_grouping.py`、拟新增 `_definition/population_builders.py` | 必须下沉共享纯 builder；Grouping/Population 受控参数 recipe，闭合 helper 保持原结果 | S1、S2 参数协议 | 已实施；最终证据见实施进度 |
| S4 | loader；`loader.py`、`validator.py`、`_compiled_state.py`、`_definition_identity.py`、`_deferred_semantics.py` | 物理只读视图、Measure 身份、完整索引/DAG、绑定、错误位置、最终 freeze | S1–S3 | 已实施；最终证据见实施进度 |
| S5 | Catalog/消费；`catalog.py`、`expressions.py`、`analysis/session/core.py`、`analysis/dsl/frame_population.py` 与 `population.py` | `.get()` 闭合值与身份 selector 的区别、符号越界错误、已有 rebinding 合同与隔离 | S1b、S4 | 已实施；最终证据见实施进度 |
| S6 | surface；`semantic/__init__.py`、`authoring.py`、`_capabilities/registry.py`、相关 tests/docs/site | 删除旧公开入口、同步 Help/类型/例子，更新当前合同 | S2–S5 | 已实施；最终证据见实施进度 |
| S7 | 负责人；相关 fixture/test owners 与本账本 | 语义/数值回归、至多两轮正式审查、最终本地门禁及证据 | S6 | 已完成；组件结果、文档修复复验和用户要求停止重复门禁的记录见实施进度 |

S1–S5 是内部开发阶段，不是需要长期支持的双轨用户模式。最终切换时 S6 必须完成，
不能提交一个在 Help 中要求 finalize、而代码已删除该入口的可交付状态。

## 3. 每阶段的具体退出条件

### S1：符号表示与内部身份

- 内部 `Ref` 的比较/hash/序列化测试保持；公开 `ms.ref.*` 十个工厂直接返回 Deferred，
  不触发查找或 source I/O，不新增 `ms.symbol`；十种 leaf 返回值按 D14 使用 NewType。
- 不修改全库内部 `ref_factory` 为符号工厂。内部 refs 不 import Semantic/Analysis；
  datasource 的 symbol 暴露/提取通过依赖中立共享层，不形成循环导入。
- symbol leaf/factory 最小模块只依赖 refs 与 Ibis 基础；不能让 `_temporal` 反向导入
  已经依赖它的完整 `_definition/deferred.py` inspector。
- 原生 Deferred 的语法树保持；`==` 是表达式，作者不依赖 bool/hash/key 或容器成员身份判断。
- 在当前 mypy 配置验证十种名义类型：EntitySymbol 可传 entity 身份参数，DatasourceSymbol
  与普通复合 Deferred 不可；无运行时子类或 wrapper，无公开转换入口，无 Any/cast 兜底。
- 捕获依赖对象，不在 load 重新读取函数 globals；支持同一模板多次独立绑定。
- 所有声明/参数捕获入口保存前运行共享白名单，检查完整子树、arity、常量形状及可知角色；
  loader 复用后再查实际依赖。前向 missing 不在 capture 阶段误拒绝，不能制造占位 SemanticInput。
- `summ()`、`orders.amount`、旧身份属性节点在进入捕获点时失败，位置含消费声明和 recipe
  子路径；普通 Python 属性访问不要求立即失败，也不虚构模块级创建位置。C1 决策见 §7。
  未消费 recipe 和已格式化的普通字符串不做来源追踪；字符串按接收参数原有规则验证。

### S1b：公开输入/输出适配

- 追踪 `type(value) is Ref`、`Ref[...]` 签名、`.ref/.refs` 及公共身份字段的真实消费者，
  按设计 §3.4 适配；内部 registry/sets/dataclass equality 仍用私有身份 backing。
- 仅迁移语义 Ref/ExactRef 边界，不改 ArtifactRef 或物理对象定位字符串。纯身份参数
  使用设计 §3.3 的名义类型/精确 union；表达式和既有闭合字段 Deferred 仍由 consumer 校验。
- 按设计 §2.1 清单迁移公开 Entity/Relationship 读取；Entity 私有 ExactRef 与 Relationship
  私有 parameter 成为内部 consumer 的输入。解除公开 Relationship 对内部 parameter 的
  继承耦合；不得让 symbol 属性穿透到关系路径图、scope 渲染或 materialization。
- `_scope`、binding、observation、materialization 中的内部 ExactRef/RelationshipParameter
  读取保留，逐项确认图内没有公开 wrapper。`_capabilities/catalog_inputs.py` 是 disclosure
  对齐，不与 runtime 访问混计。
- Catalog describe/readiness/preview/source_health、source_check helpers、datasource
  test/inspect/raw_sql 保持各自 scope/kind/entry ownership 和 source I/O 边界。
- public metadata selectors 是未绑定查找输入；`.get()` 才交付闭合对象。禁止 metadata
  symbol 混入执行图，也禁止检查 `.key` 等 Deferred 动态属性来取得身份。
- PreviewScope 多 Entity 和 PreviewSourceBindings 改 pair sequence；按内部 Entity
  key 检查重复、缺失、多余及参数形状，单 Entity scope 保持直接 AuthoringScope；
  source_bindings 保持显式 Entity pair，理由见设计 §3.5，不新增单 Entity 隐式选择重载。
- 内部 RefPayloadV1、Pydantic 编解码、CLI 字符串协议及 wire 不变化；对 Python 公开
  返回值做边界转换，不把 Deferred 序列化。旧 Ref-keyed Python Mapping 不留兼容分支。
- 不引入通用 public identity wrapper、`same_ref`、`key_of` 或新 binding builder。

### S2：构造器与 Measure 断言

- 按设计返回矩阵更新 concrete typing、overloads、docstrings，避免 Any/cast 扩散。
- `_value_ref` / `_binding_field_ref` 支持 native symbols，继续拒绝派生路径冒充物理列。
- temporal/additivity/versioning/relationship 参数只捕获身份，完整范围验证所属关系。
- 将 `declare_measure` 的语义验证提取供闭合本地调用与 loader 共享；保留其所有规则。
- 删除符号构造对 `LoaderContext.finalized` 的依赖，物理 IR 捕获仍需要合法加载上下文。

### S3：辅助值

- 用现有 named Metric grouping 测试列出已支持组合，而不是只检查普通字段 `.per`。
- 明确条件返回签名和受控参数操作：identity、membership、coordinates、axes/index、
  project/union/intersection/difference、product、classify/image/preimage、
  where/count/in_population；保留完整关联 tuple、keys、顺序、unmatched、owner 及时间锚点。
- 按语义角色保持当前装饰器调用边界；Population helper 在模块级构造，尤其先调用
  Population.count 得到 Deferred 再由 body 返回，不因符号类型变化放宽 AST 白名单。
- 必须建立 `_definition/population_builders.py`，按 D13/设计 §5.2 提取 identity/membership、
  axes/keys/path/owner 校验、project/set/product/classify/image/preimage/where 程序构造，
  复用 axes/index/count/in_population 的现有定义/resolver。公开闭合调用和 loader 共用它。
- builder 只收闭合定义事实，返回现有 bindings/programs/resolvers；公开类型适配、
  `_frame_inputs`、Session/source 执行与 owning error 转换保留原 owner。
  无 `callable(*args)`、任意 Attr/Item 或 Semantic → Analysis 反向导入。
- 闭合参数与符号参数绑定后产生相同定义；无 Session owner 的 source program 仍可
  发布，Session-owned 参数仍不可发布。不能把加载不读 source 误解成执行不读 source。

### S4：loader 与指纹

- 物理完整验证之后、final freeze 之前建立私有只读视图；不先冻结待发布 registry。
- Measure root 提示先复用 refs.py 的三段 Measure path 校验；Domain 只能一段，不接受带点
  domain。建立完整名称表后必须验证目标存在和真实根一致，提示不能授予计算事实。
- 全量图包含直接对象和 symbol、physical sidecar、参数依赖、自环；一次循环检测，
  不靠反复发布直到成功，也不根据遍历顺序保留第一个候选。
- 绑定后的事实生成现有计算图；所有发布成功才调用最终 `build_compiled_state`。
- 指纹验证基于闭合事实，诊断路径/行号、对象地址、符号拼写不进入 computation identity。
- 本次新生成引用在绑定时验证版本；最终图不对捕获的旧闭合叶子强制匹配当前版本。
  registry 保存命名 Metric 的未自引用 body，避免声明版本参与自己的指纹计算。
- 自环/缺失/断言失败不返回部分 Catalog，不重新运行作者模块。

### S5–S6：消费与清理

- Catalog `.get()` 输出类型保持设计 D07；导航身份字段返回 symbol，普通分析对未绑定
  输入报错，无隐式查找当前 Session。公开 Ref 类型及旧 getter 协议清理完成。
- 匿名闭合物理表达式保持捕获事实；published Metric 继续按原消费者范围合同处理。
- 符号不进入准备、持久化或恢复路径；无需为 authoring-only 模板新增 Store 格式。
- 逐一定义/调用追踪后移除 FinalizedDeclarations 分支与无用状态；保留内部解析。
- Help/CLI/exports/typing/native examples/latest EN-ZH 同步；显式改写 `semantic/__init__.py`
  模块 docstring 的 finalize/resolve 首屏用法，不增加兼容跳转或 stub。
- 清查旧身份属性读取、f-string 与成员判断的真实 public 调用；保留内部 Ref 用法。
  Help 和迁移说明明确捕获边界报错合同，不承诺任意第三方 Python 属性访问立即失败。
  C1 的设计闭合不替代捕获校验、迁移及其实现验收。

## 4. 验收矩阵

优先改造现有测试与 fixture，复用 `tests/conftest.py`、`tests/shared_fixtures.py`。
实施测试工作前读取 `marivo-test-fixtures` skill。不复制一套 provider × DSL 全矩阵。

| 编号 | 可观察验收 | 首选测试区域 | 当前证据 |
| --- | --- | --- | --- |
| A01 | 构造器与 ms.ref.* 同 kind/path 符号；十种工厂 NewType/运行时 kind 校验；内部 Ref 仍身份比较/hash/原序列化，公开符号比较生成 recipe | semantic authoring / exact inputs / refs tests | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |
| A02 | 模块级匿名组合与装饰器内同表达式闭合等价；alias 只发布一次 | `test_deferred_authoring.py` | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |
| A03 | 默认时间、权限坐标、关系、restricted-Ibis 依赖后声明仍正确 | `test_semantic_expressions.py` | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |
| A04 | exact forward field、derived Measure、Metric、Entity、Relationship 均可解析；missing/wrong kind 不猜测 | loader / deferred authoring | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |
| A05 | 同名不同 Entity Measure 正确索引；物理/派生命名冲突；根提示不制造目标；带点 domain/错误段数先拒绝 | authoring / loader | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |
| A06 | 直接声明、symbol 自环、间接循环有确定依赖链；不同文件发现顺序不改变语义 | loader diagnostics | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |
| A07 | unit 继承、兼容断言、乘除权限、fixed status-time、foreign axis 错误保持 | semantic expressions / authoring | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |
| A08 | identity/membership/coordinates/axes/per，以及 project/set/product/classify/image/preimage/where/count/in_population 组合绑定等价，共享中立 builder，保持 owner、路径、时间和 body 调用边界 | `test_deferred_authoring.py`、population tests | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |
| A09 | 不执行作者函数、不二次 import、不后读 globals；失败不产生部分 Catalog | expression binding / loader | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |
| A10 | 同模板绑定 A/B 不污染；旧 Catalog 不变；新范围同路径变化无原版本硬拒绝 | exact inputs / expression binding | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |
| A11 | 同闭合语义指纹一致；default-time/unit/permission/关系事实变化反映真实消费 | definition identity / expressions | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |
| A12 | 三种消费边界保持；A 的旧闭合字段作为 B 新命名声明 body，B 同路径定义更新后仍可发布，既不刷新 A 事实也不因版本不同拒绝 | semantic expressions / Catalog | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |
| A13 | source-free load；Session-owned publication 和未绑定分析输入拒绝；不绕过原范围过滤 | loader / analysis capture | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |
| A14 | 相同固定数据独立 oracle：过滤、默认时间、Measure 派生、Metric 比值、样本、null/empty 数值一致 | 现有 DuckDB 语义/分析 fixtures | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |
| A15 | 原相关持久化/冷读用例继续通过，符号不进入闭合执行/Store | 现有 persistence/recovery tests | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |
| A16 | finalize/公开 Ref 旧协议移除，无 ms.symbol；ref 工厂/构造器/selector/映射新合同可发现；上下文预算不放宽 | surface / semantic help / CLI | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |
| A17 | describe/readiness/preview/source_check 及 md.test/inspect 接收 native selector，拒绝复合表达式/错 kind，保留当前作用域和 entry ownership 检查 | Catalog / source health / datasource surface | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |
| A18 | spec.ref、entry.ref、collection.refs、analysis_ready_inputs 等可再次用于合法查找；`.get()` 闭合值与导航 symbol 不混用；结果比较/展示不触发 Deferred bool；§2.1 Analysis adapters 只传内部键/parameter | metadata / Catalog / readiness / datasource results | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |
| A19 | preview/source_health 多 Entity pair sequence 和 source bindings 的重复/缺失/多余输入、单 Entity 特例均保持规则；归一化内部映射一致 | preview scope / source bindings tests | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |
| A20 | 内部 RefPayload/Pydantic/CLI 现有输入输出一致，无 Deferred 持久化；公开 facade 与内部工厂隔离，无 datasource/semantic 或 semantic/analysis 循环依赖 | refs / import contracts / relevant codecs | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |
| A21 | 声明/参数捕获即拒绝 summ/未知字段/旧身份属性的 recipe，前向有效引用保留；诊断定位消费点和子路径；普通 Python 属性访问不要求立即失败，已格式化字符串只遵循接收参数原规则 | expression capture / typed diagnostics | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |
| A22 | EntitySymbol 接受，DatasourceSymbol/普通 Deferred 拒绝于 Entity 身份参数；对应十种角色签名与内部 kind 检查一致，不扩展成完整表达式类型系统 | 聚焦 typing 与 factory tests | 已通过本地验收；逐项依据见[实施进度](symbolic-authoring-progress.md#acceptance-and-evidence) |

A14 不用“旧实现与新实现输出相同”作为唯一 oracle；使用已知业务 fixture 的独立
预期或现有 SQL witness。A15 只证明恢复边界，不能代替 A14 数值证据。
循环测试不需要穷举所有运算符；选择直接、自环、跨 Measure/Metric 和参数依赖的代表。
A08 复用现有 builder 的语义测试，以少量组合覆盖符号绑定遗漏：有限集合加投影、
product 加 count/per、identity 域的 classify、image/preimage 的路径及锚点保持。
另外保留 Session-owned 组合拒绝，以及同一 Population helper 在模块级允许、
在当前不支持的 decorator 位置拒绝的边界；不建立完整运算符笛卡尔测试矩阵。
A17–A20 覆盖直接 ref 修订新增的公开边界风险；按现有检查/序列化测试补充代表用例，
不重复所有 provider 执行用例。

## 5. 清理清单与迁移规则

| 范围 | 查找与动作 | 完成条件 |
| --- | --- | --- |
| public entrypoints | `finalize`、`FinalizedDeclarations`、`ms.Ref`、native Help targets、导出快照 | 无公开旧入口，无 ms.symbol/no-op/兼容别名；内部 Ref 与 factory 保留 |
| contexts | `prior_contexts`、`ctx.finalized`、late-capture guards | 无人工关闭逻辑；共享调用者已迁移 |
| recipes | constructor Ref 假设、立即 consume、permission 元数据 | 符号 capture 与闭合检查职责明确 |
| fixtures/examples | `ms.finalize()`、`frozen.resolve(...)`、旧 key/getter/hash 用法 | 构造器或 ms.ref.* 直接产生符号；metadata/lookup 例子与返回合同一致 |
| identity inputs/outputs | source_check、Catalog normalization、datasource spec.ref/results、temporal inputs、readiness 结果 | public native selector ↔ private identity 的转换归各自边界，无公开 raw-key 双轨 |
| Python identity maps | PreviewScope / PreviewSourceBindings | 改 typed pair sequence；旧 Ref-keyed Mapping 删除，内部映射与 wire 不变 |
| tests | finalization-only 断言 | 删除旧状态机断言，保留前向、隔离、默认时间和导入一次等合同证据 |
| docs | owning docs、site latest EN/ZH、CLI examples、API docstrings，含 `semantic/__init__.py` 模块首屏 | 当前说明一致，历史发布内容明确保留历史归属 |
| packaged skill | `marivo-semantic` 的 finalize/resolve 工作流与 `marivo-analysis` 身份查找条款 | 已获得命名文件授权并完成相关条款对齐 |
| AGENTS.md | 原要求 finalize 后 composition 的仓库条款 | 已获得命名文件授权并完成相关条款对齐 |

不要全仓替换 `.resolve`。Ibis resolver、自有内部解析器和其他领域方法各有消费者。
不要把本设计的历史基线说明作为“旧 API 残留”误删。只对仍表示当前用法的内容做迁移。
最终 diff 检查应包含 untracked 文件，并读取当前 site latest 示例；不覆盖并行工作。

## 6. 验证与审查顺序

1. 每阶段先运行最小有用测试、修改模块 typing 和 focused lint，记录真实命令与结果。
2. S4–S6 集成后运行语义/加载/分析相关测试，再运行共享行为需要的 `make test`。
3. 代码整合稳定后进行正式审查；最多两轮，关闭 P2+ 问题并记录对应修复和复验。
   本次已按用户团队实施要求，由其他模块 agent 交叉独立审查。
4. 最终未变化源码运行不带缩小变量的 `make check-agent`，并运行实际受影响的
   site 内容检查。出现新修改或失败后只复验相关范围，再完成必要最终门禁。
   本次完整运行的源码检查和 5,302 项测试通过，只有 API 文档标题格式失败；
   修正文档后 `make docs-api-agent` 通过。按用户最新指示，不为该文档修复重跑完整门禁。
5. 若触及 provider lowering/执行路由，增加相关 Runtime 测试；纯 authoring 绑定改造
   不默认启动全 provider 环境。发布验收、安装包验收均不由本计划的本地结果代替。

示例起始命令（实施时随实际文件调整）：

```sh
make test TESTS='tests/semantic/test_deferred_authoring.py tests/semantic/test_semantic_expressions.py tests/semantic/test_authoring_exact_inputs.py'
make typecheck TYPECHECK_TARGETS='marivo/semantic marivo/_definition marivo/datasource marivo/refs.py marivo/analysis/dsl/population.py'
make lint-agent LINT_TARGETS='marivo/semantic marivo/_definition marivo/datasource marivo/refs.py marivo/analysis/dsl/population.py'
make check-agent
```

实施前的文档检查仅证明设计一致性。当前实现的真实测试、数值、审查及门禁结果
单独记录在[实施进度与证据](symbolic-authoring-progress.md)，不沿用历史设计审查结论。

## 7. 证据账本

### 实施前设计阶段的历史证据

以下状态仅描述当时的设计修订；当前实施状态见[实施进度与证据](symbolic-authoring-progress.md)。

| 记录 | 状态 / 证据 |
| --- | --- |
| 当前代码和契约阅读 | 已读 constructors、capture builder、Deferred leaf/consumer、loader publication、compiled state、Ref、source-free grouping/Population、当前架构文档 |
| 当前 Live Help | 已读取 `semantic.finalize`、`semantic.metric`，确认当前仍要求显式冻结 |
| D01–D15 方案 | 已补捕获校验、名义类型、Analysis 清单、强制 builder 下沉；C1 按用户确认在设计层闭合，未实现 |
| 本轮文档检查 | 核查两份文档相对链接、代码块配对、Python 示例语法及空白；最终结果见本节末的本轮证据，其他工作区改动未纳入 |
| A01–A22 | 接口验收全部待实施；A22 只有独立静态可行性探针，不能视为新接口通过 |
| 原包装式方案正式审查 R1 / R2 | R1 合并两项 P2，R2 三个审查者确认关闭；结论仅适用于当时快照 |
| 直接 ref 修订审查 | 本轮已做代码影响分析和文档一致性自检；未执行新的独立审查，不沿用原 R2 通过状态 |
| 本地 / Runtime / installed / release | 仅文档核查和 Ibis/mypy 可行性探针；均无实现、数值或发布验收证据 |
| commit / push | 未执行 |

### 历史设计独立审查记录

R1 绑定 HEAD `8cd8e08c9345eb28ff36ee55d089e395a93ed452`，两文档为未跟踪文件。
初稿 SHA-256：设计 `5fb41bdb2c289ac0cec1cd806be1638009414a0d34912c4a54077658e647331f`；
计划 `c3ef75fca2cc9f23af6d4b83609d4e42a50dc57756a7f7cdfae47ce55932d722`。
遵循 `marivo-review` 及 proposal-review；审查者只读，负责人统一修改两份文档。

| Finding | 审查者 / 证据 | 修订与验收落点 | 状态 |
| --- | --- | --- | --- |
| R1-F1 / P2：遗漏可发布的无 Session Population 程序 | review_symbols、review_scope 独立确认；当前 loader 成功加载有限 set/project、product、classify 的模块级 count/per 探针；image/preimage 的 owner 传递经静态代码检查 | 设计 §5.2 完整受控操作和 source-program 边界；S3/A08 补组合验收并保留 body 调用限制 | R2：review_symbols、review_scope 确认关闭 |
| R1-F2 / P2：全图版本一致性会拒绝合法旧闭合输入 | review_binding；当前 `_closure_scope_error` 只查存在/kind，`consume_metric_in_state` 区分本地捕获与发布引用 | 设计 §6.4 限定新生成引用检查，§7 保留旧事实；S4/A12 增加 A/B 旧闭合字段发布用例 | R2：review_binding 确认关闭 |

R1 临时模型探针只证明基线能力，不证明符号提案已实现；没有修改仓库测试或代码。
Metric 根 body 不自引用的说明一并明确，用于避免实现误读，不另计一个 finding。

R2 三个审查者均核对了相同文档快照：设计 SHA-256
`d5478a8f499542233178809fca75569c105a2c700a83a23be9cb4114168da19f`；计划 SHA-256
`2daf46eb2e7c7f5bfb183cd6c3eb4f3886b316aca0941e5920ad7605bc978182`。
当时三个结论均为 `No remaining P2+`，并检查了相邻合同与修订回归。该结论限定于
上述包装式方案快照。本次按用户要求改变公开 ref 合同，原复审不覆盖新增边界；
Population 闭包和旧闭合输入两个修复继续保留。

收尾 HEAD 为 `d288fcdd1ab2010c22334e22d83d6d7edaa745e1`，由并行工作提交原 site
重写和两个 disclosure 测试；本审查未创建提交。与审查基线比较，`marivo/`、
`docs/architecture/`、`AGENTS.md`、`marivo-review` 技能均无提交差异。
当时工作区仅这两份设计文档未跟踪，文档检查通过。
以上是旧修订的设计审查证据，不证明当前 A01–A22 或任何 Runtime 行为已经实现/验收。

### 上一轮直接 ref 修订的影响记录

按本轮用户指示撤销包装入口，公开 `ms.ref.*` 改为符号工厂，内部键保持原协议。
重新检查了 `refs.py` 工厂/相等/哈希/Pydantic/wire、Semantic re-export、Catalog
strict Ref normalization 与公开字段、source_check validators、PreviewScope/
PreviewSourceBindings、datasource spec.ref/manage/inspection，以及 temporal 声明输入。
确认不能仅替换一个 factory：S1b/A17–A20 是让现有身份入口仍可用的必要适配。
当前 HEAD 为 `d288fcdd1ab2010c22334e22d83d6d7edaa745e1`；本轮只改两份文档，
既有时间过滤优化源码/测试和计划未修改。本轮未启动子 agent、未执行实现验收。

### 本轮外部 review 意见甄别

这是用户提供意见后的代码核对及文档修订，不是新的独立 agent 复审；不沿用历史
R2 的无 P2+ 结论。下表“采纳”指方案修订完成，代码验收仍未执行。

| 意见 | 判断与处理 | 落点 / 状态 |
| --- | --- | --- |
| C1：符号误用静默通过 | 保留原生 Deferred，每个声明/参数捕获点立即检查完整 recipe；用户明确接受普通 Python 属性访问不立即失败。未消费 recipe 和 f-string 不追溯来源，不新增 wrapper 或 monkeypatch | D15、设计 §5.1、S1/S6/A21；**P2 已在设计层闭合，实施待验收** |
| C2：Analysis 缺调用清单 | 采纳。补十二个模块，区分公开 Entity/Relationship adapters、内部 ExactRef/program 消费及 Help 文本；不能按 `.ref` 文本命中一律改写 | 设计 §2.1、S1b/S5/A18/A20；设计缺口已修订 |
| C3：Population builder 下沉必需 | 采纳。命名中立目标模块并列出共享构造/检查范围，保留公开适配、Frame inputs 和执行 owner | D13、设计 §5.2、S3/A08/A20；设计缺口已修订 |
| C4：纯身份参数类型过宽 | 采纳问题，不采用不可继承的 Deferred 子类，也不宣称获准例外；采用 NewType 名义标记，纯身份参数不接普通 Deferred。既有闭合字段/一般表达式继续 runtime 校验 | D14、设计 §3.3、S1/S1b/A22；有最小静态可行性证据 |
| C5：Measure root 路径假设 | 采纳澄清。refs.py 已规定 Domain 一段、Measure 三段及字符规则；先校验再取根，不增加 domain 索引解析器 | 设计 §6.2、S4/A05 |
| C6：单 Entity 输入不对称 | 保留并解释。原 scope 已有 AuthoringScope 特例，source_bindings 始终显式 Entity 寻址；本次只替换映射载体，不扩展重载 | 设计 §3.5、S1b/A19 |
| C7：容器成员判断不稳定 | 采纳。明确排除 in/index/count 作为身份操作，说明 Python identity shortcut；不为偶然结果加自定义 equality | 设计 §3.1、S1/S6 |
| M1：归约后 where 示例 | 核实 `_deferred_semantics.py` 的方法分支支持；保留样例并说明不等于 retired 顶层 ms.where | 设计 §3.2 |
| M2：paid_at 后声明 | 采纳，补英文 inline 注释说明完整范围默认时间解析 | 设计 §3.2 |
| M3：模块 docstring | 采纳，单列 semantic/__init__.py finalize/resolve 首屏示例清理 | 设计 §9、S6/清理清单 |
| M4：不变量重复 | 采纳收束，执行/持久化闭合由设计 §7 统一拥有；§3.4 只说明具体边界转换，其他处引用 | 设计 §2.1/§5.1/§7 |

C1 关闭依据（2026-10-22）：用户明确要求“保留原生 Deferred，接受在声明和参数捕获
边界报错，不要求普通 Python 属性访问立即失败”。因此 D15 已确定，移除待确认的
返回对象选择及属性访问即时报错验收要求；完整 recipe 的捕获校验和 S6 迁移清理保留。
本轮提供的 review 意见已无待闭合 P2+ 设计项；这不构成新的独立复审结论，也不证明
A01–A22 已通过。未来实施按该合同执行，无需重新确认相同取舍。

前轮代码核对与探针证据（本次闭合复用，无源码或测试文件修改）：

- 核对本地 Ibis `Deferred.__getattr__`、`resolver`、`deferrable` 和 Final 限制；进程内
  继承尝试得到 `TypeError: Cannot inherit from final class`。因此不采纳 typed subclass。
- `.venv/bin/mypy --follow-imports=silent --no-incremental -c ...` 使用仓库配置：
  EntitySymbol 参数接受 EntitySymbol，拒绝 DatasourceSymbol 与普通 Deferred，得到预期
  两条 arg-type 错误；运行时 NewType 包装后仍是 Deferred。仅证明标记方案可行。
- 核对 `refs.py` 路径验证、`preview_scope.py` 输入类型、Metric where consumer、
  Analysis 真实接收类型及 Population builder 依赖；修订只写入两份设计文档。
- 原生 Deferred 的进程内探针复现同对象 membership 为 True、等路径新对象触发 TypeError，
  以及属性 recipe 可被 f-string 转为文本；这说明 C1/C7 的限制真实存在。
- 两份文档的相对链接、代码块配对、Python 示例语法、验收表连续性及
  `git diff --no-index --check /dev/null <document>` 空白检查通过。

本次闭合仅修改两份文档，复核 D15、§5.1、S0/S1/S6、A21 和本账本的合同与状态一致，
并重跑文档链接、代码块、示例语法及空白检查；未运行实现测试或新增独立审查。

## 8. 授权与完成边界

本次用户已授权完整代码、测试和普通文档实施，并另行明确批准 AGENTS.md、
`marivo/skills/marivo-semantic/SKILL.md` 和 `marivo/skills/marivo-analysis/SKILL.md`
的相关条款对齐。三个命名文件均已完成对齐，不存在待授权的实施项。

实施完成要求完成 S1–S7（含 S1b）、A01–A22、必要 disclosure/技能/仓库条款对齐及真实最终门禁
证据闭合；发布、推送或版本上线不在该完成定义内。
