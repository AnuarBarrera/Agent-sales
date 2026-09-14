from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.security import verify_csrf_token
from app.modules.activities.router import router as activities_router
from app.modules.admin.router import router as admin_router
from app.modules.calls.router import router as calls_router
from app.modules.identity.router import router as identity_router
from app.modules.leads.router import router as leads_router
from app.modules.notifications.router import router as notifications_router
from app.modules.outreach.router import router as outreach_router
from app.modules.pipeline.router import router as pipeline_router
from app.modules.prospection.router import router as prospection_router
from app.modules.rag.router import router as rag_router
from app.modules.seller_profile.router import router as seller_profile_router


class HealthResponse(BaseModel):
    status: str


class ErrorEnvelope(BaseModel):
    error: dict


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup
    yield
    # Shutdown


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    openapi_url=f"{settings.api_prefix}/openapi.json",
    docs_url=f"{settings.api_prefix}/docs",
    redoc_url=f"{settings.api_prefix}/redoc",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def add_request_id(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    request_id = request.headers.get("X-Request-ID", str(UUID(int=0)))
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.middleware("http")
async def enforce_browser_csrf(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Protect cookie-authenticated state changes with the double-submit token."""
    mutating = request.method in {"POST", "PUT", "PATCH", "DELETE"}
    exempt = request.url.path.endswith(("/auth/login", "/auth/refresh", "/auth/accept-invitation"))
    if mutating and not exempt and request.cookies.get("access_token"):
        session_id = request.cookies.get("session_id")
        csrf_token = request.headers.get("X-CSRF-Token")
        if not session_id or not csrf_token or not verify_csrf_token(session_id, csrf_token):
            return JSONResponse(status_code=403, content={"detail": "CSRF token required"})
    return await call_next(request)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(UUID(int=0)))
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "Error interno del servidor",
                "request_id": request_id,
            }
        },
    )


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health_check() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get(f"{settings.api_prefix}/health", response_model=HealthResponse, tags=["health"])
async def api_health_check() -> HealthResponse:
    return HealthResponse(status="ok")


# Import and include routers here as they are created
app.include_router(identity_router, prefix=settings.api_prefix)
app.include_router(pipeline_router, prefix=settings.api_prefix)
app.include_router(prospection_router, prefix=settings.api_prefix)
app.include_router(leads_router, prefix=settings.api_prefix)
app.include_router(activities_router, prefix=settings.api_prefix)
app.include_router(admin_router, prefix=settings.api_prefix)
app.include_router(calls_router, prefix=settings.api_prefix)
app.include_router(notifications_router, prefix=settings.api_prefix)
app.include_router(outreach_router, prefix=settings.api_prefix)
app.include_router(rag_router, prefix=settings.api_prefix)
app.include_router(seller_profile_router, prefix=settings.api_prefix)
