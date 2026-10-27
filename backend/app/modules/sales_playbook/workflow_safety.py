"""Deterministic semantic checks for executable workflow transitions.

These checks preserve extracted transitions as review data and never repair
or select a commercial interpretation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.modules.sales_playbook.schemas import WorkflowConditionCode as C
from app.modules.sales_playbook.schemas import WorkflowStageCode as S

PHASE = {
    S.SOURCING: 0, S.ENRICHMENT: 0, S.CLASSIFICATION: 0,
    S.TOUCH_1: 1, S.TOUCH_1_5: 1, S.BRANCH_IDENTIFICATION: 1,
    S.TOUCH_2: 1, S.TOUCH_3: 1,
    S.PRE_CLOSE_CALL: 2, S.EXPLORATION_CALL: 2,
    S.COMPLETE_ANALYSIS: 3, S.PROPOSAL_CLOSING: 3, S.FOLLOW_UP_GRACEFUL_CLOSE: 3,
}
ORDER = {stage: index for index, stage in enumerate(S)}
PRE_CONTACT_RESPONSE_CONDITIONS = {
    C.BOT_RESPONSE, C.HUMAN_FIRST, C.NEGATIVE_RESPONSE, C.CLEAR_INTEREST, C.NO_RESPONSE,
}
_STAGE_ALIASES = {
    S.SOURCING: ("sourcing", "lead collection", "obtención de leads", "captación de leads"),
    S.ENRICHMENT: ("enrichment", "enriquecimiento", "completar información"),
    S.CLASSIFICATION: ("classification", "clasificación", "priorización"),
    S.TOUCH_1: ("touch 1", "toque 1", "primer contacto"),
    S.TOUCH_1_5: ("touch 1.5", "toque 1.5"),
    S.BRANCH_IDENTIFICATION: ("branch identification", "identificar la rama"),
    S.TOUCH_2: ("touch 2", "toque 2", "segundo mensaje"),
    S.TOUCH_3: ("touch 3", "toque 3", "tercer mensaje"),
    S.PRE_CLOSE_CALL: ("pre close call", "llamada pre cierre", "llamada de rescate"),
    S.EXPLORATION_CALL: ("exploration call", "llamada de exploración"),
    S.COMPLETE_ANALYSIS: ("complete analysis", "análisis completo"),
    S.PROPOSAL_CLOSING: ("proposal closing", "propuesta y cierre"),
    S.FOLLOW_UP_GRACEFUL_CLOSE: ("follow up graceful close", "seguimiento", "cierre respetuoso"),
}
_CONDITION_ALIASES = {
    C.NO_FIT: ("no fit", "sin encaje", "no hay encaje"),
    C.FIT_CONFIRMED: ("fit confirmed", "encaje confirmado", "hay encaje"),
    C.NO_RESPONSE: ("no response", "sin respuesta", "no responde"),
    C.NEGATIVE_RESPONSE: ("negative response", "respuesta negativa", "rechaza"),
    C.BOT_RESPONSE: ("bot response", "responde un bot", "asistente responde"),
    C.HUMAN_FIRST: ("human first", "persona responde primero", "responde directamente una persona"),
}


@dataclass(frozen=True)
class WorkflowSemanticFinding:
    identifier: str
    severity: str
    message: str
    from_stage: str
    condition: str
    to_stage: str


def _field(item: Any, name: str) -> Any:
    value = item.get(name) if isinstance(item, dict) else getattr(item, name, None)
    return getattr(value, "value", value)


def _source_ids(transition: Any) -> set[str]:
    provenance = _field(transition, "provenance") or {}
    source_ref = _field(provenance, "source_ref")
    if not isinstance(source_ref, str):
        return set()
    # Broad extraction provenance is diagnostic only, never direct evidence.
    if source_ref in {"R22", "manual", ""} or len(source_ref.split(",")) > 10:
        return set()
    return {item.strip() for item in source_ref.split(",") if item.strip()}


def _assertion_supports_edge(transition: Any, assertions: list[Any]) -> bool:
    refs = _source_ids(transition)
    if not refs:
        return False
    source_code = S(str(_field(transition, "from_stage")))
    condition_code = C(str(_field(transition, "condition")))
    destination_code = S(str(_field(transition, "to_stage")))
    terms = (
        (source_code.value, *_STAGE_ALIASES.get(source_code, ())),
        (condition_code.value, condition_code.value.replace("_", " "), *_CONDITION_ALIASES.get(condition_code, ())),
        (destination_code.value, destination_code.value.replace("_", " "), *_STAGE_ALIASES.get(destination_code, ())),
    )
    for assertion in assertions:
        assertion_ids = set(_field(assertion, "source_section_ids") or ())
        text = " ".join(str(_field(assertion, key) or "") for key in ("assertion_text", "scope_text", "condition_text")).lower()
        if refs & assertion_ids and all(any(re.search(rf"\b{re.escape(term)}\b", text) for term in alternatives) for alternatives in terms):
            return True
    return False


def validate_workflow_semantics(
    transitions: list[Any], assertions: list[Any] | None = None,
    reviewed_finding_ids: set[str] | None = None,
) -> list[WorkflowSemanticFinding]:
    """Return approval blockers for demonstrated semantic phase hazards."""
    findings: list[WorkflowSemanticFinding] = []
    assertions = assertions or []
    for transition in transitions:
        source = str(_field(transition, "from_stage") or "")
        condition = str(_field(transition, "condition") or "")
        destination = str(_field(transition, "to_stage") or "")
        try:
            source_code, condition_code, destination_code = S(source), C(condition), S(destination)
        except ValueError:
            continue
        identifier = None
        message = ""
        if PHASE[source_code] == 0 and condition_code in PRE_CONTACT_RESPONSE_CONDITIONS:
            identifier = f"pre_contact_response_condition:{source}:{condition}:{destination}"
            message = "Una condición basada en respuesta aparece durante la preparación del prospecto, antes del contacto."
        elif source_code == S.TOUCH_1 and destination_code == S.PRE_CLOSE_CALL and condition_code == C.CLEAR_INTEREST:
            identifier = "clear_interest_does_not_authorize_rescue_call"
            message = "El interés claro, por sí solo, no confirma que corresponda una llamada de rescate."
        elif source_code == S.TOUCH_1 and destination_code == S.TOUCH_1_5 and _field(transition, "wait_hours"):
            identifier = "touch_1_5_timing"
            message = "El tiempo de espera aparece antes del paso de continuación inmediata."
        elif source_code == S.FOLLOW_UP_GRACEFUL_CLOSE and destination_code != source_code and not _assertion_supports_edge(transition, assertions):
            identifier = f"terminal_stage_reactivation:{source}:{destination}"
            message = "Hay una ruta para reactivar la venta después del cierre respetuoso sin respaldo documental específico."
        elif ORDER[destination_code] < ORDER[source_code] and not _assertion_supports_edge(transition, assertions):
            identifier = f"unsupported_backward_transition:{source}:{destination}"
            message = "Una ruta vuelve a una etapa anterior sin evidencia documental específica para ese regreso."
        if identifier:
            findings.append(WorkflowSemanticFinding(
                identifier, "approval_blocker", message, source, condition, destination,
            ))
    reviewed_finding_ids = reviewed_finding_ids or set()
    return [item for item in findings if item.identifier not in reviewed_finding_ids]
