from __future__ import annotations

from fastapi import HTTPException


class DemoError(HTTPException):
    """Stable structured API error used by demo services and routers."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 409,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        super().__init__(
            status_code=status_code,
            detail={"code": code, "message": message},
            headers=headers,
        )
