"""Public OTP endpoints; kept separate from demo business and quota routes."""
from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import ValidationError
from redis import Redis
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_token, generate_csrf_token
from app.db.session import get_db
from app.modules.demo.bootstrap import DemoBootstrapService
from app.modules.demo.errors import DemoError
from app.modules.demo.mail import DemoMailConfig, MailgunOtpSender
from app.modules.demo.otp import DemoOtpService, SqlAlchemyOtpRepository
from app.modules.demo.rate_limit import RedisDemoRateLimiter
from app.modules.demo.schemas import (
    DemoOtpChallengeRead,
    DemoOtpRequest,
    DemoOtpVerify,
    DemoOtpVerifyRead,
    DemoPublicConfigRead,
)
from app.modules.identity.schemas import UserRead
from app.modules.identity.service import IdentityService

router = APIRouter(prefix="/auth", tags=["demo-auth"])
settings = get_settings()


@router.get("/demo/config", response_model=DemoPublicConfigRead)
def public_demo_config() -> DemoPublicConfigRead:
    return DemoPublicConfigRead(enabled=settings.demo_public_mode)


@router.post("/demo/otp/request", response_model=DemoOtpChallengeRead, status_code=202)
async def request_demo_otp(
    request: Request,
    response: Response,
    origin: str | None = Header(None),
    db: Session = Depends(get_db),
) -> DemoOtpChallengeRead:
    _validate_public_request(request, origin)
    payload = await _parse_payload(request, DemoOtpRequest)
    service = _otp_service(db)
    client_ip = _client_ip(request)
    result = service.request_code(payload.email, client_ip=client_ip)
    response.status_code = 202
    return result


@router.post("/demo/otp/verify", response_model=DemoOtpVerifyRead)
async def verify_demo_otp(
    request: Request,
    response: Response,
    origin: str | None = Header(None),
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> DemoOtpVerifyRead:
    _validate_public_request(request, origin)
    if _has_active_auth_session(request, authorization):
        raise DemoError("active_session_present", "Cierra la sesión actual antes de cambiar de cuenta.", 409)

    service = _otp_service(db)
    client_ip = _client_ip(request)
    service.check_verify_attempt(client_ip)
    payload = await _parse_payload(request, DemoOtpVerify)
    try:
        verified = service.verify_code(
            payload.challenge_id, payload.code, client_ip=client_ip, rate_checked=True,
        )
        participant = DemoBootstrapService.for_session(db).ensure_participant(verified)
        identity = IdentityService(db)
        user = identity.get_user_by_id(participant.user_id)
        membership = identity.get_active_membership(participant.user_id, participant.workspace_id)
        if user is None or not user.is_active or membership is None:
            raise DemoError("otp_invalid_or_expired", "El código no es válido o ha caducado.", 400)

        access_token, refresh_token = identity.create_tokens(user, participant.workspace_id, membership.role)
        identity.store_refresh_token(user.id, refresh_token, request.headers.get("user-agent"), client_ip)
        # Rotate the browser's CSRF session after successful OTP authentication.
        csrf_session_id = secrets.token_urlsafe(24)
        csrf_token = generate_csrf_token(csrf_session_id)
        db.commit()
    except Exception:
        db.rollback()
        raise

    response.set_cookie(
        "access_token", access_token, httponly=True, secure=settings.cookie_secure,
        samesite=settings.cookie_samesite, max_age=settings.access_token_expire_minutes * 60,
    )
    response.set_cookie(
        "refresh_token", refresh_token, httponly=True, secure=settings.cookie_secure,
        samesite=settings.cookie_samesite, max_age=settings.refresh_token_expire_days * 86400,
    )
    response.set_cookie(
        "session_id", csrf_session_id, httponly=True, secure=settings.cookie_secure,
        samesite=settings.cookie_samesite, max_age=settings.csrf_token_expire_minutes * 60,
    )
    return DemoOtpVerifyRead(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserRead.model_validate(user),
        csrf_token=csrf_token,
        demo={"enabled": True, "workspace_id": participant.workspace_id},
    )


def _validate_public_request(request: Request, origin: str | None) -> None:
    if not settings.demo_public_mode:
        raise DemoError("demo_unavailable", "La demo no está disponible.", 503)
    if origin != settings.demo_public_origin:
        raise DemoError("origin_not_allowed", "Origen no permitido.", 403)
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if request.method == "POST" and media_type != "application/json":
        raise DemoError("json_required", "Se requiere una solicitud JSON.", 415)


async def _parse_payload(request: Request, schema):
    try:
        return schema.model_validate(await request.json())
    except (ValueError, ValidationError) as exc:
        raise DemoError(
            "otp_invalid_or_expired", "El código no es válido o ha caducado.", 400,
        ) from exc


def _otp_service(db: Session) -> DemoOtpService:
    try:
        redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
        limiter = RedisDemoRateLimiter(redis_client, hmac_key=settings.demo_otp_hmac_key.encode("utf-8"))
        sender = MailgunOtpSender(DemoMailConfig(
            domain=settings.mailgun_domain,
            api_key=settings.mailgun_api_key,
            from_address=settings.mailgun_sender_email,
            region=settings.mailgun_region,
            timeout=10,
        ))
        return DemoOtpService(
            repository=SqlAlchemyOtpRepository(db), sender=sender, limiter=limiter,
            hmac_key=settings.demo_otp_hmac_key.encode("utf-8"),
        )
    except Exception as exc:
        raise DemoError("demo_unavailable", "El acceso a la demo no está disponible.", 503) from exc


def _client_ip(request: Request) -> str:
    # Deliberately ignore X-Forwarded-For; only trust a directly connected client.
    if request.client is None or not request.client.host:
        raise DemoError("demo_unavailable", "No se pudo validar el origen de la solicitud.", 503)
    return request.client.host


def _has_active_auth_session(request: Request, authorization: str | None) -> bool:
    candidates = [request.cookies.get("access_token"), request.cookies.get("refresh_token")]
    if authorization and authorization.startswith("Bearer "):
        candidates.append(authorization[7:])
    now = datetime.now(UTC).timestamp()
    for token in candidates:
        if token:
            payload = decode_token(token)
            if payload and payload.get("type") in {"access", "refresh"} and payload.get("exp", 0) > now:
                return True
    return False
