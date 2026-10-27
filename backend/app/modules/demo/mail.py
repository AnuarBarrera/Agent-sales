"""Bounded Mailgun HTTPS transport; delivery errors never expose provider bodies."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from email.utils import parseaddr
from typing import Literal, Protocol

import httpx


class OtpSender(Protocol):
    def send_code(self, *, email: str, code: str, expires_in: int) -> None: ...


class MailDeliveryError(RuntimeError):
    """Redacted delivery failure. A timeout may have queued mail; never retry here."""

    def __init__(self, status_code: int | None = None):
        super().__init__("OTP email delivery failed")
        self.status_code = status_code


@dataclass(frozen=True)
class DemoMailConfig:
    domain: str
    api_key: str = field(repr=False)
    from_address: str
    region: Literal["us", "eu"] = "us"
    timeout: float = 10

    def __post_init__(self) -> None:
        labels = self.domain.split(".")
        if (len(labels) < 2 or len(self.domain) > 253
                or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels)):
            raise ValueError("Mailgun requires a valid sending domain")
        if not self.api_key.strip() or "\n" in self.api_key or "\r" in self.api_key:
            raise ValueError("Mailgun API credentials are required")
        address = parseaddr(self.from_address)[1]
        if (not address or "@" not in address or any(c in self.from_address for c in "\r\n")
                or not address.rsplit("@", 1)[1] or any(c.isspace() for c in address)):
            raise ValueError("Mailgun requires a valid sender")
        if self.region not in {"us", "eu"}:
            raise ValueError("Mailgun region must be us or eu")
        if not 0 < self.timeout <= 10:
            raise ValueError("Mailgun timeout must be at most ten seconds")

    @property
    def endpoint(self) -> str:
        host = "api.eu.mailgun.net" if self.region == "eu" else "api.mailgun.net"
        return f"https://{host}/v3/{self.domain}/messages"


class MailgunOtpSender:
    def __init__(self, config: DemoMailConfig):
        self.config = config

    def send_code(self, *, email: str, code: str, expires_in: int) -> None:
        if len(code) != 6 or not code.isascii() or not code.isdigit() or expires_in != 600:
            raise ValueError("invalid OTP delivery payload")
        fields = {
            "from": self.config.from_address,
            "to": email,
            "subject": "Tu código para la demo de Tu Web MX",
            "text": f"Tu código de acceso es {code}. Caduca en 10 minutos. "
                    "Si no solicitaste este acceso, puedes ignorar este mensaje.",
        }
        try:
            with httpx.Client(timeout=self.config.timeout, trust_env=False, follow_redirects=False) as client:
                response = client.post(
                    self.config.endpoint, auth=("api", self.config.api_key),
                    files={key: (None, value) for key, value in fields.items()},
                )
                if response.status_code != 200:
                    raise MailDeliveryError(response.status_code)
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("id"), str) or not payload["id"]:
                    raise MailDeliveryError()
        except (httpx.HTTPError, ValueError):
            raise MailDeliveryError() from None
