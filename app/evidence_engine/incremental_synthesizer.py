"""IncrementalSynthesizer: maintains tentative claims as observations accumulate.

Incremental synthesis runs after every primitive step (compare_metric,
aggregate_query, profile_table, sample_rows).  It creates or updates *tentative*
claims so agents can inspect intermediate conclusions before
``synthesize_findings`` is called.  ``synthesize_findings`` promotes tentative
claims to ``confirmed`` or ``insufficient``.

Design principles:
- All fact extraction is deterministic (no LLMs).
- IncrementalSynthesizer writes directly to the metadata store —
  it is independent of EvidencePipeline.
- Claims are keyed by scope (metric + slice dict).  One tentative claim per
  unique scope per session.
- Contradiction: two observations in the same scope have opposing delta_pct
  signs.  The newer observation is added to contradicting_observations.
"""

from __future__ import annotations

import json
import math
from typing import Any
from uuid import uuid4

from app.evidence_engine.scoring import score_confidence
from app.storage.metadata import MetadataStore


class IncrementalSynthesizer:
    """Maintains tentative claims per session as observations accumulate.

    Called after each primitive step via ``SemanticLayerService.run_step()``.
    """

    def __init__(self, metadata_store: MetadataStore) -> None:
        self._store = metadata_store

    def process(self, session_id: str) -> dict[str, Any]:
        """Process all unattributed observations and update tentative claims.

        Returns a summary:
        ``{claims_created, claims_updated, contradictions_found, causal_upgrades}``.
        """
        observations = self._load_observations(session_id)
        tentative_claims = self._load_tentative_claims(session_id)

        # Build set of all observation IDs already attributed to any tentative claim
        attributed_ids: set[str] = set()
        for claim in tentative_claims:
            attributed_ids.update(claim["supporting_observations"])
            attributed_ids.update(claim["contradicting_observations"])

        new_observations = [o for o in observations if o["observation_id"] not in attributed_ids]

        claims_created = 0
        claims_updated = 0
        contradictions_found = 0

        for obs in new_observations:
            matched = self._find_matching_claim(obs, tentative_claims)
            if matched is None:
                new_claim = self._make_tentative_claim(obs)
                self._insert_claim(session_id, new_claim)
                tentative_claims.append(new_claim)
                claims_created += 1
            else:
                if self._is_contradiction(obs, matched, observations):
                    contradictions_found += 1
                    self._add_contradicting_observation(matched, obs["observation_id"])
                else:
                    self._add_supporting_observation(matched, obs["observation_id"])
                self._recalculate_and_update(matched, observations)
                claims_updated += 1

        # Run causal checkers after all claims have been created/updated
        causal_upgrades = self._run_causal_checkers(session_id, tentative_claims, observations)

        return {
            "claims_created": claims_created,
            "claims_updated": claims_updated,
            "contradictions_found": contradictions_found,
            "causal_upgrades": causal_upgrades,
        }

    # ── Scope + contradiction logic ───────────────────────────────────────────

    @staticmethod
    def _effective_scope_from_subject(subject: dict[str, Any]) -> dict[str, Any]:
        slice_dict = dict(subject.get("slice", {}) or {})
        temporal_dims = {
            str(col) for col in subject.get("temporal_group_by_columns", []) or []
        }
        if temporal_dims:
            slice_dict = {
                key: value
                for key, value in slice_dict.items()
                if key not in temporal_dims
            }
        return {
            "metric": subject.get("metric"),
            "slice": slice_dict,
        }

    @staticmethod
    def _scope_matches(obs_subject: dict[str, Any], claim_scope: dict[str, Any]) -> bool:
        obs_scope = IncrementalSynthesizer._effective_scope_from_subject(obs_subject)
        return (
            obs_scope.get("metric") == claim_scope.get("metric")
            and obs_scope.get("slice", {}) == claim_scope.get("slice", {})
        )

    def _find_matching_claim(
        self, obs: dict[str, Any], tentative_claims: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        obs_subject = obs.get("subject", {})
        for claim in tentative_claims:
            if self._scope_matches(obs_subject, claim.get("scope", {})):
                return claim
        return None

    @staticmethod
    def _is_contradiction(
        new_obs: dict[str, Any],
        claim: dict[str, Any],
        all_observations: list[dict[str, Any]],
    ) -> bool:
        """Return True if new_obs has opposing delta_pct to any supporting observation."""
        new_delta = new_obs.get("payload", {}).get("delta_pct")
        if new_delta is None:
            return False
        obs_map = {o["observation_id"]: o for o in all_observations}
        for sup_id in claim["supporting_observations"]:
            sup_obs = obs_map.get(sup_id)
            if sup_obs is None:
                continue
            sup_delta = sup_obs.get("payload", {}).get("delta_pct")
            if sup_delta is not None and float(new_delta) * float(sup_delta) < 0:
                return True
        return False

    @staticmethod
    def _observation_magnitude(payload: dict[str, Any]) -> float:
        delta_pct = payload.get("delta_pct")
        if delta_pct is not None:
            return abs(float(delta_pct))

        outlier_factor = payload.get("outlier_factor")
        if outlier_factor is not None:
            return max(0.0, abs(float(outlier_factor)) - 1.0)

        z_score = payload.get("z_score")
        if z_score is not None:
            return abs(float(z_score))

        return 0.0

    # ── Claim construction ────────────────────────────────────────────────────

    def _make_tentative_claim(self, obs: dict[str, Any]) -> dict[str, Any]:
        subject = obs.get("subject", {})
        payload = obs.get("payload", {})
        significance = obs.get("significance", {})
        quality = obs.get("quality", {})

        scope = self._effective_scope_from_subject(subject)
        metric = scope.get("metric", "unknown")
        slice_dict = scope.get("slice", {})
        slice_str = (
            ", ".join(f"{k}={v}" for k, v in slice_dict.items())
            if slice_dict
            else "overall"
        )

        delta_pct = payload.get("delta_pct")
        if delta_pct is not None:
            direction = "increased" if float(delta_pct) > 0 else "declined"
            text = f"{metric} {direction} {abs(float(delta_pct)):.1f}% for {slice_str} (tentative)"
        elif obs.get("type") == "anomaly_detection":
            z_score = float(payload.get("z_score", 0.0))
            direction = "spike" if z_score >= 0 else "decline"
            outlier_factor = payload.get("outlier_factor")
            if outlier_factor is not None:
                magnitude = f"{abs(float(outlier_factor)):.1f}x normal"
            else:
                magnitude = f"z={abs(z_score):.1f}"
            text = (
                f"{metric} showed an anomalous {direction} "
                f"({magnitude}) for {slice_str} (tentative)"
            )
        elif obs.get("type") == "causal_candidate":
            cause_id = payload.get("candidate_cause_observation_id", "unknown")
            cause_short = cause_id[:12] if len(cause_id) >= 12 else cause_id
            text = f"{metric} is a candidate effect of observation {cause_short} for {slice_str} (tentative)"
        else:
            # Aggregate observation: build text from payload numeric values
            SKIP_KEYS = {"current_value", "delta_pct"}
            numeric_parts = [
                (k, v)
                for k, v in payload.items()
                if k not in SKIP_KEYS and isinstance(v, (int, float))
            ][:3]  # cap at 3 for readability
            if numeric_parts:
                values_str = ", ".join(
                    f"{k}={v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else f"{k}={v:,}"
                    for k, v in numeric_parts
                )
                text = f"{slice_str}: {values_str} (tentative)"
            else:
                text = f"aggregate snapshot for {slice_str} (tentative)"

        breakdown = self._compute_breakdown(payload, significance, quality, n_contradictions=0)
        confidence = score_confidence(
            float(breakdown["effect_strength"]),
            float(breakdown["consistency"]),
            float(breakdown["sample_score"]),
            float(breakdown["data_quality_score"]),
            float(breakdown["contradiction_penalty"]),
        )

        return {
            "claim_id": f"claim_{uuid4().hex[:12]}",
            "type": "root_cause_candidate",
            "text": text,
            "scope": {"metric": metric, "slice": slice_dict},
            "confidence": confidence,
            "status": "tentative",
            "supporting_observations": [obs["observation_id"]],
            "contradicting_observations": [],
            "confidence_breakdown": breakdown,
            "inference_level": "L0",
            "inference_justification": [],
        }

    @staticmethod
    def _compute_breakdown(
        payload: dict[str, Any],
        significance: dict[str, Any],
        quality: dict[str, Any],
        *,
        n_contradictions: int,
        n_supporting: int = 1,
    ) -> dict[str, Any]:
        magnitude = IncrementalSynthesizer._observation_magnitude(payload)
        effect_strength = min(1.0, magnitude / 20.0)

        sample_size = significance.get("sample_size") or 0
        sample_score = min(1.0, float(sample_size) / 150.0)

        sample_size_ok = quality.get("sample_size_ok", True)
        freshness_ok = quality.get("freshness_ok", True)
        data_quality_score = 0.95 if (sample_size_ok and freshness_ok) else 0.60

        # Consistency improves log-linearly with more supporting observations
        consistency = round(min(1.0, 0.5 + 0.5 * math.log1p(n_supporting) / math.log1p(5)), 3)

        contradiction_penalty = round(min(0.5, n_contradictions * 0.15), 3)

        return {
            "effect_strength": round(effect_strength, 3),
            "consistency": consistency,
            "sample_score": round(sample_score, 3),
            "data_quality_score": data_quality_score,
            "contradiction_penalty": contradiction_penalty,
        }

    # ── In-memory claim mutation helpers ─────────────────────────────────────

    @staticmethod
    def _add_supporting_observation(claim: dict[str, Any], obs_id: str) -> None:
        if obs_id not in claim["supporting_observations"]:
            claim["supporting_observations"].append(obs_id)

    @staticmethod
    def _add_contradicting_observation(claim: dict[str, Any], obs_id: str) -> None:
        if obs_id not in claim["contradicting_observations"]:
            claim["contradicting_observations"].append(obs_id)

    def _recalculate_and_update(
        self, claim: dict[str, Any], all_observations: list[dict[str, Any]]
    ) -> None:
        obs_map = {o["observation_id"]: o for o in all_observations}
        n_supporting = len(claim["supporting_observations"])
        n_contradictions = len(claim["contradicting_observations"])

        primary_obs = obs_map.get(claim["supporting_observations"][0]) if claim["supporting_observations"] else None
        if primary_obs:
            breakdown = self._compute_breakdown(
                primary_obs.get("payload", {}),
                primary_obs.get("significance", {}),
                primary_obs.get("quality", {}),
                n_contradictions=n_contradictions,
                n_supporting=n_supporting,
            )
        else:
            breakdown = dict(claim["confidence_breakdown"])

        confidence = score_confidence(
            float(breakdown["effect_strength"]),
            float(breakdown["consistency"]),
            float(breakdown["sample_score"]),
            float(breakdown["data_quality_score"]),
            float(breakdown["contradiction_penalty"]),
        )
        claim["confidence"] = confidence
        claim["confidence_breakdown"] = breakdown

        self._store.execute(
            """
            UPDATE claims
            SET supporting_observation_ids_json = ?,
                contradicting_observation_ids_json = ?,
                confidence = ?,
                confidence_breakdown_json = ?
            WHERE claim_id = ?
            """,
            [
                json.dumps(claim["supporting_observations"]),
                json.dumps(claim["contradicting_observations"]),
                confidence,
                json.dumps(breakdown),
                claim["claim_id"],
            ],
        )

    # ── Storage helpers ───────────────────────────────────────────────────────

    def _run_causal_checkers(
        self,
        session_id: str,
        tentative_claims: list[dict[str, Any]],
        all_observations: list[dict[str, Any]],
    ) -> int:
        """Run the causal checker chain, persist inference_level upgrades and causal edges.

        Returns the number of claims that received at least one upgrade.
        """
        from uuid import uuid4

        from app.evidence_engine.causal_checkers import get_default_registry
        from app.evidence_engine.schemas import INFERENCE_LEVEL_ORDER

        upgrades = get_default_registry().run_all(tentative_claims, all_observations, edges=[])
        if not upgrades:
            return 0

        # Build a quick lookup of the in-memory claim state
        claim_map = {c["claim_id"]: c for c in tentative_claims}

        applied = 0
        for upgrade in upgrades:
            claim = claim_map.get(upgrade.claim_id)
            if claim is None:
                continue

            current_level = claim.get("inference_level", "L0")
            # Only upgrade, never downgrade
            if (INFERENCE_LEVEL_ORDER.index(upgrade.new_level) >
                    INFERENCE_LEVEL_ORDER.index(current_level)):
                new_level = upgrade.new_level
            else:
                new_level = current_level

            # Union tokens (preserve order, no duplicates)
            existing_tokens: list[str] = claim.get("inference_justification", [])
            new_tokens = list(dict.fromkeys(
                existing_tokens + [t for t in upgrade.justification_tokens
                                   if t not in existing_tokens]
            ))
            new_confidence = min(0.99, claim.get("confidence", 0.0) + upgrade.confidence_boost)

            self._store.execute(
                """
                UPDATE claims
                SET inference_level = ?,
                    inference_justification_json = ?,
                    confidence = ?
                WHERE claim_id = ?
                """,
                [new_level, json.dumps(new_tokens), new_confidence, upgrade.claim_id],
            )

            # Keep in-memory state consistent for subsequent checkers in the same call
            claim["inference_level"] = new_level
            claim["inference_justification"] = new_tokens
            claim["confidence"] = new_confidence

            # Persist causal edges via shared per-claim reconcile helper
            if upgrade.causal_edges:
                from app.evidence_engine.causal_checkers import reconcile_causal_edges
                reconcile_causal_edges(
                    self._store, session_id, upgrade.claim_id, upgrade.causal_edges
                )

            applied += 1

        return applied

    def _load_observations(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._store.query_rows(
            """
            SELECT observation_id, observation_type, subject_json, payload_json,
                   significance_json, quality_json,
                   observed_window_json, temporal_order
            FROM observations
            WHERE session_id = ?
            ORDER BY created_at
            """,
            [session_id],
        )
        result = []
        for row in rows:
            obs: dict[str, Any] = {
                "observation_id": row["observation_id"],
                "type": row["observation_type"],
                "subject": json.loads(row["subject_json"]),
                "payload": json.loads(row["payload_json"]),
                "significance": json.loads(row["significance_json"]),
                "quality": json.loads(row["quality_json"]),
                "temporal_order": row.get("temporal_order") or 0,
            }
            raw_window = row.get("observed_window_json")
            obs["observed_window"] = json.loads(raw_window) if raw_window else None
            result.append(obs)
        return result

    def _load_tentative_claims(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._store.query_rows(
            """
            SELECT claim_id, claim_type, text, scope_json, confidence, status,
                   supporting_observation_ids_json, contradicting_observation_ids_json,
                   confidence_breakdown_json, inference_level, inference_justification_json
            FROM claims
            WHERE session_id = ? AND status = 'tentative'
            ORDER BY created_at
            """,
            [session_id],
        )
        result = []
        for row in rows:
            claim = dict(row)
            claim["type"] = claim.pop("claim_type")
            claim["scope"] = json.loads(claim.pop("scope_json"))
            claim["supporting_observations"] = json.loads(
                claim.pop("supporting_observation_ids_json")
            )
            claim["contradicting_observations"] = json.loads(
                claim.pop("contradicting_observation_ids_json")
            )
            claim["confidence_breakdown"] = json.loads(claim.pop("confidence_breakdown_json"))
            claim["inference_justification"] = json.loads(
                claim.pop("inference_justification_json")
            )
            result.append(claim)
        return result

    def _insert_claim(self, session_id: str, claim: dict[str, Any]) -> None:
        self._store.execute(
            """
            INSERT INTO claims (
                claim_id, session_id, claim_type, text, scope_json, confidence, status,
                supporting_observation_ids_json, contradicting_observation_ids_json,
                confidence_breakdown_json, inference_level, inference_justification_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                claim["claim_id"],
                session_id,
                claim["type"],
                claim["text"],
                json.dumps(claim["scope"]),
                claim["confidence"],
                claim["status"],
                json.dumps(claim["supporting_observations"]),
                json.dumps(claim["contradicting_observations"]),
                json.dumps(claim["confidence_breakdown"]),
                claim.get("inference_level", "L0"),
                json.dumps(claim.get("inference_justification", [])),
            ],
        )
