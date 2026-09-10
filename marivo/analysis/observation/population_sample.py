"""Source-free sampling payload for exact retained or selected membership."""

from __future__ import annotations

from dataclasses import dataclass

from marivo.analysis.datasets.descriptors import _CORE_TOKEN
from marivo.analysis.datasets.handles import CanonicalValue, _LogicalNodePayload
from marivo.analysis.observation.sampling import EntitySamplingPolicy


@dataclass(frozen=True, slots=True, repr=False, eq=False, kw_only=True)
class PopulationSamplePayload(_LogicalNodePayload, _token=_CORE_TOKEN):
    policy: EntitySamplingPolicy
    target_population_definition_fingerprint: str

    @property
    def identity_payload(self) -> CanonicalValue:
        return (self.policy.identity_payload, self.target_population_definition_fingerprint)
