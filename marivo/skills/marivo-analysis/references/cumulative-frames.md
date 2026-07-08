# Cumulative Frames

Cumulative MetricFrames contain running-total values anchored to all history. The observe
window clips displayed rows only.

Allowed:
- `show()`
- `contract()`
- `transform.window(...)`
- `correlate`, `discover`, `assess_quality`, `derive`, and `hypothesis_test` with the running-total caveat in mind

Rejected in v1:
- `compare`
- `attribute`
- `decompose`
- `forecast`

Use the base flow metric for rejected intents.
