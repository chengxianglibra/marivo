# Semantic Layer Typed Binding Contract -- SUPERSEDED

> **本文档已被取代，不应作为新设计或实现的依据。**

本文档描述的 typed binding contract（carrier/field/time bindings、BindingTarget、CarrierBinding、FieldSurface、TimeSurface 等）已在 dataset-native grounding 重写中删除。

**替代方案：** OSI Dataset 与 Field 直接承载物理落地。`Dataset.source` 指定数据源本地关系 FQN，`Field.expression` 指定物理列名或计算 SQL 表达式，`Dataset.custom_extensions[].data.datasource_id` 选择数据源。不存在独立的持久化物理绑定层。

**关键变更：** 物理接地从独立 binding 层内联到 Dataset/Field 对象本身。所有 carrier、surface、binding import、join relation 概念已删除。跨数据集连接由 OSI Relationship 对象表达。

**权威参考：** `docs/superpowers/specs/2026-04-30-osi-alignment-v2-design.md` 及更新后的 `docs/specs/semantic/overview.md`。

**历史说明：** 本文档保留用于可追溯性。原始内容可从 git 历史中本通知提交之前的版本恢复。
