from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator

from app.modules.identity.models import MembershipStatus, UserRole


# User schemas
class UserBase(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=255)


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=128)


class UserUpdate(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=255)
    is_active: bool | None = None


class UserRead(UserBase):
    id: UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Workspace schemas
class WorkspaceBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=100)


class WorkspaceCreate(WorkspaceBase):
    pass


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    is_active: bool | None = None


class WorkspaceRead(WorkspaceBase):
    id: UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Membership schemas
class MembershipRead(BaseModel):
    id: UUID
    user_id: UUID
    workspace_id: UUID
    role: str
    status: str
    joined_at: datetime
    user: UserRead

    class Config:
        from_attributes = True


class MembershipUpdate(BaseModel):
    role: UserRole | None = None
    status: MembershipStatus | None = None

    @model_validator(mode="after")
    def require_change(self) -> "MembershipUpdate":
        if self.role is None and self.status is None:
            raise ValueError("At least one membership field is required")
        return self


# Invitation schemas
class InvitationCreate(BaseModel):
    email: EmailStr
    role: UserRole = Field(..., pattern="^(setter|closer)$")


class InvitationRead(BaseModel):
    id: UUID
    workspace_id: UUID
    email: EmailStr
    role: str
    invited_by_id: UUID | None
    expires_at: datetime
    accepted_at: datetime | None
    created_at: datetime

    class Config:
        from_attributes = True


# Auth schemas
class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserRead
    csrf_token: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class AcceptInvitationRequest(BaseModel):
    token: str
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field(..., min_length=1, max_length=255)


# Current user info
class CurrentUserDemo(BaseModel):
    enabled: Literal[True] = True
    workspace_id: UUID


class CurrentUserResponse(BaseModel):
    user: UserRead
    workspace: WorkspaceRead
    membership: MembershipRead
    demo: CurrentUserDemo | None = None
