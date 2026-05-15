# Attribute Intent

状态：current。

`attribute` 的内部 observe 输入不接受 calendar alignment 参数。

如 attribution workflow 需要日期对齐，应在最终 compare 步骤上传递 `compare_type`，由 compare runtime 生成 bucket pairing。
