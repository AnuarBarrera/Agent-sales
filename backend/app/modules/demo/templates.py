"""Public, ID-free commercial example used to seed private demo workspaces."""
from __future__ import annotations

from app.modules.sales_playbook.commercial_context import (
    CommercialContextContent,
    CommunicationBlock,
    ICPBlock,
    OfferBlock,
    SignalBlock,
)

DEMO_TEMPLATE_VERSION = "tuwebmx-demo-v1"


def demo_template() -> CommercialContextContent:
    """Build a fresh validated example; no tenant IDs or approval data included."""
    return CommercialContextContent.model_validate({
        "business_name": "Tu Web MX (ejemplo)",
        "offer": OfferBlock(
            summary="Diseño y desarrollo de sitios web para negocios que necesitan presentar mejor su oferta.",
            problem_solved="Ayudar a que un negocio explique sus servicios y facilite el contacto desde su propio sitio.",
            differentiators=("Acompañamiento directo durante el proyecto",),
            price_context=None,
        ),
        "icp": ICPBlock(
            industries=("servicios locales", "salud", "profesionales independientes"),
            geographies=("Ciudad de México",),
            buyer_roles=("propietario", "persona responsable del negocio"),
            desired_traits=("Negocio con una oferta que necesita comunicar en línea",),
            exclusions=(
                "Excluir sólo cuando exista evidencia atribuible de un sitio propio operativo.",
            ),
        ),
        "signals": (
            SignalBlock(
                id="website_opportunity",
                kind="opportunity",
                direction="positive",
                description=(
                    "Investigar una ausencia o falla observable del sitio propio; una URL no encontrada "
                    "por sí sola no confirma que el negocio carezca de sitio."
                ),
                evidence_hint="URL del sitio y observación verificable con fecha o fuente atribuible.",
            ),
            SignalBlock(
                id="operating_website_exclusion",
                kind="fit",
                direction="negative",
                description="Un sitio propio operativo puede excluir al negocio si hay evidencia atribuible.",
                evidence_hint="URL propia que carga y presenta información vigente del negocio.",
            ),
        ),
        "communication": CommunicationBlock(
            tone="Claro, respetuoso y específico a la evidencia disponible.",
            call_to_action="Preguntar si sería útil conversar sobre su sitio web.",
            allowed_channels=("email", "phone"),
            forbidden_claims=(
                "No prometer resultados, ventas, exactitud predictiva ni intención de compra.",
                "No afirmar ausencia de sitio sin evidencia verificable.",
            ),
            contact_restrictions=("Usar sólo datos de contacto atribuibles al negocio.",),
            pricing_policy="omit",
        ),
    })
