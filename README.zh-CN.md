# Marivo

[English](README.md) | 简体中文

面向 AI agents 的以指标为中心的分析运行时。

Marivo 是一个 Python library，不是托管服务，也不是 chat UI。它把数据仓库转换成
AI agent 可以*可靠*分析的对象。它为 agent 提供了三样原本缺失的东西：一个类型化的
**语义层**，固定每个 metric 和 dimension 的含义；一套**以指标为中心的分析流程**，
由类型化 operators 组成；以及一条**可审计的证据链**，贯穿每个结论。

Marivo 不是 text-to-SQL wrapper。Python declarations 是契约，ibis expressions
是执行语言，typed frames 是分析步骤之间的边界 —— agent 组合的是经过声明的可信构件，
而不是临时生成一段原始 SQL 再假设它正确。

- **统一语义** — Datasources、entities、metrics、dimensions 和 relationships
  都用 Python 声明，并通过 semantic ref（`sales.revenue`）引用，而不是直接使用原始表名或列名。
- **以指标为中心的分析** — Sessions 从可信 metric 出发，串联 typed intents
  （observe、compare、attribute、correlate、forecast），每一步都返回 typed frame。
- **可审计证据** — 每个 operator 都记录 findings、propositions 和 assessments，
  因此任何结论都可以追溯到产生它的输入。
- **就绪门禁** — `catalog.readiness()` 会在 authoring 和访问问题解决之前，
  阻止不完整的 semantic objects 进入分析。

## 为什么需要 Marivo

如果直接把 agent 指向原始仓库并让它写 SQL，失败方式通常很明确：它可能臆造 join、
漏掉必需的过滤条件、误读含义不清的列，然后给出一个难以核验的答案。每次查询都从零开始，
定义无法沉淀，过程也难以复审。

Marivo 用一份 agent 必须遵循的契约，替代"生成 SQL 然后碰运气"的流程：

| 没有语义运行时 | 使用 Marivo |
|---|---|
| 每次查询都靠猜表名和列名 | 可信的 **semantic refs**（`sales.revenue`），携带人工编写的含义与 guardrails |
| 每次都重新推导定义 | 一份纳入版本控制的**声明式契约**，在 agents 与 sessions 之间共享 |
| 自由拼写的 SQL，错误往往只表现为错误的数字 | **类型化 intents 与 frames** —— 非法步骤会在任何 backend 操作之前明确报错 |
| 无法核验的结论 | 把每个结果链接回其输入的**证据链** |
| 半成品模型照样被分析 | 阻止未完成 semantics 进入分析的**就绪门禁** |

## 它如何工作

1. **声明**：在 `models/datasources/` 中声明 datasources，在 `models/semantic/`
   中声明 semantics（domains、entities、dimensions、metrics）—— 使用
   `marivo.datasource`（`md`）和 `marivo.semantic`（`ms`）。

2. **加载并检查就绪状态**：`ms.load()` 构建 catalog；`ms.readiness()` 会在 agent
   所需对象完整且可达之前阻止分析。

3. **打开 session**：从一个 guiding question 出发，并从 catalog 解析出一个可信 metric。

4. **串联 typed intents** —— `observe → compare → attribute` 等 —— 每一步返回一个
   typed frame，并写入 evidence。

## 要求

Python 3.12 或更高版本。

## 安装

```bash
pip install marivo
```

根据你的 datasource 安装对应的 backend extra：

| Backend | 安装命令 |
| --- | --- |
| DuckDB | `pip install "marivo[duckdb]"` |
| MySQL | `pip install "marivo[mysql]"` |
| Postgres | `pip install "marivo[postgres]"` |
| ClickHouse | `pip install "marivo[clickhouse]"` |
| Trino | `pip install "marivo[trino]"` |
| 所有已打包的 backends | `pip install "marivo[all]"` |

部署 Marivo 时，只需要在 agent 运行的环境中安装 library，提交 `models/`
declarations，并通过 `*_env` 字段引用的环境变量提供 datasource secrets。
Marivo 没有独立 server。

如果设置失败，先运行只读的 doctor：

```bash
marivo doctor
marivo doctor --semantic
marivo doctor --datasource warehouse --connect
```

默认 doctor 会检查安装状态、project layout、datasource declarations、backend extras、
secret references，以及已有 `.marivo/` 状态；它不会连接数据库，也不会写入 secrets。
使用 `--semantic` 查看 semantic load/readiness diagnostics；只有在你明确希望执行一次
live datasource round-trip 时才使用 `--connect`。

## 快速开始

用 CLI 初始化 project：

```bash
marivo --version
marivo init
```

`marivo init` 会创建 project skeleton（`marivo.toml`、`models/`、`.marivo/`），
并把 `marivo-semantic` 和 `marivo-analysis` skills 安装到共享 `.agents/skills`
目录，以及 Claude Code 和 Codex 的兼容目录中。这样 coding agent 就可以和你一起编写
semantic layer。

在 `models/semantic/` 下声明 semantics，然后加载并分析：

```python
import marivo.semantic as ms
import marivo.analysis as mv

catalog = ms.load()
report = catalog.readiness()
if report.status == "blocked":
    report.show()                       # 分析前先解决 blockers

session = mv.session.get_or_create(name="revenue-check", question="Why did Q4 drop?")
revenue = session.catalog.get("metric.sales.revenue")
region = session.catalog.get("dimension.sales.orders.region")

current = session.observe(revenue, time_scope={"start": "2026-10-01", "end": "2027-01-01"}, grain="month", dimensions=[region])
baseline = session.observe(revenue, time_scope={"start": "2025-10-01", "end": "2026-01-01"}, grain="month", dimensions=[region])

delta = session.compare(current, baseline)
attribution = session.attribute(delta, axes=[region])
attribution.show()
```

项目内每个 surface 都提供帮助：`ms.help()` 和 `mv.help()` 会列出 surface；
`ms.help("metric")` 和 `mv.help("observe")` 会进入具体 symbol。

## 发布

将完成的交付物上传到 S3：

```bash
marivo publish ./report.html
marivo publish ./output-dir
```

在 `marivo.toml` 中配置非 secret 的 S3 设置，在 `~/.marivo/secrets.toml` 中配置
credentials。详见[文档](https://marivo.io/zh-cn/latest/installation/#部署与开发)。

## 文档

完整指南在 [marivo.io](https://marivo.io/zh-cn/latest/) 维护：

- **安装** — 安装、`marivo init`、部署。
- **快速开始** — 编写 declarations、加载 catalog、运行第一次分析。
- **概念** — semantic layer、analysis workflow、readiness 和 evidence。

维护中的 agent guidance 位于 `marivo/skills/marivo-semantic` 和
`marivo/skills/marivo-analysis`。

## 开发

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,duckdb,trino]"
```

Repository commands 会通过 `make` 使用本地 virtualenv：

```bash
make format
make lint
make typecheck
make examples-check
make test
make check
```

贡献前请阅读 [`agent-guide.md`](agent-guide.md)。完整流程见
[`CONTRIBUTING.md`](CONTRIBUTING.md)。