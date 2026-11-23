"""Invocation-local admission for optional analysis rewrites."""

from __future__ import annotations

from dataclasses import dataclass

from marivo.analysis.model.optimization_rules import OPTIMIZATION_RULE_IDS, OptimizationRuleId


@dataclass(frozen=True, slots=True)
class OptimizationPolicy:
    """Select optional rewrites without changing mandatory validation."""

    enabled_rules: frozenset[OptimizationRuleId] = OPTIMIZATION_RULE_IDS

    @classmethod
    def default(cls) -> OptimizationPolicy:
        return cls()

    @classmethod
    def disabled(cls) -> OptimizationPolicy:
        return cls(frozenset())

    def allows(self, rule: OptimizationRuleId) -> bool:
        return rule in self.enabled_rules

    def with_rule(self, rule: OptimizationRuleId, *, enabled: bool) -> OptimizationPolicy:
        rules = frozenset((rule,))
        return OptimizationPolicy(
            self.enabled_rules | rules if enabled else self.enabled_rules - rules
        )


DEFAULT_OPTIMIZATION_POLICY = OptimizationPolicy.default()
