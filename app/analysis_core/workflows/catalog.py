from __future__ import annotations

from app.analysis_core.composites import CompositeStepTemplate, CompositeWorkflowSpec


WATCH_TIME_DROP_WORKFLOW = CompositeWorkflowSpec(
    name="watch_time_drop",
    description="Default workflow for diagnosing watch-time regression in the MVP domain.",
    steps=[
        CompositeStepTemplate("compare_watch_time"),
        CompositeStepTemplate("analyze_qoe"),
        CompositeStepTemplate("analyze_ads"),
        CompositeStepTemplate("analyze_recommendation"),
        CompositeStepTemplate(
            "synthesize_findings",
            dependencies=[0, 1, 2, 3],
        ),
    ],
)


WORKFLOW_SPECS = {
    WATCH_TIME_DROP_WORKFLOW.name: WATCH_TIME_DROP_WORKFLOW,
}
