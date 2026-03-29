# Factum 工程质量工具快速参考

## 日常开发命令

### 代码格式化
```bash
# 检查格式问题
ruff format --check .

# 自动格式化所有文件
ruff format .
```

### 代码检查
```bash
# 检查所有问题
ruff check .

# 自动修复可修复的问题
ruff check --fix .
```

### 类型检查
```bash
# 检查app目录的类型
mypy app
```

### 测试
```bash
# 运行所有测试（并行）
pytest

# 运行测试并生成覆盖率报告
pytest --cov=app --cov-report=term-missing

# 生成HTML覆盖率报告
pytest --cov=app --cov-report=html
open htmlcov/index.html
```

### Pre-commit
```bash
# 手动运行所有pre-commit检查
pre-commit run --all-files

# 只检查暂存的文件
pre-commit run
```

## 提交前检查清单

- [ ] `ruff format .` - 代码已格式化
- [ ] `ruff check .` - 通过linting检查
- [ ] `mypy app` - 通过类型检查
- [ ] `pytest` - 所有测试通过
- [ ] 更新了相关文档

## 配置文件

- `pyproject.toml` - 所有工具的配置
- `.pre-commit-config.yaml` - Pre-commit hooks配置
- `.github/workflows/ci.yml` - CI/CD配置
- `CONTRIBUTING.md` - 完整的开发规范

## 当前质量指标

- 测试覆盖率: 83.84%
- 类型注解覆盖: 100%
- 测试通过率: 99.9% (942/943)
- 代码格式化: 100%

## 工具版本

- Python: 3.12+
- Ruff: >=0.3.0
- Mypy: >=1.9
- pytest: >=8
- pytest-cov: >=5.0
