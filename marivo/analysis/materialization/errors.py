"""Safe failures from the execution boundary."""

from marivo.analysis.model.execution_errors import MaterializationError


def _execution_error(stage: str) -> MaterializationError:
    return MaterializationError(
        expected="a supported and complete registered Dataset execution",
        received="the admitted action could not complete its current phase; remote read status may be unknown",
        repair="Correct the reported source or resource requirement, then explicitly resubmit the analysis.",
        stage=stage,
    )
