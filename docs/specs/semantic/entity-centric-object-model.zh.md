# Marivo Entity-Centric Semantic Object Model -- SUPERSEDED

> **本文档已被取代，不应作为新设计或实现的依据。**

本文档描述的 entity-centric 对象模型（以 entity 作为唯一 physical grounding 单元，通过 typed binding 落地）已在 dataset-native grounding 重写中被 OSI 对齐模型取代。

**替代方案：** OSI `Dataset` 直接包含 `source`（关系 FQN）和 `fields[]`（物理列/表达式），物理接地是内联的，不需要独立的 binding 层。`Relationship` 对象表达跨数据集连接。

**关键变更：** Entity 被 OSI Dataset 取代；entity.field 被 OSI Field 取代；独立的 typed binding contract 被删除；entity relationship 被 OSI Relationship 取代；compatibility profile 被删除。

**权威参考：** `docs/superpowers/specs/2026-04-30-osi-alignment-v2-design.md` 及更新后的 `docs/specs/semantic/overview.md`。

**历史说明：** 本文档保留用于可追溯性。原始内容可从 git 历史中本通知提交之前的版本恢复。
