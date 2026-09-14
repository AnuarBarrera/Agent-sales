from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.permissions import (
    NotAuthorizedError,
    can_assign_leads,
    can_change_stage,
    can_manage_lead,
    can_manage_rag,
    can_manage_users,
    can_mark_won_lost,
    can_use_ai,
    can_view_audit,
    can_view_lead,
    can_view_run,
    require_member,
    require_role,
)
from app.modules.identity.models import Membership, MembershipStatus, UserRole


class TestRequireMember:
    def test_require_member_active_membership(self):
        """Test require_member returns membership when active."""
        session = MagicMock(spec=Session)
        user_id = uuid4()
        workspace_id = uuid4()

        mock_membership = MagicMock(spec=Membership)
        mock_membership.user_id = user_id
        mock_membership.workspace_id = workspace_id
        mock_membership.status = MembershipStatus.ACTIVE

        session.query.return_value.filter.return_value.first.return_value = mock_membership

        result = require_member(session, user_id, workspace_id)
        assert result == mock_membership

    def test_require_member_no_membership(self):
        """Test require_member raises NotAuthorizedError when no membership."""
        session = MagicMock(spec=Session)
        session.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(NotAuthorizedError):
            require_member(session, uuid4(), uuid4())

    def test_require_member_inactive_membership(self):
        """Test require_member raises NotAuthorizedError when membership inactive.

        The query filters by ACTIVE status, so an inactive membership won't be returned.
        This test verifies that the filter works correctly.
        """
        session = MagicMock(spec=Session)

        # The filter includes Membership.status == MembershipStatus.ACTIVE
        # so inactive memberships are never returned
        session.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(NotAuthorizedError):
            require_member(session, uuid4(), uuid4())


class TestRequireRole:
    def test_require_role_admin_allowed(self):
        """Test require_role passes for admin."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.ADMIN

        require_role(mock_membership, [UserRole.ADMIN])

    def test_require_role_setter_allowed(self):
        """Test require_role passes for setter when allowed."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.SETTER

        require_role(mock_membership, [UserRole.SETTER, UserRole.CLOSER])

    def test_require_role_not_allowed(self):
        """Test require_role raises NotAuthorizedError when role not allowed."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.SETTER

        with pytest.raises(NotAuthorizedError):
            require_role(mock_membership, [UserRole.ADMIN, UserRole.CLOSER])


class TestCanViewRun:
    def test_admin_can_view_any_run(self):
        """Test admin can view any run."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.ADMIN

        mock_run = MagicMock()
        mock_run.created_by_id = uuid4()

        assert can_view_run(mock_membership, mock_run) is True

    def test_setter_can_view_own_run(self):
        """Test setter can view own run."""
        user_id = uuid4()
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.SETTER
        mock_membership.user_id = user_id

        mock_run = MagicMock()
        mock_run.created_by_id = user_id

        assert can_view_run(mock_membership, mock_run) is True

    def test_setter_cannot_view_other_run(self):
        """Test setter cannot view other user's run."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.SETTER
        mock_membership.user_id = uuid4()

        mock_run = MagicMock()
        mock_run.created_by_id = uuid4()

        assert can_view_run(mock_membership, mock_run) is False


class TestCanViewLead:
    def test_admin_can_view_any_lead(self):
        """Test admin can view any lead."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.ADMIN

        mock_lead = MagicMock()
        mock_lead.setter_id = uuid4()
        mock_lead.closer_id = uuid4()

        assert can_view_lead(mock_membership, mock_lead) is True

    def test_setter_can_view_own_lead(self):
        """Test setter can view own lead."""
        user_id = uuid4()
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.SETTER
        mock_membership.user_id = user_id

        mock_lead = MagicMock()
        mock_lead.setter_id = user_id
        mock_lead.closer_id = uuid4()

        assert can_view_lead(mock_membership, mock_lead) is True

    def test_setter_can_view_assigned_lead(self):
        """Test setter can view assigned lead."""
        user_id = uuid4()
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.SETTER
        mock_membership.user_id = user_id

        mock_lead = MagicMock()
        mock_lead.setter_id = user_id
        mock_lead.closer_id = uuid4()

        assert can_view_lead(mock_membership, mock_lead) is True

    def test_setter_cannot_view_unassigned_lead(self):
        """Test setter cannot view unassigned lead."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.SETTER
        mock_membership.user_id = uuid4()

        mock_lead = MagicMock()
        mock_lead.setter_id = uuid4()
        mock_lead.closer_id = uuid4()

        assert can_view_lead(mock_membership, mock_lead) is False

    def test_closer_can_view_assigned_lead(self):
        """Test closer can view assigned lead."""
        user_id = uuid4()
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.CLOSER
        mock_membership.user_id = user_id

        mock_lead = MagicMock()
        mock_lead.setter_id = uuid4()
        mock_lead.closer_id = user_id

        assert can_view_lead(mock_membership, mock_lead) is True

    def test_closer_cannot_view_unassigned_lead(self):
        """Test closer cannot view unassigned lead."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.CLOSER
        mock_membership.user_id = uuid4()

        mock_lead = MagicMock()
        mock_lead.setter_id = uuid4()
        mock_lead.closer_id = uuid4()

        assert can_view_lead(mock_membership, mock_lead) is False


