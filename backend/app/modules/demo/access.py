"""Server-derived demo principals and a closed route capability boundary."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from app.modules.demo.errors import DemoError
from app.modules.demo.models import DemoParticipant


@dataclass(frozen=True)
class DemoPrincipal:
    participant_id: UUID
    user_id: UUID
    workspace_id: UUID


def load_demo_principal(db, user_id: UUID, workspace_id: UUID) -> DemoPrincipal | None:
    participant = db.execute(select(DemoParticipant).where(
        DemoParticipant.workspace_id == workspace_id,
    )).scalar_one_or_none()
    if participant is None:
        return None
    if participant.user_id != user_id:
        raise DemoError("demo_owner_required", "Este espacio de demo es privado.", 403)
    return DemoPrincipal(participant.id, user_id, workspace_id)


_READ_PATHS = frozenset({
    "/auth/me", "/demo/session", "/seller-profile", "/sales-playbooks",
    "/sales-playbooks/active", "/sales-playbooks/{playbook_id}",
    "/lead-fit/capabilities", "/lead-fit/policies/current", "/leads",
    "/leads/{lead_id}", "/leads/{lead_id}/research",
    "/leads/{lead_id}/research/evidence", "/leads/{lead_id}/analysis",
    "/leads/{lead_id}/timeline", "/activities/leads/{lead_id}/timeline",
    "/leads/{lead_id}/fit-evaluations/context", "/leads/{lead_id}/fit-evaluations/latest",
    "/leads/{lead_id}/setter/latest", "/leads/{lead_id}/setter/readiness",
    "/leads/{lead_id}/setter/executions/{execution_id}/evidence",
    "/prospection-runs", "/prospection-runs/{run_id}",
    "/prospection-runs/{run_id}/fit-results", "/prospection-runs/{run_id}/events",
    "/pipeline/stages",
    "/demo/leads/{lead_id}/draft-review",
    "/demo/leads/{lead_id}/follow-up",
    "/demo/leads/{lead_id}/ai-suggestion",
})
_WRITE_PATHS = frozenset({
    ("POST", "/auth/logout"), ("PUT", "/seller-profile"),
    ("POST", "/sales-playbooks/commercial-context/drafts"),
    ("PATCH", "/sales-playbooks/{playbook_id}/commercial-context"),
    ("POST", "/sales-playbooks/{playbook_id}/approve"),
    ("POST", "/lead-fit/policies/preview"), ("POST", "/lead-fit/policies"),
    ("POST", "/demo/searches"), ("POST", "/demo/leads/{lead_id}/draft"),
    ("POST", "/demo/leads/{lead_id}/draft-review"),
    ("POST", "/demo/leads/{lead_id}/ai-suggestion"),
    ("POST", "/demo/leads/{lead_id}/follow-up/notes"),
    ("POST", "/demo/leads/{lead_id}/follow-up/contacts"),
    ("POST", "/demo/leads/{lead_id}/follow-up/tasks"),
    ("POST", "/demo/leads/{lead_id}/follow-up/tasks/{task_id}/complete"),
})


def enforce_demo_access(request, principal: DemoPrincipal | None) -> None:
    if principal is None:
        return
    # Use the matched framework route, not a user-supplied URL or substring.
    route = request.scope.get("route")
    path = getattr(route, "path", "")
    from app.core.config import get_settings

    prefix = get_settings().api_prefix
    if not path.startswith(prefix + "/"):
        raise DemoError("demo_route_unavailable", "Esta función no está disponible en la demo.", 403)
    path = path[len(prefix):]
    allowed = (request.method == "GET" and path in _READ_PATHS) or (request.method, path) in _WRITE_PATHS
    if not allowed:
        raise DemoError("demo_route_unavailable", "Esta función no está disponible en la demo.", 403)
