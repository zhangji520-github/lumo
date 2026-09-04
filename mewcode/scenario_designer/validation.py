from __future__ import annotations

import re

from mewcode.runtime.models import RuntimeDiagnostic
from mewcode.runtime.scenario_loader import parse_scenario, scenario_to_dict
from mewcode.scenario_designer.models import ScenarioDraft

_SECRET_RE = re.compile(
    r"(?i)(?:api[_-]?key|token|password|authorization)\s*[:=]\s*['\"]?[A-Za-z0-9_-]{12,}"
)


def validate_draft(draft: ScenarioDraft) -> tuple[RuntimeDiagnostic, ...]:
    diagnostics = list(draft.diagnostics)
    try:
        parse_scenario(
            scenario_to_dict(draft.definition),
            expected_id=draft.definition.id,
        )
    except Exception as exc:
        diagnostics.append(RuntimeDiagnostic(
            severity="error",
            code="scenario-schema-invalid",
            message=str(exc),
        ))
    if len(draft.prompt_text.encode("utf-8")) > 20 * 1024:
        diagnostics.append(RuntimeDiagnostic(
            severity="error",
            code="prompt-too-large",
            message="Scenario prompt exceeds the 20 KiB limit",
        ))
    if _SECRET_RE.search(draft.prompt_text):
        diagnostics.append(RuntimeDiagnostic(
            severity="error",
            code="prompt-secret-detected",
            message="Scenario prompt appears to contain a credential",
        ))
    return tuple(diagnostics)