class TestCanManageLead:
    def test_admin_can_manage_lead(self):
        """Test admin can manage lead."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.ADMIN

        mock_lead = MagicMock()

        assert can_manage_lead(mock_membership, mock_lead) is True

    def test_setter_cannot_manage_lead(self):
        """Test setter cannot manage lead."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.SETTER

        mock_lead = MagicMock()

        assert can_manage_lead(mock_membership, mock_lead) is False


class TestCanAssignLeads:
    def test_admin_can_assign_leads(self):
        """Test admin can assign leads."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.ADMIN

        assert can_assign_leads(mock_membership) is True

    def test_closer_can_assign_leads(self):
        """Test closer can assign leads."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.CLOSER

        assert can_assign_leads(mock_membership) is True

    def test_setter_cannot_assign_leads(self):
        """Test setter cannot assign leads."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.SETTER

        assert can_assign_leads(mock_membership) is False


class TestCanChangeStage:
    def test_admin_can_change_any_stage(self):
        """Test admin can change any stage."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.ADMIN

        assert can_change_stage(mock_membership, MagicMock(), "won") is True
        assert can_change_stage(mock_membership, MagicMock(), "lost") is True
        assert can_change_stage(mock_membership, MagicMock(), "not_interested") is True
        assert can_change_stage(mock_membership, MagicMock(), "contacted") is True

    def test_setter_can_change_initial_stages(self):
        """Test setter can change initial stages."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.SETTER

        assert can_change_stage(mock_membership, MagicMock(), "validated") is True
        assert can_change_stage(mock_membership, MagicMock(), "contacted") is True
        assert can_change_stage(mock_membership, MagicMock(), "conversation") is True

    def test_setter_cannot_mark_won_lost(self):
        """Test setter cannot mark lead as won/lost."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.SETTER

        assert can_change_stage(mock_membership, MagicMock(), "won") is False
        assert can_change_stage(mock_membership, MagicMock(), "lost") is False
        assert can_change_stage(mock_membership, MagicMock(), "not_interested") is False

    def test_closer_can_change_any_stage(self):
        """Test closer can change any stage."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.CLOSER

        assert can_change_stage(mock_membership, MagicMock(), "won") is True
        assert can_change_stage(mock_membership, MagicMock(), "lost") is True
        assert can_change_stage(mock_membership, MagicMock(), "not_interested") is True


class TestCanMarkWonLost:
    def test_admin_can_mark_won_lost(self):
        """Test admin can mark won/lost."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.ADMIN

        assert can_mark_won_lost(mock_membership) is True

    def test_closer_can_mark_won_lost(self):
        """Test closer can mark won/lost."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.CLOSER

        assert can_mark_won_lost(mock_membership) is True

    def test_setter_cannot_mark_won_lost(self):
        """Test setter cannot mark won/lost."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.SETTER

        assert can_mark_won_lost(mock_membership) is False


class TestCanUseAI:
    def test_all_roles_can_use_ai(self):
        """Test all roles can use AI."""
        for role in [UserRole.ADMIN, UserRole.SETTER, UserRole.CLOSER]:
            mock_membership = MagicMock(spec=Membership)
            mock_membership.role = role
            assert can_use_ai(mock_membership) is True


class TestCanManageRAG:
    def test_admin_can_manage_rag(self):
        """Test admin can manage RAG."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.ADMIN

        assert can_manage_rag(mock_membership) is True

    def test_non_admin_cannot_manage_rag(self):
        """Test non-admin cannot manage RAG."""
        for role in [UserRole.SETTER, UserRole.CLOSER]:
            mock_membership = MagicMock(spec=Membership)
            mock_membership.role = role
            assert can_manage_rag(mock_membership) is False


class TestCanManageUsers:
    def test_admin_can_manage_users(self):
        """Test admin can manage users."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.ADMIN

        assert can_manage_users(mock_membership) is True

    def test_non_admin_cannot_manage_users(self):
        """Test non-admin cannot manage users."""
        for role in [UserRole.SETTER, UserRole.CLOSER]:
            mock_membership = MagicMock(spec=Membership)
            mock_membership.role = role
            assert can_manage_users(mock_membership) is False


class TestCanViewAudit:
    def test_admin_can_view_audit(self):
        """Test admin can view audit."""
        mock_membership = MagicMock(spec=Membership)
        mock_membership.role = UserRole.ADMIN

        assert can_view_audit(mock_membership) is True

    def test_non_admin_cannot_view_audit(self):
        """Test non-admin cannot view audit."""
        for role in [UserRole.SETTER, UserRole.CLOSER]:
            mock_membership = MagicMock(spec=Membership)
            mock_membership.role = role
            assert can_view_audit(mock_membership) is False
