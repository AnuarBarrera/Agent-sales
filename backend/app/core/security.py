import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from jose import JWTError, jwt

from app.core.config import get_settings

settings = get_settings()

# Argon2 password hasher
ph = PasswordHasher()

# CSRF token store (in production, use Redis)
_csrf_tokens: dict[str, tuple[str, datetime]] = {}


def hash_password(password: str) -> str:
    """Hash a password using Argon2id."""
    return ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against its hash."""
    try:
        return ph.verify(hashed, password)
    except Exception:
        return False


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)  # type: ignore[return-value]


def create_refresh_token(data: dict) -> str:
    """Create a JWT refresh token."""
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)  # type: ignore[return-value]


def decode_token(token: str) -> dict | None:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        return payload  # type: ignore[return-value]
    except JWTError:
        return None


def generate_csrf_token(session_id: str) -> str:
    """Generate a CSRF token for a session."""
    token = secrets.token_urlsafe(32)
    expire = datetime.now(UTC) + timedelta(minutes=settings.csrf_token_expire_minutes)
    _csrf_tokens[session_id] = (token, expire)
    return token


def verify_csrf_token(session_id: str, token: str) -> bool:
    """Verify a CSRF token."""
    if session_id not in _csrf_tokens:
        return False
    stored_token, expire = _csrf_tokens[session_id]
    if datetime.now(UTC) > expire:
        del _csrf_tokens[session_id]
        return False
    return secrets.compare_digest(stored_token, token)


def cleanup_expired_csrf_tokens() -> None:
    """Remove expired CSRF tokens."""
    now = datetime.now(UTC)
    expired = [sid for sid, (_, exp) in _csrf_tokens.items() if exp < now]
    for sid in expired:
        del _csrf_tokens[sid]
