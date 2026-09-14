import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.notifications.models import (
    Handoff,
    HandoffStatus,
    Notification,
    NotificationStatus,
    NotificationType,
)

logger = logging.getLogger(__name__)


class NotificationsService:
    def __init__(self, db: Session):
        self.db = db

    def create_notification(
        self,
        workspace_id: UUID,
        user_id: UUID,
        notification_type: str,
        title: str,
        message: str,
        source_id: UUID | None = None,
        source_type: str | None = None,
    ) -> Notification:
        """Create a new notification."""
        notification = Notification(
            workspace_id=workspace_id,
            user_id=user_id,
            type=notification_type,
            title=title,
            message=message,
            source_id=source_id,
            source_type=source_type,
        )
        self.db.add(notification)
        self.db.flush()
        return notification

    def create_notification_for_task_due(
        self,
        task,
        workspace_id: UUID,
        user_id: UUID,
        notification_type: str,
    ) -> Notification:
        """Create a notification for task due/overdue."""
        titles = {
            "task_due_soon": "Tarea próxima a vencer",
            "task_overdue": "Tarea vencida",
        }
        messages = {
            "task_due_soon": f"La tarea '{task.title}' vence pronto.",
            "task_overdue": f"La tarea '{task.title}' está vencida.",
        }
        return self.create_notification(
            workspace_id=workspace_id,
            user_id=user_id,
            notification_type=notification_type,
            title=titles.get(notification_type, "Notificación"),
            message=messages.get(notification_type, ""),
            source_id=task.id,
            source_type="task",
        )

    def get_notifications(
        self,
        workspace_id: UUID,
        user_id: UUID,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> list[Notification]:
        """Get notifications for a user."""
        query = select(Notification).where(
            Notification.workspace_id == workspace_id,
            Notification.user_id == user_id,
        )

        if status:
            query = query.where(Notification.status == status)

        if cursor:
            try:
                cursor_id = UUID(cursor)
                cursor_notif = self.db.get(Notification, cursor_id)
                if cursor_notif:
                    query = query.where(Notification.created_at < cursor_notif.created_at)
            except ValueError:
                pass

        query = query.order_by(Notification.created_at.desc()).limit(limit + 1)
        notifications = list(self.db.execute(query).scalars().all())
        return notifications[:limit]

    def get_unread_count(self, workspace_id: UUID, user_id: UUID) -> int:
        """Get count of unread notifications."""
        result = self.db.execute(
            select(func.count(Notification.id)).where(
                Notification.workspace_id == workspace_id,
                Notification.user_id == user_id,
                Notification.status == NotificationStatus.UNREAD,
            )
        ).scalar_one()
        return result

    def mark_as_read(
        self,
        notification_id: UUID,
        user_id: UUID,
        workspace_id: UUID,
    ) -> bool:
        """Mark a notification as read."""
        notification = self.db.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.workspace_id == workspace_id,
                Notification.user_id == user_id,
            )
        ).scalar_one_or_none()

        if not notification:
            return False

        notification.status = NotificationStatus.READ
        notification.read_at = datetime.now(UTC)
        self.db.flush()
        return True

    def mark_all_as_read(self, workspace_id: UUID, user_id: UUID) -> int:
        """Mark all unread notifications as read."""
        result = self.db.execute(
            select(Notification).where(
                Notification.workspace_id == workspace_id,
                Notification.user_id == user_id,
                Notification.status == NotificationStatus.UNREAD,
            )
        ).scalars().all()

        count = 0
        for notification in result:
            notification.status = NotificationStatus.READ
            notification.read_at = datetime.now(UTC)
            count += 1

        self.db.flush()
        return count

    def dismiss_notification(
        self,
        notification_id: UUID,
        user_id: UUID,
        workspace_id: UUID,
    ) -> bool:
        """Dismiss a notification."""
        notification = self.db.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.workspace_id == workspace_id,
                Notification.user_id == user_id,
            )
        ).scalar_one_or_none()

        if not notification:
            return False

        notification.status = NotificationStatus.DISMISSED
        self.db.flush()
        return True

    # Handoff methods
    def create_handoff(
        self,
        workspace_id: UUID,
        lead_id: UUID,
        from_user_id: UUID,
        to_user_id: UUID,
        reason: str | None = None,
    ) -> Handoff:
        """Create a handoff request and notify the receiving closer."""
        from app.modules.activities.models import Activity, ActivityType
        from app.modules.identity.models import Membership, MembershipStatus, UserRole
        from app.modules.leads.models import Lead
        from app.modules.pipeline.models import PipelineStage

        lead = self.db.execute(
            select(Lead).where(Lead.id == lead_id, Lead.workspace_id == workspace_id)
        ).scalar_one_or_none()
        if not lead:
            raise ValueError("Lead not found")
        sender = self.db.execute(
            select(Membership).where(
                Membership.user_id == from_user_id,
                Membership.workspace_id == workspace_id,
                Membership.status == MembershipStatus.ACTIVE,
            )
        ).scalar_one_or_none()
        if not sender:
            raise ValueError("Sender is not an active workspace member")
        if sender.role == UserRole.SETTER:
            if lead.setter_id != from_user_id:
                raise ValueError("A setter can only hand off their own leads")
            stage = self.db.get(PipelineStage, lead.pipeline_stage_id) if lead.pipeline_stage_id else None
            if not stage or stage.key not in {"validated", "contacted", "conversation"}:
                raise ValueError("Lead is not ready for handoff")
        recipient = self.db.execute(
            select(Membership).where(
                Membership.user_id == to_user_id,
                Membership.workspace_id == workspace_id,
                Membership.status == MembershipStatus.ACTIVE,
                Membership.role.in_([UserRole.CLOSER, UserRole.ADMIN]),
            )
        ).scalar_one_or_none()
        if not recipient:
            raise ValueError("Recipient must be an active closer or admin")
        lead.closer_id = to_user_id
        handoff = Handoff(
            workspace_id=workspace_id,
            lead_id=lead_id,
            from_user_id=from_user_id,
            to_user_id=to_user_id,
            reason=reason,
            status=HandoffStatus.PENDING,
        )
        self.db.add(handoff)
        self.db.flush()
        self.db.add(
            Activity(
                workspace_id=workspace_id,
                lead_id=lead_id,
                type=ActivityType.ASSIGNMENT,
                content=f"Lead entregado a {to_user_id}",
                author_id=from_user_id,
                related_id=handoff.id,
            )
        )
        self.db.flush()
        self.create_notification(
            workspace_id=workspace_id,
            user_id=to_user_id,
            notification_type=NotificationType.HANDOFF_RECEIVED.value,
            title="Nuevo lead entregado",
            message=f"Se te entregó el lead {lead_id}.",
            source_id=handoff.id,
            source_type="handoff",
        )
        return handoff

    def get_handoff(self, handoff_id: UUID, workspace_id: UUID) -> Handoff | None:
        """Get a handoff by ID."""
        return self.db.execute(
            select(Handoff).where(
                Handoff.id == handoff_id,
                Handoff.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()

    def get_handoffs_for_user(
        self,
        workspace_id: UUID,
        user_id: UUID,
        status: str | None = None,
    ) -> list[Handoff]:
        """Get handoffs for a user (as recipient)."""
        query = select(Handoff).where(
            Handoff.workspace_id == workspace_id,
            Handoff.to_user_id == user_id,
        )

        if status:
            query = query.where(Handoff.status == status)

        query = query.order_by(Handoff.initiated_at.desc())
        return list(self.db.execute(query).scalars().all())

    def respond_to_handoff(
        self,
        handoff_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        accept: bool,
    ) -> Handoff | None:
        """Accept or reject a handoff."""
        handoff = self.get_handoff(handoff_id, workspace_id)
        if not handoff or handoff.to_user_id != user_id:
            return None

        if handoff.status != HandoffStatus.PENDING:
            raise ValueError("Handoff already responded to")

        handoff.status = HandoffStatus.ACCEPTED if accept else HandoffStatus.REJECTED
        handoff.responded_at = datetime.now(UTC)
        self.db.flush()

        # Create notification for the sender
        self.create_notification(
            workspace_id=handoff.workspace_id,
            user_id=handoff.from_user_id,
            notification_type="handoff_response",
            title="Entrega de lead respondida",
            message=f"Tu entrega de lead ha sido {'aceptada' if accept else 'rechazada'}",
            source_id=handoff.id,
            source_type="handoff",
        )

        # If accepted, update lead assignment
        if accept:
            from app.modules.identity.models import Membership, MembershipStatus
            from app.modules.leads.models import Lead

            lead = self.db.get(Lead, handoff.lead_id)
            if lead:
                # Check if the receiving user has membership
                membership = self.db.execute(
                    select(Membership).where(
                        Membership.user_id == user_id,
                        Membership.workspace_id == handoff.workspace_id,
                        Membership.status == MembershipStatus.ACTIVE,
                    )
                ).scalar_one_or_none()

                if membership:
                    lead.closer_id = user_id
                    self.db.flush()

        self.db.flush()
        return handoff
