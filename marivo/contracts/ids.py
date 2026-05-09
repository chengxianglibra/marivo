from __future__ import annotations

from typing import NewType

# Session domain
SessionId = NewType("SessionId", str)
StepId = NewType("StepId", str)
ArtifactId = NewType("ArtifactId", str)
AttemptId = NewType("AttemptId", str)

# Evidence domain
FindingId = NewType("FindingId", str)
PropositionId = NewType("PropositionId", str)
AssessmentId = NewType("AssessmentId", str)
ActionProposalId = NewType("ActionProposalId", str)
GapId = NewType("GapId", str)
InferenceRecordId = NewType("InferenceRecordId", str)

# Semantic domain
ModelId = NewType("ModelId", int)
RevisionId = NewType("RevisionId", str)
DatasetName = NewType("DatasetName", str)
MetricName = NewType("MetricName", str)
RelationshipName = NewType("RelationshipName", str)

# Infrastructure
DatasourceId = NewType("DatasourceId", str)
EngineId = NewType("EngineId", str)
RouteId = NewType("RouteId", str)

# Auth domain
UserId = NewType("UserId", str)
Action = NewType("Action", str)
ResourceId = NewType("ResourceId", str)

# Evidence referencing
EvidenceRef = NewType("EvidenceRef", str)
CacheKey = NewType("CacheKey", str)
