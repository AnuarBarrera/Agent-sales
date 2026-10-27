"""Atomic Redis backed OTP admission limits with HMAC-obscured identifiers."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress

from app.modules.demo.errors import DemoError

_LIMIT_SCRIPT = """
local cooldown = redis.call('SET', KEYS[1], '1', 'NX', 'EX', ARGV[1])
if cooldown == false then return 0 end
local email_count = tonumber(redis.call('GET', KEYS[2]) or '0')
local ip_count = tonumber(redis.call('GET', KEYS[3]) or '0')
if email_count >= tonumber(ARGV[2]) or ip_count >= tonumber(ARGV[3]) then
  redis.call('DEL', KEYS[1])
  return -1
end
email_count = redis.call('INCR', KEYS[2])
if email_count == 1 then redis.call('EXPIRE', KEYS[2], ARGV[4]) end
ip_count = redis.call('INCR', KEYS[3])
if ip_count == 1 then redis.call('EXPIRE', KEYS[3], ARGV[4]) end
return 1
"""

_VERIFY_SCRIPT = """
local count = tonumber(redis.call('GET', KEYS[1]) or '0')
if count >= tonumber(ARGV[1]) then return 0 end
count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[2]) end
return 1
"""


class RedisDemoRateLimiter:
    """Apply 3/email + 10/IP requests, a 60s resend cooldown and 30/IP verifies."""

    def __init__(self, redis_client, *, hmac_key: bytes) -> None:
        if len(hmac_key) < 32:
            raise ValueError("rate limit HMAC key must contain at least 32 bytes")
        self.redis = redis_client
        self.hmac_key = hmac_key

    def check_request(self, email_key: str, client_ip: str) -> None:
        email_id = self._key("email", email_key)
        ip_id = self._key("ip", _canonical_ip(client_ip))
        result = self.redis.eval(
            _LIMIT_SCRIPT, 3,
            f"demo:otp:cooldown:{email_id}",
            f"demo:otp:email:{email_id}",
            f"demo:otp:request-ip:{ip_id}",
            60, 3, 10, 900,
        )
        self._check_result(result, cooldown=60)

    def check_verify(self, client_ip: str) -> None:
        ip_id = self._key("ip", _canonical_ip(client_ip))
        result = self.redis.eval(
            _VERIFY_SCRIPT, 1, f"demo:otp:verify-ip:{ip_id}", 30, 900,
        )
        self._check_result(result)

    def _key(self, kind: str, value: str) -> str:
        digest = hmac.new(
            self.hmac_key,
            f"tuwebmx-demo-rate:v1:{kind}:{value}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return digest

    @staticmethod
    def _check_result(result, cooldown: int | None = None) -> None:
        if result == 1:
            return
        if result == 0 or result == -1:
            retry_after = cooldown if result == 0 and cooldown is not None else 900
            raise DemoError(
                "rate_limited", "Demasiados intentos. Inténtalo más tarde.", 429,
                headers={"Retry-After": str(retry_after)},
            )
        raise DemoError("demo_unavailable", "El acceso a la demo no está disponible.", 503)


def _canonical_ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise DemoError("demo_unavailable", "No se pudo validar el origen de la solicitud.", 503) from exc
