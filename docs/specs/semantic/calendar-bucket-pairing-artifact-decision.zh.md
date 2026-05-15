# Calendar Bucket Pairing Artifact Decision

状态：current。

Bucket pairing 不作为独立 artifact 暴露，也不再冻结在 observation artifact 中。

Runtime 在执行 `compare` 时根据 `compare_type` 即时生成 pairing，并写入 compare artifact metadata。调用方通过 compare artifact 审计最终对齐结果。
