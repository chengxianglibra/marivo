# Confidence Calibration Baseline

## What calibration means in Factum

`score_confidence()` in `app/evidence_engine/scoring.py` produces a deterministic score
in [0, 1] from five components:

| Component | Weight / Role |
|---|---|
| `effect_strength` | Magnitude of `delta_pct` relative to baseline |
| `consistency` | Fraction of supporting vs contradicting observations |
| `sample_score` | Log-scaled sample size adequacy |
| `data_quality_score` | Freshness + sample-size-ok flags from observation quality |
| `contradiction_penalty` | Subtracted when contradicting observations exist |

Calibration asks: *does `confidence = 0.7` correlate with ~70% claim correctness in practice?*
Currently the score is an uncalibrated heuristic; the components and weights were chosen
by engineering judgement, not empirical measurement.

## Causal checker confidence boosts (currently uncalibrated)

When `IncrementalSynthesizer` applies causal checkers, each successful upgrade adds a
small boost capped at 0.99:

| Checker | Boost |
|---|---|
| `CrossSliceConsistencyChecker` (L0→L1) | +0.02 |
| `TemporalPrecedenceChecker` (L1→L2) | +0.03 |
| `DoseResponseChecker` (L1 bonus) | +0.02 |
| `ReversalChecker` (L2 bonus) | +0.02 |

These values are heuristic placeholders. They should be validated empirically before
being used to gate agent decisions.

## Baseline collection approach

### Data required

Run `metric_query` on datasets where the ground truth is known:
- A/B test results where the winning variant is established
- Post-incident root-cause reports with expert consensus
- Manually labeled metric anomalies

For each such session, after `synthesize_findings`, record:

```json
{
  "claim_id": "claim_...",
  "confidence": 0.72,
  "inference_level": "L1",
  "ground_truth_correct": true
}
```

### Reliability diagram

Group claims into confidence buckets and compute accuracy per bucket:

| Bucket | Predicted confidence | Observed accuracy | Calibration error |
|--------|---------------------|-------------------|-------------------|
| 0.0–0.2 | mean(confidence in bucket) | fraction correct | \|predicted − actual\| |
| 0.2–0.4 | … | … | … |
| 0.4–0.6 | … | … | … |
| 0.6–0.8 | … | … | … |
| 0.8–1.0 | … | … | … |

A perfectly calibrated model has `predicted ≈ actual` in every bucket.
Expected Calibration Error (ECE) = weighted mean of per-bucket errors.

### Minimum dataset size

At least 50 labeled claims per inference level (L0, L1, L2) for a reliable reliability
diagram. L3–L5 require experimental data and can be deferred until the experimental
confirmation workflow is in place.

## Calibration API stub (M-05 deferred)

`app/evidence_engine/calibration.py` is reserved for the calibration implementation.
The intended interface:

```python
class ConfidenceCalibrator:
    def fit(self, raw_scores: list[float], labels: list[bool]) -> None:
        """Fit isotonic regression on labeled data."""

    def calibrate(self, raw_score: float) -> float | None:
        """Return calibrated confidence, or None if insufficient data."""
```

Until M-05 is implemented, `calibrated_confidence` is always `null` in API responses.
The `raw_score` returned by `score_confidence()` is what agents and the UI currently use.

## Immediate next steps

1. Define a `calibration_dataset` test fixture with 10–20 synthetic claim records
   that have known ground truth (for regression testing the calibration pipeline itself).
2. Collect expert judgments on the Sprint 8 integration test sessions as a small
   pilot dataset (target: ≥10 labeled claims across L0/L1/L2).
3. Once ≥50 labeled claims are available per level, fit isotonic regression and
   replace the `calibrate()` stub with a real implementation.
4. Re-evaluate causal checker confidence boosts against the calibrated scores.
