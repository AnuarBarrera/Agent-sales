"""Compiler de propuestas de política de encaje (spec §3.1).

Sólo sugiere criterios a partir del contexto comercial aprobado. Nunca aprueba
automáticamente ni modifica el digest del contexto 4.0.
"""

from __future__ import annotations

from app.modules.lead_fit.contracts import (
    CriterionEvaluator,
    CriterionKind,
    CriterionSpec,
    FitPolicyContent,
)


def _criterion_id(prefix: str, index: int) -> str:
    return f"{prefix}_{index}"


def compile_policy_proposal(
    *,
    icp_industries: tuple[str, ...],
    icp_geographies: tuple[str, ...],
    buyer_roles: tuple[str, ...],
    desired_traits: tuple[str, ...],
    exclusions: tuple[str, ...],
    communication_channels: tuple[str, ...],
    contract_version: str = "fit-policy-1.0",
    accept_score: int = 70,
    min_coverage: int = 60,
) -> FitPolicyContent:
    """
    Construye una propuesta de política a partir del contexto comercial.

    Industrias y geografías orientan el descubrimiento; no generan condiciones
    de encaje implícitas. buyer_roles y desired_traits son preferencias. Las exclusiones
    se marcan como exclusiones con must_resolve=false. allowed_channels se usa
    para readiness de contacto, no como criterio de encaje.
    """
    criteria: list[CriterionSpec] = []
    index = 1

    # Preference: buyer roles
    if buyer_roles:
        criteria.append(
            CriterionSpec(
                id=_criterion_id("buyer_role", index),
                kind=CriterionKind.PREFERENCE,
                description=f"El comprador objetivo tiene uno de estos roles: {', '.join(buyer_roles)}.",
                source_pointer="commercial_context.icp.buyer_roles",
                weight=40,
                evaluator=CriterionEvaluator.SEMANTIC,
                must_resolve=False,
            )
        )
        index += 1

    # Preference: desired traits
    if desired_traits:
        criteria.append(
            CriterionSpec(
                id=_criterion_id("trait", index),
                kind=CriterionKind.PREFERENCE,
                description=f"El negocio muestra uno de estos rasgos deseados: {', '.join(desired_traits)}.",
                source_pointer="commercial_context.icp.desired_traits",
                weight=20,
                evaluator=CriterionEvaluator.SEMANTIC,
                must_resolve=False,
            )
        )
        index += 1

    # Exclusions
    for exclusion in exclusions:
        criteria.append(
            CriterionSpec(
                id=_criterion_id("exclusion", index),
                kind=CriterionKind.EXCLUSION,
                description=f"El negocio es: {exclusion}.",
                source_pointer="commercial_context.icp.exclusions",
                weight=0,
                evaluator=CriterionEvaluator.SEMANTIC,
                must_resolve=False,
            )
        )
        index += 1

    if not any(item.kind != CriterionKind.EXCLUSION for item in criteria):
        # Sin criterios positivos: política vacía, el usuario debe revisar.
        criteria.append(
            CriterionSpec(
                id="pending_review",
                kind=CriterionKind.PREFERENCE,
                description="Sin condiciones de encaje definidas; revisar política comercial.",
                source_pointer="reviewed_justification: Definir una condición comercial observable antes de aprobar.",
                weight=1,
                evaluator=CriterionEvaluator.SEMANTIC,
                must_resolve=False,
            )
        )

    return FitPolicyContent(
        contract_version=contract_version,
        criteria=tuple(criteria),
        accept_score=accept_score,
        min_coverage=min_coverage,
        choice_confidence_floor=0.80,
        entity_confidence_floor=0.90,
        max_evidence_age_hours=720,
    )
