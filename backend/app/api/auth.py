"""Rutas de inicio de sesión del panel: /auth/...

- POST /auth/login           correo + contraseña -> pase de acceso + cookie de renovación
- POST /auth/refresh         cookie -> pase de acceso nuevo (y cookie nueva)
- POST /auth/select-tenant   elegir con qué negocio trabajar -> pase con ese negocio
- GET  /auth/me              quién soy, mis negocios y el negocio activo
- POST /auth/logout          cierra la sesión de este navegador

El pase de acceso va en la respuesta (el panel lo guarda en memoria y lo manda en la
cabecera Authorization: Bearer ...). El de renovación va en una cookie HttpOnly: el
JavaScript del panel no puede leerla, así que un script malicioso no puede robarla.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.webhooks import get_session_factory
from app.core.auth_tokens import (
    AccessClaims,
    InvalidTokenError,
    create_access_token,
    decode_access_token,
)
from app.core.config import get_settings
from app.models import User
from app.services import auth
from app.services.auth import TenantAccess

router = APIRouter(prefix="/auth", tags=["inicio de sesión"])

REFRESH_COOKIE = "saint_refresh"
# La cookie solo viaja a las rutas /auth: el resto de la API nunca la recibe.
REFRESH_COOKIE_PATH = "/auth"
WRONG_CREDENTIALS = "Correo o contraseña incorrectos"
SESSION_EXPIRED = "Tu sesión terminó. Vuelve a iniciar sesión."

bearer = HTTPBearer(auto_error=False)
Factory = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


# --- Lo que recibe y devuelve la API ---


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)
    # false: la cookie se borra al cerrar el navegador (igual dura 7 días como máximo).
    remember: bool = True


class SelectTenantIn(BaseModel):
    tenant_id: uuid.UUID


class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    role: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str


class SessionOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserOut
    tenants: list[TenantOut]
    # Si la persona tiene un solo negocio, ya queda elegido; con varios, el panel muestra
    # la pantalla de elegir negocio y llama a /auth/select-tenant.
    active_tenant: TenantOut | None


# --- Ayudantes ---


def _tenant_out(tenant: TenantAccess) -> TenantOut:
    return TenantOut(id=tenant.id, name=tenant.name, slug=tenant.slug, role=tenant.role.value)


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _set_refresh_cookie(response: Response, session: auth.NewSession, remember: bool) -> None:
    # La cookie lleva delante "1." o "0." para recordar, al renovar, si la persona marcó
    # "Mantener la sesión iniciada". El pase de verdad es lo que va después del punto.
    response.set_cookie(
        REFRESH_COOKIE,
        f"{1 if remember else 0}.{session.refresh_token}",
        expires=session.expires_at if remember else None,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        # En producción solo por HTTPS; en desarrollo la API corre en http://127.0.0.1.
        secure=get_settings().app_env != "development",
        samesite="strict",
    )


def _read_refresh_cookie(value: str | None) -> tuple[str, bool] | None:
    """(pase, recordar) a partir de la cookie, o None si no hay o está mal formada."""
    flag, _, token = (value or "").partition(".")
    if flag not in ("0", "1") or not token:
        return None
    return token, flag == "1"


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)


async def _session_out(
    user: User, tenants: list[TenantAccess], active: TenantAccess | None
) -> SessionOut:
    token, expires_at = create_access_token(
        user.id, active.id if active else None, active.role.value if active else None
    )
    return SessionOut(
        access_token=token,
        expires_at=expires_at,
        user=UserOut(id=user.id, email=user.email, full_name=user.full_name),
        tenants=[_tenant_out(t) for t in tenants],
        active_tenant=_tenant_out(active) if active else None,
    )


async def current_claims(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AccessClaims:
    """Dependencia para las rutas del panel: exige un pase de acceso válido."""
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Falta iniciar sesión")
    try:
        return decode_access_token(credentials.credentials)
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, SESSION_EXPIRED) from exc


async def current_tenant_claims(
    claims: Annotated[AccessClaims, Depends(current_claims)],
) -> AccessClaims:
    """Como current_claims, pero además exige haber elegido un negocio."""
    if claims.tenant_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Primero elige un negocio")
    return claims


# --- Rutas ---


@router.post("/login", response_model=SessionOut)
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    factory: Factory,
    redis: Annotated[Redis, Depends(get_redis)],
) -> SessionOut:
    limiter = auth.LoginLimiter(redis)
    ip = _client_ip(request)
    try:
        await limiter.check(body.email, ip)
        session = await auth.login(
            body.email,
            body.password,
            user_agent=request.headers.get("user-agent"),
            ip=ip,
            factory=factory,
        )
    except auth.TooManyAttemptsError as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Hubo muchos intentos seguidos. Vuelve a probar en unos minutos.",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except auth.InvalidCredentialsError as exc:
        await limiter.failed(body.email, ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, WRONG_CREDENTIALS) from exc
    await limiter.succeeded(body.email)

    tenants = await auth.tenants_for(session.user.id, factory=factory)
    _set_refresh_cookie(response, session, body.remember)
    return await _session_out(session.user, tenants, tenants[0] if len(tenants) == 1 else None)


@router.post("/refresh", response_model=SessionOut)
async def refresh(
    request: Request,
    response: Response,
    factory: Factory,
    body: SelectTenantIn | None = None,
    saint_refresh: Annotated[str | None, Cookie()] = None,
) -> SessionOut | JSONResponse:
    """Pase nuevo sin contraseña. Opcional: tenant_id para seguir en el mismo negocio."""
    cookie = _read_refresh_cookie(saint_refresh)
    if cookie is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, SESSION_EXPIRED)
    token, remember = cookie
    try:
        session = await auth.refresh(
            token,
            user_agent=request.headers.get("user-agent"),
            ip=_client_ip(request),
            factory=factory,
        )
    except auth.InvalidSessionError:
        # Se borra la cookie que ya no sirve, para que el panel no la siga mandando.
        expired = JSONResponse(status_code=401, content={"detail": SESSION_EXPIRED})
        _clear_refresh_cookie(expired)
        return expired

    tenants = await auth.tenants_for(session.user.id, factory=factory)
    active = None
    if body is not None:
        active = next((t for t in tenants if t.id == body.tenant_id), None)
    elif len(tenants) == 1:
        active = tenants[0]
    _set_refresh_cookie(response, session, remember)
    return await _session_out(session.user, tenants, active)


@router.post("/select-tenant", response_model=SessionOut)
async def select_tenant(
    body: SelectTenantIn,
    claims: Annotated[AccessClaims, Depends(current_claims)],
    factory: Factory,
) -> SessionOut:
    user = await auth.get_active_user(claims.user_id, factory=factory)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, SESSION_EXPIRED)
    tenants = await auth.tenants_for(user.id, factory=factory)
    active = next((t for t in tenants if t.id == body.tenant_id), None)
    if active is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No tienes acceso a ese negocio")
    return await _session_out(user, tenants, active)


class MeOut(BaseModel):
    user: UserOut
    tenants: list[TenantOut]
    active_tenant: TenantOut | None


@router.get("/me", response_model=MeOut)
async def me(claims: Annotated[AccessClaims, Depends(current_claims)], factory: Factory) -> MeOut:
    user = await auth.get_active_user(claims.user_id, factory=factory)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, SESSION_EXPIRED)
    tenants = await auth.tenants_for(user.id, factory=factory)
    # El rol se lee de la base, no del pase: si se lo cambiaron hace un minuto, ya cuenta.
    active = next((t for t in tenants if t.id == claims.tenant_id), None)
    return MeOut(
        user=UserOut(id=user.id, email=user.email, full_name=user.full_name),
        tenants=[_tenant_out(t) for t in tenants],
        active_tenant=_tenant_out(active) if active else None,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    factory: Factory,
    saint_refresh: Annotated[str | None, Cookie()] = None,
) -> Response:
    cookie = _read_refresh_cookie(saint_refresh)
    if cookie is not None:
        await auth.logout(cookie[0], factory=factory)
    _clear_refresh_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
