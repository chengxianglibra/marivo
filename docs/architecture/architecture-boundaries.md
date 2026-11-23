# 架构边界与静态检查

Marivo 的依赖从应用入口流向领域模块与共享定义。跨域使用公开入口或明确的 SPI。
静态检查覆盖运行时导入、`TYPE_CHECKING` 导入和函数内导入。下面是按职责划分的层位，
精确的导入顺序、并列项和受保护模块由配置定义。[Import Linter 契约](../../.importlinter)

## 顶层职责

| 层位 | 模块与职责 |
| --- | --- |
| L0 | `_kernel` 持有引用、分组、时区、渲染和结构化错误等共享原语。 |
| L1 | `_definition` 持有惰性表达式、依赖身份与定义图，允许 Ibis 表达式。`introspection` 提供中立的 Help 注册和渲染。`telemetry` 记录调用，保持对领域实现的隔离。`_authoring` 提供共享声明组件。 |
| L2 | `datasource` 持有凭据、物理来源绑定、引擎策略和执行。具体引擎实现由 Datasource 内部装配。 |
| L3 | `semantic` 持有业务定义、编译、校验和 Catalog。 |
| L3.5 | `ontology` 在已加载的 Semantic Catalog 上验证上下文边，可通过 Semantic 间接访问 Datasource。 |
| L4 | `analysis` 捕获 Population 和观察，编排 Frame 生命周期并计算、发布、读取结果。 |
| L5 | `_help` 路由领域 Help。`server` 提供 HTTP v2。`cli` 和 `__main__` 组织命令入口。`skills` 提供随包分发的 Agent 工作流。 |

Datasource SPI 提供会话、来源元数据和方言策略。Semantic SPI 提供已编译状态、
表达式绑定和 Catalog 投影。HTTP v2 通过 Analysis Session 读取 SPI 找到并打开已提交
结果，沿用相同的分页、游标和持久化校验。
[Datasource SPI](../../marivo/datasource/spi.py) ·
[Semantic SPI](../../marivo/semantic/spi.py) ·
[Analysis 读取 SPI](../../marivo/analysis/session/read_spi.py)

## Analysis 内部分工

| 模块 | 职责 |
| --- | --- |
| [Session](../../marivo/analysis/session/) | 捕获工作区与会话所有权，组织来源调用和已提交结果的读取。 |
| [DSL](../../marivo/analysis/dsl/) | 构造不可变的 Frame、Column 和 Population，绑定用户输入。 |
| [物化](../../marivo/analysis/materialization/) | 准备来源、运行计划、发布或读取结果。 |
| [本地方法](../../marivo/analysis/methods/) | 对已检查的阶段数据求值。 |
| [编译](../../marivo/analysis/compiler/) | 将已绑定的查询转换为阶段计划和后端表达式。 |
| [核心](../../marivo/analysis/core/) | 持有不可变 Frame 节点、类型推导、冻结数据与归因事实。 |
| [绑定](../../marivo/analysis/binding/) | 解析根、来源、坐标、时间和业务目标。 |
| [模型](../../marivo/analysis/model/) | 持有轻量数据契约、持久化身份和错误。 |

Import Linter 锁定上表的相对依赖顺序，并从 `marivo` 根包递归检查同级依赖。
架构棘轮分别扫描运行时图与完整图。模型有独立的依赖禁区，编译器也有单独的
物化和 Session 禁区。[Analysis 导入契约](../../.importlinter) ·
[架构棘轮](../../scripts/architecture_baseline.json)

## 日常检查

| 检查 | 覆盖内容 |
| --- | --- |
| Ruff | `PLC0415` 检查函数内导入。库和测试的可选依赖装载使用逐行理由。`PLR0915` 将单函数语句数限制为 80。`TID251` 检查受禁 API，`SLF001` 在 `_kernel`、Ontology 和 Server 生效。[Ruff 配置](../../pyproject.toml) |
| Mypy | 对库和测试执行严格类型检查，并要求 `type: ignore` 指定错误码。[类型检查配置](../../pyproject.toml) |
| Import Linter | 检查层级、同级无环范围、受保护 SPI、引擎隔离与重依赖边界。[导入契约](../../.importlinter) |
| ARCH001–ARCH006 | 检查跨包私有导入、域外引擎判断、动态逃生点、两张导入图的循环边、结构与类型预算，以及独立进程的冷启动和 Help 装载范围。[架构检查脚本](../../scripts/check_architecture.py) |

`make lint` 同时运行 Ruff 和两项全库架构检查。`LINT_TARGETS` 仅缩窄
Ruff 的文件范围。只有实际债务下降后才用
`.venv/bin/python scripts/check_architecture.py --ratchet-down` 更新基线。
Pull request 的治理检查使用基线版本的脚本（若存在），阻止新增导入豁免和预算抬升。
[Makefile](../../Makefile) · [Pre-commit](../../.pre-commit-config.yaml) ·
[CI 工作流](../../.github/workflows/ci.yml)
