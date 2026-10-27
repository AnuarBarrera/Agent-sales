"""Deterministic, server-owned first question for an unresolved opportunity."""

from app.modules.setter.context import ApprovedCommercialSetterContext, SetterContext
from app.modules.setter.schemas import SetterDecisionAction, SetterModelOutput

DISCOVERY_TEMPLATE_VERSION = "discovery-template-1.0"
DISCOVERY_CTA = "¿Tiene sentido que les comparta una idea breve?"


def build_discovery_output(context: SetterContext) -> SetterModelOutput:
    if context.draft_purpose != "discovery":
        raise ValueError("Discovery output requires server-authorized discovery purpose")
    if not isinstance(context.approved_context, ApprovedCommercialSetterContext):
        raise ValueError("Discovery requires approved commercial context v4")
    seller = context.approved_context.commercial_context
    seller_name = seller.business_name.strip()
    offer_summary = (seller.offer.summary or "").strip()
    if not seller_name or not offer_summary:
        raise ValueError("Approved seller name and offer summary are required")
    channel = next(
        (value for value in ("email", "phone") if value in context.approved_context.authorized_channels),
        None,
    )
    if channel == "phone":
        message = (
            f"Hola, soy de {seller_name}. Le llamo porque ofrecemos {offer_summary}. "
            "¿Cómo gestionan actualmente ese tema en su negocio?"
        )
        cta = "¿Tiene un minuto para conversar?"
    elif channel == "email":
        message = (
            f"Hola, somos {seller_name}. Nuestro servicio es: {offer_summary}. "
            "¿Cómo gestionan actualmente ese tema en su negocio?"
        )
        cta = DISCOVERY_CTA
    else:
        raise ValueError("Discovery requires an eligible email or phone channel")
    return SetterModelOutput(
        action=SetterDecisionAction.DRAFT_TOUCH_1,
        rationale="Plantilla determinista para preguntar por una oportunidad aún no establecida.",
        stage=context.workflow_stage.value,
        channel=channel,
        evidence_ids=list(context.evidence_ids),
        guardrail_ids=[],
        assertion_ids=[],
        message=message,
        cta=cta,
        personalization_claims=[],
    )
