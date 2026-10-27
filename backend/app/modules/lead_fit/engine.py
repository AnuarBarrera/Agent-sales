"""Motor de decisión determinista de encaje (spec §4).

Sin dependencia de DB, settings ni proveedores. Toda la lógica de agregación,
puntuación y clasificación es pura y verificable.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.modules.lead_fit.contracts import (
    AssessmentResult,
    ContactPoint,
    CriterionAssessment,
    CriterionKind,
    CriterionSpec,
    FitClassification,
    FitDecision,
    FitPolicyContent,
    FitResearchPlan,
    SourceAssessment,
)


def _weight_of(criterion: CriterionSpec) -> int:
    if criterion.kind in (CriterionKind.REQUIRED, CriterionKind.PREFERENCE):
        return criterion.weight
    return 0


def aggregate_assessments(
    policy: FitPolicyContent,
    answers: tuple[SourceAssessment, ...],
) -> tuple[CriterionAssessment, ...]:
    """
    Agrega evaluaciones de Jev por criterio.

    Reglas (spec §4):
    - matches y does_not_match con respaldo/confianza suficientes simultáneos
      => conflicting.
    - No resolver a favor de la mayoría, documento más largo o ausencia.
    - Dos fuentes duplicadas no son dos confirmaciones independientes.
    - Ausencia de respuesta / provider failure / campo inesperado no se
      normalizan a does_not_match.
    """
    by_criterion: dict[str, list[SourceAssessment]] = {}
    for answer in answers:
        if answer.method == "jev" and answer.confidence < policy.choice_confidence_floor:
            answer = answer.model_copy(update={"status": AssessmentResult.NOT_ESTABLISHED})
        by_criterion.setdefault(answer.criterion_id, []).append(answer)

    aggregated: list[CriterionAssessment] = []
    for criterion in policy.criteria:
        criterion_answers = by_criterion.get(criterion.id, [])
        matches = [a for a in criterion_answers if a.status is AssessmentResult.MATCHES]
        does_not_match = [
            a for a in criterion_answers if a.status is AssessmentResult.DOES_NOT_MATCH
        ]
        not_established = [
            a
            for a in criterion_answers
            if a.status is AssessmentResult.NOT_ESTABLISHED
        ]
        conflicting = [
            a for a in criterion_answers if a.status is AssessmentResult.CONFLICTING
        ]

        evidence_ids = tuple(a.evidence_id for a in criterion_answers)

        if conflicting:
            result = AssessmentResult.CONFLICTING
            confidence = max((a.confidence for a in conflicting), default=0.0)
            method = "jev" if conflicting else "code"
            probabilities = conflicting[0].probabilities if conflicting else {}
            reason_code = "criterion_conflicting_sources"
        elif matches and does_not_match:
            # matches y does_not_match simultáneos con respaldo => conflicting.
            result = AssessmentResult.CONFLICTING
            confidence = max(
                [a.confidence for a in matches + does_not_match], default=0.0
            )
            method = "jev"
            probabilities = matches[0].probabilities if matches else {}
            reason_code = "criterion_conflicting_matches_and_mismatches"
        elif matches:
            result = AssessmentResult.MATCHES
            confidence = max((a.confidence for a in matches), default=0.0)
            method = "jev" if matches else "code"
            probabilities = matches[0].probabilities if matches else {}
            reason_code = "criterion_matched"
        elif does_not_match:
            result = AssessmentResult.DOES_NOT_MATCH
            confidence = max(
                (a.confidence for a in does_not_match), default=0.0
            )
            method = "jev" if does_not_match else "code"
            probabilities = does_not_match[0].probabilities if does_not_match else {}
            reason_code = "criterion_does_not_match"
        else:
            result = AssessmentResult.NOT_ESTABLISHED
            confidence = max((a.confidence for a in not_established), default=0.0)
            method = "code"
            probabilities = {}
            reason_code = "criterion_not_established"

        # Confianza alta en not_established no aumenta cobertura.
        aggregated.append(
            CriterionAssessment(
                criterion_id=criterion.id,
                result=result,
                evidence_ids=evidence_ids,
                method=method,
                confidence=confidence,
                probabilities=probabilities,
                reason_code=reason_code,
            )
        )

    return tuple(aggregated)


def classify_fit(
    policy: FitPolicyContent,
    identity_status: str,
    assessments: tuple[CriterionAssessment, ...],
    contacts: tuple[ContactPoint, ...],
    allowed_channels: tuple[str, ...],
) -> FitDecision:
    """
    Clasifica el encaje según el orden de decisión del spec §4.

    Orden:
    1. Identidad sin resolver => needs_more_research.
    2. Una exclusion con matches => not_qualified.
    3. Un required con does_not_match => not_qualified.
    4. Required o must_resolve unknown/conflicting => needs_more_research.
    5. K=0, coverage < min_coverage o cualquier criterio positivo conflicting =>
       needs_more_research. La precedencia de exclusión/required incumplido se
       conserva.
    6. fit_score >= accept_score => qualified.
    7. Si score es menor, calcular max_possible. Si max_possible < accept_score
       => not_qualified; en otro caso needs_more_research.
    """
    assessment_by_id = {
        a.criterion_id: (
            a.model_copy(update={"result": AssessmentResult.NOT_ESTABLISHED})
            if a.method == "jev" and a.confidence < policy.choice_confidence_floor
            else a
        )
        for a in assessments
    }

    required_criteria = [
        c for c in policy.criteria if c.kind is CriterionKind.REQUIRED
    ]
    exclusion_criteria = [
        c for c in policy.criteria if c.kind is CriterionKind.EXCLUSION
    ]

    # 1. Identidad sin resolver => needs_more_research (ninguna evidencia de otro
    # sujeto puede producir rechazo).
    if identity_status != "matched":
        return FitDecision(
            classification=FitClassification.NEEDS_MORE_RESEARCH,
            fit_score=None,
            coverage=0.0,
            reasons=(
                "Identidad del sujeto no resuelta; se requiere más investigación.",
            ),
            missing_criterion_ids=tuple(c.id for c in required_criteria),
            contact_ready=False,
        )

    # 2. Una exclusion con matches => not_qualified.
    exclusion_matches = [
        c
        for c in exclusion_criteria
        if assessment_by_id.get(c.id)
        and assessment_by_id[c.id].result is AssessmentResult.MATCHES
    ]
    if exclusion_matches:
        return FitDecision(
            classification=FitClassification.NOT_QUALIFIED,
            fit_score=None,
            coverage=0.0,
            reasons=tuple(
                f"Exclusión confirmada: {c.description}" for c in exclusion_matches
            ),
            missing_criterion_ids=(),
            contact_ready=False,
        )

    # 3. Un required con does_not_match => not_qualified.
    required_fails = [
        c
        for c in required_criteria
        if assessment_by_id.get(c.id)
        and assessment_by_id[c.id].result is AssessmentResult.DOES_NOT_MATCH
    ]
    if required_fails:
        return FitDecision(
            classification=FitClassification.NOT_QUALIFIED,
            fit_score=None,
            coverage=0.0,
            reasons=tuple(
                f"Requisito no cumplido: {c.description}" for c in required_fails
            ),
            missing_criterion_ids=tuple(c.id for c in required_fails),
            contact_ready=False,
        )

    # 4. Required o must_resolve unknown/conflicting =>
    # needs_more_research. Calcular coverage aunque sea needs_more_research.
    required_unknown = []
    for c in required_criteria:
        a = assessment_by_id.get(c.id)
        if a is None or a.result in (
            AssessmentResult.NOT_ESTABLISHED,
            AssessmentResult.CONFLICTING,
        ):
            required_unknown.append(c)
    must_resolve_unknown = [
        c
        for c in policy.criteria
        if c.must_resolve
        and (
            (a := assessment_by_id.get(c.id)) is None
            or a.result in (AssessmentResult.NOT_ESTABLISHED, AssessmentResult.CONFLICTING)
        )
    ]
    if required_unknown or must_resolve_unknown:
        unknown_ids = tuple(dict.fromkeys(
            c.id for c in (*required_unknown, *must_resolve_unknown)
        ))
        positive_criteria = [
            c
            for c in policy.criteria
            if c.kind in (CriterionKind.REQUIRED, CriterionKind.PREFERENCE)
        ]
        W = sum(_weight_of(c) for c in positive_criteria)
        K = 0
        for c in positive_criteria:
            a = assessment_by_id.get(c.id)
            if a is not None and a.result not in (
                AssessmentResult.NOT_ESTABLISHED,
                AssessmentResult.CONFLICTING,
            ):
                K += _weight_of(c)
        coverage = (100.0 * K / W) if W > 0 else 0.0
        return FitDecision(
            classification=FitClassification.NEEDS_MORE_RESEARCH,
            fit_score=None,
            coverage=coverage,
            reasons=(
                "Condición(es) indispensable(s) sin resolver: "
                + ", ".join(unknown_ids),
            ),
            missing_criterion_ids=unknown_ids,
            contact_ready=False,
        )

    # Cálculo de W, K, M.
    positive_criteria = [
        c
        for c in policy.criteria
        if c.kind in (CriterionKind.REQUIRED, CriterionKind.PREFERENCE)
    ]
    W = sum(_weight_of(c) for c in positive_criteria)

    K = 0
    M = 0
    missing_ids: list[str] = []
    conflicting_positive: list[str] = []
    for c in positive_criteria:
        a = assessment_by_id.get(c.id)
        if a is None or a.result is AssessmentResult.NOT_ESTABLISHED:
            missing_ids.append(c.id)
            continue
        if a.result is AssessmentResult.CONFLICTING:
            conflicting_positive.append(c.id)
            missing_ids.append(c.id)
            continue
        K += _weight_of(c)
        if a.result is AssessmentResult.MATCHES:
            M += _weight_of(c)

    # 5. K=0, coverage < min_coverage o cualquier criterio positivo conflicting =>
    # needs_more_research.
    coverage = (100.0 * K / W) if W > 0 else 0.0
    if K == 0 or coverage < float(policy.min_coverage) or conflicting_positive:
        fit_score = int(100 * M / K) if K > 0 else None
        return FitDecision(
            classification=FitClassification.NEEDS_MORE_RESEARCH,
            fit_score=fit_score,
            coverage=coverage,
            reasons=(
                "Cobertura insuficiente o criterios en conflicto; se requieren más datos.",
            ),
            missing_criterion_ids=tuple(missing_ids),
            contact_ready=_contact_ready(contacts, allowed_channels),
        )

    fit_score = int(100 * M / K) if K > 0 else None
    if fit_score is not None and fit_score >= float(policy.accept_score):
        return FitDecision(
            classification=FitClassification.QUALIFIED,
            fit_score=fit_score,
            coverage=coverage,
            reasons=("Encaje suficiente según la política de prospección.",),
            missing_criterion_ids=(),
            contact_ready=_contact_ready(contacts, allowed_channels),
        )

    # 7. Máximo alcanzable si todos los pesos desconocidos cumplieran.
    max_possible = 100 * (M + (W - K)) / W if W > 0 else 0.0
    if max_possible < float(policy.accept_score):
        return FitDecision(
            classification=FitClassification.NOT_QUALIFIED,
            fit_score=fit_score,
            coverage=coverage,
            reasons=(
                "Incluso con todos los datos pendientes cumplidos, el score no alcanza el umbral de aceptación.",
            ),
            missing_criterion_ids=tuple(missing_ids),
            contact_ready=_contact_ready(contacts, allowed_channels),
        )

    return FitDecision(
        classification=FitClassification.NEEDS_MORE_RESEARCH,
        fit_score=fit_score,
        coverage=coverage,
        reasons=(
            "Score por debajo del umbral de aceptación pero los datos pendientes podrían cambiarlo.",
        ),
        missing_criterion_ids=tuple(missing_ids),
        contact_ready=_contact_ready(contacts, allowed_channels),
    )


def _contact_ready(
    contacts: tuple[ContactPoint, ...], allowed_channels: tuple[str, ...]
) -> bool:
    """contact_ready es independiente: usa intersección con canales permitidos."""
    allowed = set(allowed_channels)
    if not allowed:
        return False
    for contact in contacts:
        if (
            contact.subject_status == "matched"
            and contact.valid_format
            and contact.channel in allowed
        ):
            return True
    return False


def build_research_plan(
    *,
    lead_id,
    evaluation_id,
    policy_id,
    source_digest: str,
    missing_criterion_ids: tuple[str, ...],
    query: str,
    source_strategy: str = "maps",
) -> FitResearchPlan:
    """Construye un plan de investigación incremental (T07)."""
    from datetime import UTC, datetime

    from app.modules.lead_fit.contracts import FitResearchPlan

    return FitResearchPlan(
        lead_id=lead_id,
        evaluation_id=evaluation_id,
        policy_id=policy_id,
        source_digest=source_digest,
        purpose="fit",
        missing_criterion_ids=missing_criterion_ids,
        query=query,
        source_strategy=source_strategy,
        max_search=1,
        max_extract=1,
        max_jev=2,
        max_rounds=1,
        monetary_cap_usd=None,
        expires_at=datetime.now(UTC),
    )


def evaluate_fixture(
    policy: FitPolicyContent,
    identity_status: str,
    assessments: Iterable[CriterionAssessment],
    contacts: tuple[ContactPoint, ...] = (),
    allowed_channels: tuple[str, ...] = (),
) -> FitDecision:
    """Helper solo para tests (no método productivo en clases)."""
    return classify_fit(
        policy,
        identity_status,
        tuple(assessments),
        contacts,
        allowed_channels,
    )
