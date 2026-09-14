from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.permissions import require_member
from app.db.session import get_db
from app.modules.identity.router import get_current_user
from app.modules.seller_profile.schemas import SellerProfileRead, SellerProfileUpdate
from app.modules.seller_profile.service import SellerProfileService

router = APIRouter(prefix="/seller-profile", tags=["seller-profile"])


@router.get("", response_model=SellerProfileRead)
def get_seller_profile(
    current_user: tuple[UUID, UUID, str] = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SellerProfileRead:
    _, workspace_id, _ = current_user
    require_member(db, current_user[0], workspace_id)
    profile = SellerProfileService(db).get(workspace_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Seller profile not found")
    return SellerProfileRead.model_validate(profile)


@router.put("", response_model=SellerProfileRead)
def upsert_seller_profile(
    data: SellerProfileUpdate,
    response: Response,
    current_user: tuple[UUID, UUID, str] = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SellerProfileRead:
    user_id, workspace_id, _ = current_user
    require_member(db, user_id, workspace_id)
    try:
        profile, created = SellerProfileService(db).upsert(workspace_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return SellerProfileRead.model_validate(profile)
