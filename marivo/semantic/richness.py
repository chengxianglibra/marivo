"""Demand-driven semantic richness report (advisory; never blocks)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from marivo.semantic.ir import DimensionIR
    from marivo.semantic.reader import SemanticProject
    from marivo.semantic.validator import Registry


@dataclass(frozen=True)
class DemandSignal:
    example_questions: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()
    run_history_refs: tuple[str, ...] = ()
    build_purpose: str | None = None


@dataclass(frozen=True)
class RichnessGap:
    kind: Literal["coverage", "depth"]
    subkind: str
    refs: tuple[str, ...]
    demand_weight: float
    demand_evidence: tuple[str, ...]
    suggested_action: str

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "subkind": self.subkind,
            "refs": list(self.refs),
            "demand_weight": self.demand_weight,
            "demand_evidence": list(self.demand_evidence),
            "suggested_action": self.suggested_action,
        }


@dataclass(frozen=True)
class RichnessReport:
    gaps: tuple[RichnessGap, ...]
    checked_at: str

    def __repr__(self) -> str:
        return f"<RichnessReport gaps={len(self.gaps)}; call .show() to inspect>"

    def render(self) -> str:
        """Return bounded plain-text inspection card without a trailing newline."""
        lines: list[str] = [f"RichnessReport gaps={len(self.gaps)}"]
        if self.gaps:
            for gap in self.gaps[:5]:
                lines.append(f"  - {gap.kind}: {', '.join(gap.refs)}")
            if len(self.gaps) > 5:
                lines.append(f"  ... {len(self.gaps) - 5} more; call .to_dict() for full list")
        else:
            lines.append("  (no gaps found)")
        lines.append(f"checked_at: {self.checked_at}")
        lines.append("available:")
        for entry in (".render()", ".to_dict()"):
            lines.append(f"- {entry}")
        return "\n".join(lines)

    def show(self) -> None:
        """Print render() output followed by a trailing newline and return None."""
        print(self.render())

    def to_dict(self) -> dict[str, object]:
        return {
            "gaps": [gap.to_dict() for gap in self.gaps],
            "checked_at": self.checked_at,
        }


@dataclass(frozen=True)
class RichnessSummary:
    gaps: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {"gaps": list(self.gaps)}


def _checked_at() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _detect_depth(reg: Registry) -> list[tuple[str, tuple[str, ...]]]:
    gaps: list[tuple[str, tuple[str, ...]]] = []
    objects = list(reg.datasets.values()) + list(reg.fields.values()) + list(reg.metrics.values())
    for obj in objects:
        ai = obj.ai_context
        ref = (obj.semantic_id,)
        if not (ai.business_definition and ai.business_definition.strip()):
            gaps.append(("missing_business_definition", ref))
        if not ai.guardrails:
            gaps.append(("missing_guardrails", ref))
        if not ai.synonyms:
            gaps.append(("missing_synonyms", ref))
        if not ai.examples:
            gaps.append(("missing_examples", ref))
    return gaps


def _detect_coverage(reg: Registry) -> list[tuple[str, tuple[str, ...]]]:
    gaps: list[tuple[str, tuple[str, ...]]] = []

    metric_datasets: set[str] = set()
    for metric in reg.metrics.values():
        metric_datasets.update(metric.entities)

    fields_by_dataset: dict[str, list[DimensionIR]] = {}
    for field_obj in reg.fields.values():
        fields_by_dataset.setdefault(field_obj.entity, []).append(field_obj)

    for dataset in reg.datasets.values():
        if dataset.semantic_id in metric_datasets:
            continue
        primary_key = set(dataset.primary_key)
        has_measure_like = any(
            (not field_obj.is_time_dimension) and (field_obj.name not in primary_key)
            for field_obj in fields_by_dataset.get(dataset.semantic_id, [])
        )
        if has_measure_like:
            gaps.append(("fact_table_no_metric", (dataset.semantic_id,)))

    related_pairs = {
        frozenset((rel.from_entity, rel.to_entity)) for rel in reg.relationships.values()
    }
    datasets = list(reg.datasets.values())
    for i in range(len(datasets)):
        for j in range(i + 1, len(datasets)):
            left, right = datasets[i], datasets[j]
            if not (set(left.primary_key) & set(right.primary_key)):
                continue
            if frozenset((left.semantic_id, right.semantic_id)) in related_pairs:
                continue
            refs = tuple(sorted((left.semantic_id, right.semantic_id)))
            gaps.append(("dataset_shares_keys_no_relationship", refs))

    return gaps


_W_HISTORY = 3.0
_W_EXAMPLE = 1.0
_W_INTENT = 1.0
_W_PURPOSE = 0.5


def _gap_terms(
    refs: tuple[str, ...],
    objects: Mapping[str, object],
    fields_by_dataset: Mapping[str, Sequence[DimensionIR]],
) -> set[str]:
    terms: set[str] = set()
    for ref in refs:
        leaf = ref.rsplit(".", 1)[-1]
        if leaf:
            terms.add(leaf.lower())
        obj = objects.get(ref)
        if obj is not None:
            name = getattr(obj, "name", None)
            if name:
                terms.add(str(name).lower())
            ai = getattr(obj, "ai_context", None)
            for synonym in getattr(ai, "synonyms", ()) or ():
                terms.add(str(synonym).lower())
            for example in getattr(ai, "examples", ()) or ():
                terms.add(str(example).lower())
        for field_obj in fields_by_dataset.get(ref, ()):
            terms.add(field_obj.name.lower())
            for synonym in field_obj.ai_context.synonyms:
                terms.add(str(synonym).lower())
            for example in field_obj.ai_context.examples:
                terms.add(str(example).lower())
    terms.discard("")
    return terms


def _mentions(text: str, terms: set[str]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def _demand_weight(
    refs: tuple[str, ...],
    terms: set[str],
    demand: DemandSignal | None,
) -> tuple[float, tuple[str, ...]]:
    if demand is None:
        return 0.0, ()
    weight = 0.0
    evidence: list[str] = []
    history = set(demand.run_history_refs)
    for ref in refs:
        if ref in history:
            weight += _W_HISTORY
            evidence.append(f"run_history:{ref}")
    for question in demand.example_questions:
        if _mentions(question, terms):
            weight += _W_EXAMPLE
            evidence.append(f"example:{question}")
    for intent in demand.intents:
        if _mentions(intent, terms):
            weight += _W_INTENT
            evidence.append(f"intent:{intent}")
    if demand.build_purpose and _mentions(demand.build_purpose, terms):
        weight += _W_PURPOSE
        evidence.append(f"build_purpose:{demand.build_purpose}")
    return weight, tuple(evidence)


_SUGGESTED_ACTION = {
    "fact_table_no_metric": "Declare a metric over this dataset or confirm it is dimension-only.",
    "dataset_shares_keys_no_relationship": "Declare a relationship between these datasets or confirm they are independent.",
    "missing_business_definition": "Add ai_context.business_definition for reuse and intent matching.",
    "missing_guardrails": "Add ai_context.guardrails to record usage constraints.",
    "missing_synonyms": "Add ai_context.synonyms for natural-language matching.",
    "missing_examples": "Add ai_context.examples (sample questions) to seed demand.",
}


def build_richness_report(
    project: SemanticProject,
    *,
    demand: DemandSignal | None = None,
) -> RichnessReport:
    reg = project._registry
    if reg is None:
        return RichnessReport(gaps=(), checked_at=_checked_at())

    objects: dict[str, object] = {**reg.datasets, **reg.fields, **reg.metrics}
    fields_by_dataset: dict[str, list[DimensionIR]] = {}
    for field_obj in reg.fields.values():
        fields_by_dataset.setdefault(field_obj.entity, []).append(field_obj)

    gaps: list[RichnessGap] = []

    for subkind, refs in _detect_coverage(reg):
        terms = _gap_terms(refs, objects, fields_by_dataset)
        weight, evidence = _demand_weight(refs, terms, demand)
        if demand is not None and weight == 0.0:
            continue
        gaps.append(
            RichnessGap(
                kind="coverage",
                subkind=subkind,
                refs=refs,
                demand_weight=weight,
                demand_evidence=evidence,
                suggested_action=_SUGGESTED_ACTION[subkind],
            )
        )

    for subkind, refs in _detect_depth(reg):
        terms = _gap_terms(refs, objects, fields_by_dataset)
        weight, evidence = _demand_weight(refs, terms, demand)
        gaps.append(
            RichnessGap(
                kind="depth",
                subkind=subkind,
                refs=refs,
                demand_weight=weight,
                demand_evidence=evidence,
                suggested_action=_SUGGESTED_ACTION[subkind],
            )
        )

    gaps.sort(key=lambda gap: (-gap.demand_weight, gap.kind, gap.subkind, gap.refs))
    return RichnessReport(gaps=tuple(gaps), checked_at=_checked_at())
