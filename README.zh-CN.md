# Marivo

[English](README.md) | 简体中文

面向 AI agents 的以指标为中心的分析运行时。

Marivo 是一个 Python library，不是托管服务，也不是 chat UI。它把数据仓库转换成
AI agent 可以*可靠*分析的对象。它不是 text-to-SQL wrapper：Python declarations
是契约，ibis expressions 是执行语言，typed frames 是分析步骤之间的边界。

- **统一语义** — Datasources、entities、metrics、dimensions 和 relationships
  都用 Python 声明，并通过 semantic ref（`sales.revenue`）引用，而不是直接使用原始表名或列名。
- **以指标为中心的分析** — Sessions 从可信 metric 出发，串联 typed intents
  （observe、compare、decompose、correlate、forecast），每一步都返回 typed frame。
- **可信证据** — 每个 operator 都记录 findings、propositions 和 assessments，
  因此任何结论都可以追溯到它的输入。
- **就绪门禁** — `catalog.readiness()` 会在 authoring 和访问问题解决之前，
  阻止不完整的 semantic objects 进入分析。

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
session.decompose(delta, axis=region).show()
```

项目内每个 surface 都提供帮助：`ms.help()` 和 `mv.help()` 会列出 surface；
`ms.help("metric")` 和 `mv.help("observe")` 会进入具体 symbol。

## 文档

完整指南在 documentation site 中维护（由 [`site/`](site/) 构建）：

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
make lint
make typecheck
make examples-check
make test
make check
```

贡献前请阅读 [`agent-guide.md`](agent-guide.md)。完整流程见
[`CONTRIBUTING.md`](CONTRIBUTING.md)。
