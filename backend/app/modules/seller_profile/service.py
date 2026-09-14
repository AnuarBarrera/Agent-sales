from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.seller_profile.models import SellerProfile
from app.modules.seller_profile.schemas import SellerProfileUpdate


class SellerProfileService:
    def __init__(self, db: Session):
        self.db = db

    def get(self, workspace_id: UUID) -> SellerProfile | None:
        return self.db.execute(
            select(SellerProfile).where(SellerProfile.workspace_id == workspace_id)
        ).scalar_one_or_none()

    def upsert(self, workspace_id: UUID, data: SellerProfileUpdate) -> tuple[SellerProfile, bool]:
        profile = self.get(workspace_id)
        created = profile is None
        if profile is None:
            if data.business_name is None:
                raise ValueError("business_name is required when creating a seller profile")
            profile = SellerProfile(workspace_id=workspace_id, business_name=data.business_name)
            self.db.add(profile)

        for field, value in data.model_dump(exclude_unset=True).items():
            if value is not None or field == "business_name":
                setattr(profile, field, value)
        self.db.flush()
        return profile, created
