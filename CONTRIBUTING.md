# Marivo 开发规范

## 代码风格

### 基本规范
- 遵循 PEP 8 Python代码风格指南
- 使用 Ruff 进行代码格式化和linting
- 行长度限制：100字符
- 缩进：4个空格
- 使用类型注解（Type Hints）

### 命名规范
- 类名：`PascalCase`（例如：`SemanticService`, `QueryRouter`）
- 函数/变量：`snake_case`（例如：`create_entity`, `table_name`）
- 常量：`UPPER_SNAKE_CASE`（例如：`MAX_RETRIES`, `DEFAULT_TIMEOUT`）
- 私有成员：`_leading_underscore`（例如：`_internal_method`）

### 导入组织
导入语句按以下顺序组织：
1. Future imports (`from __future__ import annotations`)
2. 标准库导入
3. 第三方库导入
4. 本地应用导入

使用 Ruff 的 isort 功能自动排序。

### 类型注解
- 所有公共函数必须包含类型注解
- 使用 `from __future__ import annotations` 启用延迟注解
- 使用 `TYPE_CHECKING` 处理循环导入
- 使用 `| None` 而不是 `Optional[]`（Python 3.10+语法）

## 项目结构

```
marivo/
  contracts/     # 共享域类型：ID、值对象、错误码
  core/          # 纯域逻辑，零 I/O
  runtime/       # 用例编排层
  ports/         # Protocol 接口定义
  adapters/      # Port 实现（local/server）
  profiles/      # Profile 工厂（create_local_runtime / create_server_runtime）
  transports/    # 传输层（CLI, MCP, HTTP API）
  api/           # FastAPI HTTP 路由
```

## 架构约束

- `core/` 不得导入 adapter、transport 或存储库（由 import-linter 在 CI 中强制执行）
- Surface 层（transports / api）只做协议转换，必须通过 Runtime 层访问业务逻辑
- 详细约束参见 `docs/architecture-invariants.md`

## 开发工具

### 安装开发依赖
```bash
.venv/bin/pip install -e ".[dev,trino]"
```

### Pre-commit Hooks
安装pre-commit hooks以在提交前自动检查代码：
```bash
.venv/bin/pip install pre-commit
pre-commit install
```

手动运行所有检查：
```bash
pre-commit run --all-files
```

### 代码格式化
```bash
# 自动格式化（包含 ruff format 和 ruff check --fix）
make format
```

### Linting
```bash
# 检查代码问题
make lint
```

### 类型检查
```bash
make typecheck
```

### 构建 API 文档
使用 Sphinx 从公共模块（`marivo.datasource` / `marivo.semantic` /
`marivo.analysis`）的 docstring 生成 HTML API 参考。输出位于
`site/public/api/`（已在 `.gitignore` 中忽略），由 Astro 站点在 `/api/` 路径发布。

```bash
# 安装文档依赖
.venv/bin/pip install -e ".[docs]"

# 生成 API 文档
make docs-api
```

完整站点构建会先自动生成 API 文档（`site` 的 npm `prebuild` 脚本会调用
`make docs-api`），因此发布构建需在具备 Python 环境的主机上运行：

```bash
cd site && npm run build
```

## 测试

### 运行测试
```bash
# 运行所有测试（并行）
make test

# 运行特定测试文件
.venv/bin/pytest tests/test_sessions.py

# 运行特定测试方法
.venv/bin/pytest tests/test_sessions.py::SessionAPITests::test_get_session_after_create

# 显示详细输出
.venv/bin/pytest -v

# 显示print输出
.venv/bin/pytest -s
```

### 测试覆盖率
```bash
# 生成覆盖率报告
.venv/bin/pytest --cov=marivo --cov-report=term-missing

# 生成HTML覆盖率报告
.venv/bin/pytest --cov=marivo --cov-report=html
# 然后打开 htmlcov/index.html

# 生成XML覆盖率报告（用于CI）
.venv/bin/pytest --cov=marivo --cov-report=xml
```

### 测试要求
- 新功能必须包含单元测试
- 测试覆盖率目标：≥80%
- 所有测试必须通过才能合并
- 测试文件命名：`test_*.py`
- 测试类命名：`*Tests`
- 测试方法命名：`test_*`

## 提交规范

### 提交前检查清单
- [ ] 代码已格式化（`make format`）
- [ ] 通过linting检查（`make lint`）
- [ ] 通过类型检查（`make typecheck`）
- [ ] 所有测试通过（`make test`）
- [ ] 测试覆盖率满足要求
- [ ] 更新了相关文档

### 提交信息格式
使用清晰、描述性的提交信息：
```
简短的总结（50字符以内）

详细描述（如果需要）：
- 为什么做这个变更
- 解决了什么问题
- 有什么影响
```

## CI/CD

项目使用GitHub Actions进行持续集成：
- 自动运行linting和类型检查
- 自动运行测试套件
- 生成测试覆盖率报告
- 所有检查必须通过才能合并PR

## 常见问题

### Q: 如何修复格式问题？
A: 运行 `make format` 自动格式化所有文件（包含 import 排序）。

### Q: 如何修复import顺序问题？
A: `make format` 会自动修复 import 排序。

### Q: Mypy报告类型错误怎么办？
A: 添加正确的类型注解。如果是第三方库缺少类型定义，可以在pyproject.toml中配置忽略。

### Q: 测试失败怎么办？
A: 检查错误信息，修复代码或测试。使用 `.venv/bin/pytest -v -s` 查看详细输出。
