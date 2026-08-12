<p align="center">
  <a href="https://marivo.io" target="_blank">
    <img src="https://raw.githubusercontent.com/chengxianglibra/marivo/main/site/src/assets/marivo-mark.svg" alt="Marivo" width="128">
  </a>
</p>

<h1 align="center">Marivo</h1>

<p align="center">
  <em>面向 AI 智能体的数据分析 Harness：把业务语义、分析过程、会话状态和证据连接在一起。</em>
</p>

<p align="center">
  <a href="https://marivo.io/zh-cn/docs/latest/" target="_blank"><strong>文档</strong></a> ·
  <a href="https://marivo.io/zh-cn/docs/latest/first-analysis/" target="_blank"><strong>第一次分析</strong></a> ·
  <a href="https://discord.gg/8WqCzeaYk" target="_blank"><strong>Discord</strong></a> ·
  <a href="https://marivo.io/zh-cn/blog/" target="_blank"><strong>博客</strong></a>
</p>

<p align="center">
  <a href="README.md"><b>English</b></a>
  <b> | </b>
  <b>简体中文</b>
</p>

<p align="center">
  <a href="https://github.com/chengxianglibra/marivo/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/chengxianglibra/marivo/ci.yml?branch=main&label=CI" alt="CI"></a>
  <a href="https://pypi.org/project/marivo/"><img src="https://img.shields.io/pypi/v/marivo" alt="PyPI version"></a>
  <a href="https://pypi.org/project/marivo/"><img src="https://img.shields.io/pypi/pyversions/marivo" alt="Python versions"></a>
  <a href="https://github.com/chengxianglibra/marivo/blob/main/LICENSE"><img src="https://img.shields.io/github/license/chengxianglibra/marivo" alt="License"></a>
</p>

**Marivo** 是一个 Python 框架，让 AI 智能体基于共享语义、类型化分析操作、持久化会话和可追溯证据分析业务数据。它把开放式业务问题转化为可以检查、继续和复盘的调查过程。

**核心能力**

- 🧭 **共享语义**：一次定义指标、维度、关系和使用约束，不需要在每次查询中重新推导
- 🧮 **类型化分析**：通过有明确输入、输出和失败边界的算子推进分析
- 🗂️ **持久化会话**：把问题、中间结果和分析产物保存在同一条调查线上，随时可以继续
- 🔎 **可追溯证据**：让分析发现始终连接到来源结果、分析范围和限制

Marivo 不是托管的聊天界面，也不是 Text-to-SQL 包装器。智能体使用已经声明的业务含义和有明确边界的分析操作，不需要在每次 SQL 查询中重新推导指标、关联关系和分析逻辑。

## 为什么需要 Marivo

如果只把原始表结构交给智能体并让它生成 SQL，很多重要选择都是隐含的：指标表示什么、哪些记录应纳入统计、表之间如何关联、采用什么比较方式，以及结论由哪些证据支持。这些选择容易随提示词变化，事后也很难复查。

Marivo 把这些选择变成可以复用和检查的契约。业务定义保存在由代码管理的语义层中，分析通过类型化操作推进，重要结果则与产生它们的分析会话和证据保持关联。

## 四个核心能力

### Semantic Layer

Python 声明以稳定引用定义数据源绑定、实体、关系、指标、维度和使用约束。智能体可以检查证据并起草定义；用户或业务负责人确认业务口径。

### Typed Analysis DSL

`observe`、`compare`、`attribute` 等类型化算子为智能体提供明确的分析操作，并返回类型化结果对象。不支持的步骤会通过契约明确失败，而不是隐藏在自由编写的 SQL 中。

### Analysis Session

每次调查都在项目本地保存问题、中间结果、分析产物和历史记录。智能体可以在已有结果上继续分析，不需要重新构造上下文或重复已经完成的工作。

### Evidence Engine

确定性的类型化 findings 会与来源结果保持关联，并投影为按 operator 定义的有界 digest。
Marivo 不调用 LLM，也不生成跨 artifact 判断：综合与下一步选择由智能体负责，类型化推断边界、
省略信息与精确审计读取让最终结论仍然可检查。

分析开始前，就绪检查会验证当前任务所需语义对象的技术交接状态。它会阻止不完整的定义进入分析，但不会把技术就绪当作对业务口径的批准。

## 如何使用 Marivo

1. **安装并初始化项目。** Marivo 创建项目结构，并让兼容的智能体可以使用内置的 `marivo-semantic` 和 `marivo-analysis` skills。
2. **准备语义层。** 已有项目直接复用现有定义；新项目可以让智能体通过 `marivo-semantic` 起草所需定义。
3. **说明业务问题。** 智能体通过 `marivo-analysis` 检查就绪状态、选择类型化分析步骤、保存证据，并返回结论和限制。

用户只需确认会明显改变业务含义或结论用途的选择，不需要编写 Python、选择算子、管理分析会话或指定证据字段。

## 快速开始

Marivo 需要 Python 3.12 或更高版本。进入要作为 Marivo 项目的目录，然后运行：

```bash
curl -fsSL https://marivo.io/install-marivo-cn.sh | bash
```

安装脚本会使用 `uv` 准备项目本地环境并初始化当前目录。它支持 macOS、Linux、WSL，以及
Windows 上的 Git Bash、MSYS2 或 Cygwin。手动安装、数据源扩展、支持平台和故障排查请参阅[安装文档](https://marivo.io/zh-cn/docs/latest/installation/)。

先验证一次选定的运行环境，再进入 Python 查询 focused help：

```bash
python -m marivo help
```

```python
import marivo

marivo.help()
marivo.help("semantic.metric")
marivo.help("analysis.observe")
```

如果项目已经包含 `marivo.toml` 和 `models/`，直接复用现有语义层。新项目只需告诉智能体要使用的数据源和业务目标，再在分析前确认智能体提出的指标含义。

指标准备就绪后，可以直接提出业务问题：

> 使用 Marivo 分析已经确认的 `sales.revenue` 指标为什么在上季度相比去年同期下降。先关注地区差异，最后给出结论、关键证据和限制。

内置 skills 会处理语义目录浏览、就绪检查、算子选择、分析会话管理和证据收集。

## 文档

- [安装](https://marivo.io/zh-cn/docs/latest/installation/)
- [快速开始](https://marivo.io/zh-cn/docs/latest/quick-start/)
- [让智能体完成第一次分析](https://marivo.io/zh-cn/docs/latest/first-analysis/)
- [语义层](https://marivo.io/zh-cn/docs/latest/concepts/semantic-layer/)
- [分析流程](https://marivo.io/zh-cn/docs/latest/concepts/analysis-workflow/)
- [证据链](https://marivo.io/zh-cn/docs/latest/concepts/evidence/)

## 开发

```bash
uv venv --python 3.12 --seed
uv pip install --python .venv/bin/python -e ".[dev,duckdb,trino]"
```

通过仓库入口执行检查：

```bash
make format
make lint
make typecheck
make test
make check
```

贡献前请阅读 [`agent-guide.md`](agent-guide.md)。完整流程见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。
