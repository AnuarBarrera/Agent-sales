from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SellerProfileUpdate(BaseModel):
    business_name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    offer_summary: str | None = None
    target_customer_summary: str | None = None
    location_or_market: str | None = Field(None, max_length=255)
    price_context: str | None = Field(None, max_length=500)
    primary_goal: str | None = Field(None, max_length=500)
    differentiators: str | None = None
    sales_constraints: str | None = None


class SellerProfileRead(SellerProfileUpdate):
    id: UUID
    workspace_id: UUID
    business_name: str = Field(..., min_length=1, max_length=255)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
