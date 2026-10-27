"""Server-owned Lead Fit policy authority and approval (T03)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.modules.identity.models import UserRole
from app.modules.lead_fit.contracts import (
    CriterionEvaluator,
    CriterionKind,
    CriterionSpec,
    FitPolicyContent,
    canonical_sha256,
    validate_policy_integrity,
)
from app.modules.lead_fit.models import LeadFitPolicy, LeadFitPolicyStatus
from app.modules.lead_fit.policy import compile_policy_proposal
from app.modules.sales_playbook.commercial_context import (
    ApprovedCommercialContext,
    verified_approved_commercial_context,
)
from app.modules.sales_playbook.commercial_service import CommercialContextService
from app.modules.sales_playbook.models import SalesPlaybook, SalesPlaybookStatus


class PolicyAuthorityNotFound(LookupError):
    """The requested approved playbook does not belong to this workspace."""


class PolicyAuthorityStale(ValueError):
    """The commercial authority is invalid or no longer source-current."""


@dataclass(frozen=True)
class VerifiedPolicyAuthority:
    """Detached verified authority reusable by evaluation and Setter consumers."""

    workspace_id: UUID
    playbook_id: UUID
    playbook_version: int
    commercial_digest: str
    context: ApprovedCommercialContext


def _approved_content_paths(value: Any, prefix: str = "commercial_context") -> set[str]:
    """Return stable field paths present in approved commercial content."""
    paths: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            path = f"{prefix}.{key}"
            paths.add(path)
            paths.update(_approved_content_paths(nested, path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            item_path = f"{prefix}[{index}]"
            paths.add(item_path)
            paths.update(_approved_content_paths(nested, item_path))
    return paths


def _validate_source_pointers(content: FitPolicyContent, context: ApprovedCommercialContext) -> None:
    """Bind every criterion to approved content or a reviewed justification."""
    approved_paths = _approved_content_paths({
        "business_name": context.business_name,
        "offer": context.offer.model_dump(mode="json"),
        "icp": context.icp.model_dump(mode="json"),
        "signals": [item.model_dump(mode="json") for item in context.signals],
        "communication": context.communication.model_dump(mode="json"),
    })
    for criterion in content.criteria:
        pointer = criterion.source_pointer.strip()
        if pointer.startswith("reviewed_justification:"):
            rationale = pointer.partition(":")[2].strip()
            if len(rationale) < 20:
                raise ValueError("La justificación revisada debe explicar la procedencia (mínimo 20 caracteres)")
            continue
        if pointer not in approved_paths:
            raise ValueError("source_pointer no corresponde al contenido comercial aprobado")


def _validate_opportunity_bindings(content: FitPolicyContent, context: ApprovedCommercialContext) -> None:
    """Preserve approved signal meaning without requiring an opportunity gate."""
    approved_opportunities = {
        f"commercial_context.signals[{index}]": (
            _opportunity_description(signal),
            # Previously approved policies retain their exact reviewed wording.
            f"Necesidad observable: {signal.description}. Cómo comprobar: {signal.evidence_hint}",
        )
        for index, signal in enumerate(context.signals)
        if signal.kind == "opportunity" and signal.direction == "positive"
    }
    for criterion in content.criteria:
        expected = approved_opportunities.get(criterion.source_pointer)
        if expected is not None and (
            criterion.kind not in (CriterionKind.REQUIRED, CriterionKind.PREFERENCE)
            or criterion.description not in expected
            or criterion.evaluator is not CriterionEvaluator.SEMANTIC
            or criterion.exact_rule is not None
        ):
            raise ValueError(
                "La oportunidad debe ser preferencia o indispensable y preservar la descripción "
                "y evidencia del contexto aprobado."
            )


def _opportunity_description(signal) -> str:
    """Describe an approved criterion, not a verified fact about a prospect."""
    description = (
        f"Oportunidad observable: {signal.description}. "
        f"Cómo comprobar: {signal.evidence_hint}"
    )
    if len(description) > 1000:
        raise PolicyAuthorityStale(
            "La oportunidad observable excede el límite de 1000 caracteres; "
            "acorte su descripción o cómo comprobarla."
        )
    return description


def _proposal(context: ApprovedCommercialContext) -> FitPolicyContent:
    """Reuse the existing compiler and retain all policy-relevant v4 inputs."""
    has_opportunity = any(
        signal.kind == "opportunity" and signal.direction == "positive"
        for signal in context.signals
    )
    try:
        content = compile_policy_proposal(
            icp_industries=context.icp.industries,
            icp_geographies=context.icp.geographies,
            buyer_roles=context.icp.buyer_roles,
            desired_traits=context.icp.desired_traits,
            exclusions=context.icp.exclusions,
            communication_channels=context.communication.allowed_channels,
        )
    except ValueError as exc:
        raise PolicyAuthorityStale(
            "El contexto aprobado excede los límites de una política; revise la propuesta comercial"
        ) from exc
    # With an approved observable opportunity, descriptive profile/priority
    # fields must not dilute its coverage or become an implicit extra gate.
    # Existing approved policies remain untouched; this is a reviewable proposal.
    criteria = [
        item for item in content.criteria
        if not has_opportunity or item.kind == CriterionKind.EXCLUSION
    ]
    for index, signal in enumerate(context.signals):
        is_opportunity = signal.kind == "opportunity" and signal.direction == "positive"
        if has_opportunity and not is_opportunity:
            continue
        if is_opportunity:
            description = _opportunity_description(signal)
        elif signal.direction == "negative":
            description = f"Se prioriza menos al negocio cuando presenta esta señal: {signal.description}"
        else:
            description = f"Se prioriza al negocio cuando presenta esta señal: {signal.description}"
        if len(description) > 1000:
            raise PolicyAuthorityStale(
                "La descripción de la señal excede el límite de 1000 caracteres; acórtela."
            )
        criteria.append(CriterionSpec(
            id=f"signal_{index + 1}",
            kind=CriterionKind.PREFERENCE,
            description=description,
            source_pointer=f"commercial_context.signals[{index}]",
            weight=20,
            evaluator=CriterionEvaluator.SEMANTIC,
            must_resolve=False,
        ))
    if len(criteria) > 10:
        raise PolicyAuthorityStale(
            "El contexto aprobado contiene más condiciones que las admitidas; revise la propuesta comercial"
        )
    return FitPolicyContent(
        criteria=tuple(criteria),
        accept_score=content.accept_score,
        min_coverage=content.min_coverage,
        choice_confidence_floor=content.choice_confidence_floor,
        entity_confidence_floor=content.entity_confidence_floor,
        max_evidence_age_hours=content.max_evidence_age_hours,
    )


class LeadFitPolicyService:
    """Policy service that derives all authority from the approved playbook."""

    def __init__(self, db) -> None:
        self.db = db

    def verified_authority(
        self, workspace_id: UUID, playbook_id: UUID, *, lock: bool = False,
    ) -> VerifiedPolicyAuthority:
        """Load and verify the tenant-scoped approved v4 playbook and source freshness."""
        statement = select(SalesPlaybook).where(
            SalesPlaybook.workspace_id == workspace_id,
            SalesPlaybook.id == playbook_id,
            SalesPlaybook.status == SalesPlaybookStatus.APPROVED,
        ).execution_options(populate_existing=True)
        if lock:
            statement = statement.with_for_update()
        playbook = self.db.execute(statement).scalar_one_or_none()
        if playbook is None:
            raise PolicyAuthorityNotFound("Approved playbook not found in this workspace")
        try:
            context = verified_approved_commercial_context(playbook)
            CommercialContextService(self.db).validate_commercial_bindings(
                workspace_id, context.source_snapshot, playbook.source_payload,
            )
        except (TypeError, ValueError) as exc:
            raise PolicyAuthorityStale("Approved commercial context is stale or invalid") from exc
        if context.workspace_id != workspace_id or context.playbook_id != playbook_id:
            raise PolicyAuthorityStale("Approved commercial context binding is invalid")
        return VerifiedPolicyAuthority(
            workspace_id=workspace_id,
            playbook_id=playbook_id,
            playbook_version=playbook.version,
            commercial_digest=context.content_digest,
            context=context,
        )

    def preview(self, workspace_id: UUID, playbook_id: UUID) -> dict[str, Any]:
        """Return an editable deterministic proposal with the actual current digest."""
        authority = self.verified_authority(workspace_id, playbook_id)
        content = _proposal(authority.context)
        validate_policy_integrity(content)
        _validate_source_pointers(content, authority.context)
        return {
            "content": content,
            "expected_policy_digest": canonical_sha256(content.model_dump(mode="json")),
            "expected_commercial_digest": authority.commercial_digest,
        }

    def approve(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        actor_role: UserRole,
        playbook_id: UUID,
        content: FitPolicyContent,
        expected_commercial_digest: str,
        confirmed: bool,
    ) -> LeadFitPolicy:
        """Approve a reviewed proposal only against freshly verified authority."""
        if actor_role != UserRole.ADMIN:
            raise PermissionError("Solo ADMIN puede aprobar políticas de encaje")
        if not confirmed:
            raise ValueError("confirmed debe ser true para aprobar una política")
        validate_policy_integrity(content)

        # The playbook row is the serialization point shared with commercial edits.
        authority = self.verified_authority(workspace_id, playbook_id, lock=True)
        if expected_commercial_digest != authority.commercial_digest:
            raise PolicyAuthorityStale("El digest comercial esperado ya no está vigente")
        _validate_source_pointers(content, authority.context)
        _validate_opportunity_bindings(content, authority.context)

        content_dump = content.model_dump(mode="json")
        policy_digest = canonical_sha256(content_dump)
        existing = self.db.execute(
            select(LeadFitPolicy).where(
                LeadFitPolicy.workspace_id == workspace_id,
                LeadFitPolicy.playbook_id == playbook_id,
                LeadFitPolicy.status == LeadFitPolicyStatus.APPROVED,
            ).order_by(LeadFitPolicy.version.desc()).limit(1)
        ).scalar_one_or_none()
        if (
            existing is not None
            and existing.commercial_digest == authority.commercial_digest
            and existing.policy_digest == policy_digest
            and existing.content == content_dump
        ):
            return existing

        policy = LeadFitPolicy(
            workspace_id=workspace_id,
            playbook_id=playbook_id,
            version=(existing.version + 1) if existing else 1,
            commercial_digest=authority.commercial_digest,
            content=content_dump,
            policy_digest=policy_digest,
            actor_id=actor_id,
            status=LeadFitPolicyStatus.APPROVED,
        )
        self.db.add(policy)
        self.db.flush()
        return policy

    def current(self, workspace_id: UUID, playbook_id: UUID) -> LeadFitPolicy | None:
        """Return the current policy only while its commercial authority is fresh."""
        try:
            authority = self.verified_authority(workspace_id, playbook_id)
        except PolicyAuthorityStale:
            return None
        policy = self.db.execute(
            select(LeadFitPolicy).where(
                LeadFitPolicy.workspace_id == workspace_id,
                LeadFitPolicy.playbook_id == playbook_id,
                LeadFitPolicy.status == LeadFitPolicyStatus.APPROVED,
            ).order_by(LeadFitPolicy.version.desc()).limit(1)
        ).scalar_one_or_none()
        if policy is None or policy.commercial_digest != authority.commercial_digest:
            return None
        try:
            content = FitPolicyContent.model_validate(policy.content)
            validate_policy_integrity(content)
            _validate_source_pointers(content, authority.context)
            if canonical_sha256(content.model_dump(mode="json")) != policy.policy_digest:
                return None
        except (TypeError, ValueError):
            return None
        return policy
