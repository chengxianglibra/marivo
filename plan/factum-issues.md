# Factum 服务问题清单

> 调查时间: 2026-03-25
> 调查场景: 分析 sys_titan 用户在 oneservice 集群的排队时间问题

---

## 严重问题 (P0)

### 1. aggregate_query 步骤无法查询 Iceberg 分区表

**现象:**
- `aggregate_query` 步骤查询 `iceberg.iceberg_inf.ods_trino_query_info` 表时被 Trino 拒绝
- 错误信息: `QUERY_REJECTED - Filter required on iceberg_inf.ods_trino_query_info for at least one partition column: log_date, log_hour`
- 即使在 `filter` 参数中明确指定了 `log_date = '20260325'` 和 `log_hour BETWEEN '00' AND '13'`，仍然被拒绝

**影响:**
- 核心分析步骤 `aggregate_query` 完全无法用于 Iceberg 分区表
- 用户无法执行聚合分析，只能依赖 `sample_rows` 进行有限的采样分析

**根因分析:**
- presto-gateway 要求 Iceberg 表查询必须包含分区列过滤
- `aggregate_query` 生成的 SQL 可能在 WHERE 子句传递方式上存在问题
- Trino 引擎配置要求同时过滤 `log_date` 和 `log_hour`

**复现步骤:**
```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{
    "table_name": "ods_trino_query_info",
    "select": ["user", "COUNT(*) as cnt"],
    "group_by": ["user"],
    "filter": "log_date = '\''20260325'\'' AND log_hour BETWEEN '\''00'\'' AND '\''13'\''"
  }' "$FACTUM/sessions/$SESSION/steps/aggregate_query"
# 返回: QUERY_REJECTED
```

**对比:** `sample_rows` 步骤可以正常工作，说明问题出在 `aggregate_query` 的 SQL 生成逻辑。

---

## 高优先级问题 (P1)

### 2. 步骤类型间行为不一致

**现象:**
- `sample_rows` 步骤可以正常查询分区表，会话约束也能正确应用
- `aggregate_query` 步骤在相同条件下被拒绝
- `profile_table` 步骤能工作但明确标注"session filters are not applied"

**影响:**
- 用户在不同步骤类型间体验到不一致的行为
- 增加了使用复杂度，需要根据步骤类型调整查询策略

**根因分析:**
- 不同步骤类型可能有不同的 SQL 生成器和过滤条件注入逻辑
- 路由解析可能在不同步骤类型间存在差异

---

### 3. 会话约束在部分步骤中被跳过

**现象:**
- `profile_table` 返回结果显示 `"skipped": ["constraint: user = 'sys_titan'", "raw_filter: cluster = 'k8soneservice-oneservice'"]`
- `sample_rows` 返回结果显示 `"applied": ["constraint: user = 'sys_titan'", "raw_filter: cluster = 'k8soneservice-oneservice'"]`

**影响:**
- 用户设置的会话级约束在某些步骤中被忽略
- 可能导致分析结果不符合预期范围

---

## 中等优先级问题 (P2)

### 4. profile_table 步骤不做分区过滤

**现象:**
- `profile_table` 步骤扫描全表，返回 `row_count: 53992781`
- 不会应用会话约束或分区过滤
- 返回说明: `profile_table scans the full table; session filters are not applied`

**影响:**
- 在生产环境大数据量表上可能导致性能问题
- 无法针对特定分区进行表分析

**建议:** 添加可选的分区过滤参数，或在文档中明确说明限制。

---

### 5. 数据路由配置需优化

**现象:**
- 本地 DuckDB 数据库没有表数据（`SHOW TABLES` 返回空）
- 所有查询都被路由到远程 Trino 引擎
- 没有本地缓存或回退机制

**影响:**
- 依赖外部 Trino 集群可用性
- 增加了查询延迟

---

## 低优先级问题 (P3)

### 6. Trino 引擎特定约束文档化不足

**现象:**
- 生产环境 Trino 有特殊约束：
  - 必须有分区过滤 (`log_date`, `log_hour`)
  - presto-gateway 不支持 `EXECUTE IMMEDIATE`
  - 特定的 user/source/client-tags 要求
- 这些约束在系统层面没有统一处理

**建议:** 在引擎配置或步骤执行层自动注入必要的分区过滤条件。

---

### 7. 错误信息可读性差

**现象:**
- Trino 错误直接透传，如 `QUERY_REJECTED` 包含原始 Trino 错误格式
- 没有将 Trino 特定错误转换为用户友好的提示

**建议:** 添加错误信息转换层，将 Trino 约束错误转换为可操作的建议。

---

## 问题统计

| 严重级别 | 数量 | 影响范围 |
|---------|------|---------|
| P0 (严重) | 1 | 核心功能不可用 |
| P1 (高) | 2 | 功能受限、行为不一致 |
| P2 (中) | 2 | 性能、配置问题 |
| P3 (低) | 2 | 用户体验问题 |

---

## 建议修复顺序

1. **P0-1**: 修复 `aggregate_query` 的分区过滤问题（最高优先级）
2. **P1-2**: 统一步骤类型的约束注入行为
3. **P1-3**: 确保会话约束在所有适用步骤中正确应用
4. **P2-4**: 为 `profile_table` 添加分区过滤支持
5. **P3-6**: 在引擎层自动处理 Trino 分区约束