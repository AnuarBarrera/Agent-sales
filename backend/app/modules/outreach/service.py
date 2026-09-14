import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import can_view_lead, require_member
from app.integrations.llm import LLMProvider
from app.modules.leads.models import Lead
from app.modules.outreach.models import (
    Conversation,
    MessageStatus,
    OutreachMessage,
)

logger = logging.getLogger(__name__)


class OutreachService:
    def __init__(self, db: Session):
        self.db = db
        self.llm = LLMProvider()

    def _get_lead(self, workspace_id: UUID, lead_id: UUID) -> Lead:
        """Load a lead scoped to the active workspace."""
        lead = self.db.execute(
            select(Lead).where(Lead.id == lead_id, Lead.workspace_id == workspace_id)
        ).scalar_one_or_none()
        if not lead:
            raise ValueError("Lead not found")
        return lead

    def create_draft(
        self,
        workspace_id: UUID,
        lead_id: UUID,
        user_id: UUID,
        channel: str,
        context: str | None = None,
        tone: str | None = None,
        length: str | None = None,
        purpose: str | None = None,
    ):
        """Create an AI-generated message draft."""
        lead = self._get_lead(workspace_id, lead_id)
        membership = require_member(self.db, user_id, workspace_id)
        if not can_view_lead(membership, lead):
            raise PermissionError("Not authorized to create outreach for this lead")

        if not lead.business:
            raise ValueError("Lead has no associated business")

        business = lead.business

        # Build AI prompt
        prompt = self._build_draft_prompt(
            business=business,
            lead=lead,
            channel=channel,
            context=context,
            tone=tone,
            length=length,
            purpose=purpose,
        )

        # Call LLM
        try:
            response = self.llm.generate(prompt)
            draft_text = response.get("text", "")
            tokens_used = response.get("tokens", 0)
            model = response.get("model", "unknown")

            # Validate draft
            warnings = self._validate_draft(draft_text, channel, business.name)

            conversation = self.list_conversations(workspace_id, lead_id, limit=1)
            conversation = conversation[0] if conversation else self.create_conversation(
                workspace_id, lead_id, subject=f"Seguimiento: {business.name}"
            )
            message = self.create_message(
                workspace_id=workspace_id,
                lead_id=lead_id,
                user_id=user_id,
                conversation_id=conversation.id,
                direction="outbound",
                channel=channel,
                body=draft_text,
                ai_generated=True,
                ai_model=model,
                ai_prompt=prompt,
                ai_context=context,
                status=MessageStatus.DRAFT.value,
            )

            return {
                "draft": draft_text,
                "message_id": str(message.id),
                "status": getattr(message.status, "value", message.status),
                "ai_model": model,
                "tokens_used": tokens_used,
                "warnings": warnings,
            }
        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            raise

    def _build_draft_prompt(
        self,
        business,
        lead,
        channel: str,
        context: str | None = None,
        tone: str | None = None,
        length: str | None = None,
        purpose: str | None = None,
    ) -> str:
        """Build the AI prompt for draft generation."""
        prompt_parts = [
            "Eres un asistente de ventas experto para Tu Web MX.",
            f"Genera un mensaje {channel} para un prospecto.",
            "",
            "Información del negocio:",
            f"- Nombre: {business.name}",
            f"- Giro: {lead.giro if hasattr(lead, 'giro') else 'No especificado'}",
            f"- Teléfono: {business.phone or 'No disponible'}",
            f"- Sitio web: {business.website or 'No disponible'}",
            "",
            "Información del lead:",
            f"- Nombre: {lead.business.name if lead.business else 'No disponible'}",
        ]

        if context:
            prompt_parts.append(f"\nContexto adicional: {context}")
        if tone:
            prompt_parts.append(f"Tono: {tone}")
        if length:
            prompt_parts.append(f"Longitud: {length}")
        if purpose:
            prompt_parts.append(f"Propósito: {purpose}")

        prompt_parts.extend([
            "",
            "Reglas de validación:",
            "- 35-80 palabras",
            "- Incluir nombre del negocio",
            "- Una pregunta para generar respuesta",
            "- No usar términos internos",
            "- Tono profesional pero cercano",
            "",
            "Genera SOLO el mensaje, sin explicaciones adicionales.",
        ])

        return "\n".join(prompt_parts)

    def _validate_draft(self, draft: str, channel: str, business_name: str) -> list[str]:
        """Validate draft against rules."""
        warnings = []
        words = draft.split()

        if len(words) < 35:
            warnings.append(f"Mensaje muy corto ({len(words)} palabras, mínimo 35)")
        elif len(words) > 80:
            warnings.append(f"Mensaje muy largo ({len(words)} palabras, máximo 80)")

        if business_name.lower() not in draft.lower():
            warnings.append("No menciona el nombre del negocio")

        # Check for question mark
        if "?" not in draft:
            warnings.append("No contiene pregunta para generar respuesta")

        return warnings

    def create_conversation(
        self,
        workspace_id: UUID,
        lead_id: UUID,
        subject: str | None = None,
    ) -> Conversation:
        """Create a new conversation."""
        self._get_lead(workspace_id, lead_id)
        conversation = Conversation(
            workspace_id=workspace_id,
            lead_id=lead_id,
            subject=subject,
        )
        self.db.add(conversation)
        self.db.flush()
        return conversation

    def create_message(
        self,
        workspace_id: UUID,
        lead_id: UUID,
        user_id: UUID,
        conversation_id: UUID | None = None,
        direction: str = "outbound",
        channel: str = "whatsapp",
        subject: str | None = None,
        body: str = "",
        ai_generated: bool = False,
        ai_model: str | None = None,
        ai_prompt: str | None = None,
        ai_context: str | None = None,
        status: str = "draft",
    ) -> OutreachMessage:
        """Create a message."""
        self._get_lead(workspace_id, lead_id)
        if conversation_id:
            conversation = self.get_conversation(conversation_id, workspace_id)
            if not conversation or conversation.lead_id != lead_id:
                raise ValueError("Conversation not found for lead")
        message = OutreachMessage(
            workspace_id=workspace_id,
            lead_id=lead_id,
            conversation_id=conversation_id,
            author_id=user_id,
            direction=direction,
            channel=channel,
            subject=subject,
            body=body,
            ai_generated=ai_generated,
            ai_model=ai_model,
            ai_prompt=ai_prompt,
            ai_context=ai_context,
            status=status,
        )
        self.db.add(message)
        self.db.flush()
        return message

    def get_message(self, message_id: UUID, workspace_id: UUID) -> OutreachMessage | None:
        """Get a message by ID."""
        return self.db.execute(
            select(OutreachMessage).where(
                OutreachMessage.id == message_id,
                OutreachMessage.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()

    def approve_message(
        self,
        message_id: UUID,
        workspace_id: UUID,
        approver_id: UUID,
        modified_body: str | None = None,
    ) -> OutreachMessage | None:
        """Approve a draft message."""
        message = self.db.execute(
            select(OutreachMessage).where(
                OutreachMessage.id == message_id,
                OutreachMessage.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()

        if not message:
            return None

        if message.status != MessageStatus.DRAFT:
            raise ValueError("Only draft messages can be approved")

        if modified_body is not None:
            message.body = modified_body

        message.status = MessageStatus.APPROVED
        message.approved_by_id = approver_id
        message.approved_at = datetime.now(UTC)
        self.db.flush()
        return message

    def reject_message(
        self,
        message_id: UUID,
        workspace_id: UUID,
        approver_id: UUID,
    ) -> OutreachMessage | None:
        """Reject a draft message."""
        message = self.db.execute(
            select(OutreachMessage).where(
                OutreachMessage.id == message_id,
                OutreachMessage.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()

        if not message:
            return None

        if message.status != MessageStatus.DRAFT:
            raise ValueError("Only draft messages can be rejected")

        message.status = MessageStatus.REJECTED
        message.approved_by_id = approver_id
        message.approved_at = datetime.now(UTC)
        self.db.flush()
        return message

    def mark_sent(
        self,
        message_id: UUID,
        workspace_id: UUID,
    ) -> OutreachMessage | None:
        """Mark a message as sent."""
        message = self.db.execute(
            select(OutreachMessage).where(
                OutreachMessage.id == message_id,
                OutreachMessage.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()

        if not message:
            return None

        if message.status != MessageStatus.APPROVED:
            raise ValueError("Only approved messages can be sent")

        message.status = MessageStatus.SENT
        message.sent_at = datetime.now(UTC)
        self.db.flush()
        return message

    def get_conversation(self, conversation_id: UUID, workspace_id: UUID) -> Conversation | None:
        """Get a conversation by ID."""
        return self.db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()

    def list_conversations(
        self,
        workspace_id: UUID,
        lead_id: UUID | None = None,
        limit: int = 50,
    ) -> list[Conversation]:
        query = select(Conversation).where(Conversation.workspace_id == workspace_id)
        if lead_id:
            query = query.where(Conversation.lead_id == lead_id)
        query = query.order_by(Conversation.updated_at.desc())
        return list(self.db.execute(query.limit(limit)).scalars().all())

    def get_lead_messages(
        self,
        lead_id: UUID,
        workspace_id: UUID,
        limit: int = 50,
    ) -> list[OutreachMessage]:
        """Get all messages for a lead."""
        query = select(OutreachMessage).where(
            OutreachMessage.lead_id == lead_id,
            OutreachMessage.workspace_id == workspace_id,
        ).order_by(OutreachMessage.created_at.desc()).limit(limit)
        return list(self.db.execute(query).scalars().all())
