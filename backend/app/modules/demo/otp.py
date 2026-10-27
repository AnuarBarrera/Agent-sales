"""Cryptographic primitives and canonical identity handling for demo OTP."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.demo.errors import DemoError
from app.modules.demo.models import DemoOtpChallenge

if TYPE_CHECKING:
    from app.modules.demo.schemas import DemoOtpChallengeRead

_OTP = re.compile(r"[0-9]{6}\Z")
_EMAIL_ADAPTER = TypeAdapter(EmailStr)


@dataclass(frozen=True)
class VerifiedDemoEmail:
    """Server-created proof of an OTP challenge, never accepted from HTTP input."""

    email_key: str
    challenge_id: UUID


class SqlAlchemyOtpRepository:
    """Persistence boundary for OTP rows; verification locks a challenge row."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create_pending(self, challenge_id, email, digest, expires_at) -> None:
        self.db.add(DemoOtpChallenge(
            id=challenge_id, email_key=email, code_hmac=digest,
            expires_at=expires_at, attempts=0, delivery_status="pending",
        ))

    def invalidate_previous(self, email, now) -> None:
        self.db.execute(update(DemoOtpChallenge).where(
            DemoOtpChallenge.email_key == email,
            DemoOtpChallenge.invalidated_at.is_(None),
            DemoOtpChallenge.consumed_at.is_(None),
        ).values(invalidated_at=now))

    def commit(self) -> None:
        self.db.commit()

    def set_delivery_status(self, challenge_id, status) -> None:
        row = self.db.get(DemoOtpChallenge, challenge_id)
        if row is None:
            raise RuntimeError("OTP challenge disappeared")
        row.delivery_status = status

    def get_for_update(self, challenge_id):
        return self.db.execute(
            select(DemoOtpChallenge)
            .where(DemoOtpChallenge.id == challenge_id)
            .with_for_update()
        ).scalar_one_or_none()


def email_key(email: str) -> str:
    """Return a validated, casefolded email key without alias rewriting."""
    if not isinstance(email, str) or len(email.strip()) > 254:
        raise ValueError("invalid email address")
    try:
        canonical = str(_EMAIL_ADAPTER.validate_python(email.strip())).casefold()
    except (ValidationError, ValueError) as exc:
        raise ValueError("invalid email address") from exc
    if len(canonical) > 254:
        raise ValueError("invalid email address")
    return canonical


def create_otp_code() -> str:
    """Generate a uniformly distributed six-digit decimal code."""
    return f"{secrets.randbelow(1_000_000):06d}"


def otp_digest(key: bytes, challenge_id: UUID, canonical_email: str, code: str) -> bytes:
    """Create a domain-separated HMAC binding a code to challenge and email."""
    if len(key) < 32:
        raise ValueError("demo OTP HMAC key must contain at least 32 bytes")
    if not _OTP.fullmatch(code):
        raise ValueError("OTP must contain six ASCII digits")
    message = b"tuwebmx-demo-otp:v1\0" + b"\0".join(
        (str(challenge_id).encode("ascii"), email_key(canonical_email).encode("utf-8"), code.encode("ascii"))
    )
    return hmac.new(key, message, hashlib.sha256).digest()


def verify_otp_digest(
    key: bytes,
    challenge_id: UUID,
    canonical_email: str,
    code: str,
    expected_digest: bytes,
) -> bool:
    """Compare a submitted code in constant time, rejecting malformed values."""
    try:
        candidate = otp_digest(key, challenge_id, canonical_email, code)
    except ValueError:
        return False
    return hmac.compare_digest(candidate, expected_digest)


class DemoOtpService:
    """Issue and consume email challenges without holding a database lock over email delivery."""

    def __init__(self, *, repository, sender, limiter, hmac_key: bytes,
                 clock=lambda: datetime.now(UTC)) -> None:
        if len(hmac_key) < 32:
            raise ValueError("demo OTP HMAC key must contain at least 32 bytes")
        self.repository = repository
        self.sender = sender
        self.limiter = limiter
        self.hmac_key = hmac_key
        self.clock = clock

    def request_code(self, email: str, *, client_ip: str) -> DemoOtpChallengeRead:
        try:
            canonical = email_key(email)
        except ValueError as exc:
            raise DemoError("otp_invalid_or_expired", "No se pudo iniciar el acceso.", 400) from exc
        self._limit("check_request", canonical, client_ip)
        challenge_id = UUID(bytes=secrets.token_bytes(16), version=4)
        code = create_otp_code()
        now = self.clock()
        self.repository.invalidate_previous(canonical, now)
        self.repository.create_pending(
            challenge_id, canonical,
            otp_digest(self.hmac_key, challenge_id, canonical, code),
            now + timedelta(seconds=600),
        )
        self.repository.commit()
        try:
            self.sender.send_code(email=canonical, code=code, expires_in=600)
        except Exception as exc:
            self.repository.set_delivery_status(challenge_id, "failed")
            self.repository.commit()
            raise DemoError("demo_unavailable", "No se pudo enviar el código. Inténtalo más tarde.", 503) from exc
        self.repository.set_delivery_status(challenge_id, "sent")
        self.repository.commit()
        from app.modules.demo.schemas import DemoOtpChallengeRead

        return DemoOtpChallengeRead(challenge_id=challenge_id)

    def verify_code(
        self, challenge_id: UUID, code: str, *, client_ip: str, rate_checked: bool = False,
    ) -> VerifiedDemoEmail:
        if not rate_checked:
            self.check_verify_attempt(client_ip)
        row = self.repository.get_for_update(challenge_id)
        now = self.clock()
        valid_row = bool(
            row is not None
            and row.delivery_status == "sent"
            and row.invalidated_at is None
            and row.consumed_at is None
            and row.attempts < 5
            and _as_utc(row.expires_at) > now
        )
        if not valid_row or not verify_otp_digest(
            self.hmac_key, challenge_id, row.email_key if row is not None else "invalid@example.com",
            code, row.code_hmac if row is not None else b"",
        ):
            if row is not None and row.delivery_status == "sent" and row.invalidated_at is None \
                    and row.consumed_at is None and row.attempts < 5 and _as_utc(row.expires_at) > now:
                row.attempts += 1
            self.repository.commit()
            raise DemoError("otp_invalid_or_expired", "El código no es válido o ha caducado.", 400)
        row.consumed_at = now
        return VerifiedDemoEmail(email_key=row.email_key, challenge_id=challenge_id)

    def _limit(self, method: str, *args) -> None:
        try:
            getattr(self.limiter, method)(*args)
        except DemoError:
            raise
        except Exception as exc:
            raise DemoError("demo_unavailable", "El acceso a la demo no está disponible.", 503) from exc

    def check_verify_attempt(self, client_ip: str) -> None:
        """Count the IP before parsing so malformed verification bodies are limited too."""
        self._limit("check_verify", client_ip)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
