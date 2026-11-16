"""Rutas de los números de WhatsApp del negocio activo: /numbers/...

- GET    /numbers                    los números y su estado
- POST   /numbers                    conectar un número nuevo -> primer QR
- GET    /numbers/{id}               estado actualizado (pregunta a Evolution)
- POST   /numbers/{id}/qr            otro QR (o código de vinculación con el teléfono)
- PATCH  /numbers/{id}               encender o apagar los mensajes de grupos
- POST   /numbers/{id}/disconnect    desvincular el teléfono
- DELETE /numbers/{id}               borrar el número (solo el dueño)

Qué puede hacer cada rol: ver services/numbers.py.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.auth import Factory, client_ip, current_member
from app.integrations.evolution import EvolutionClient, EvolutionError
from app.services import members, numbers
from app.services.members import Member

router = APIRouter(prefix="/numbers", tags=["números de WhatsApp"])

CurrentMember = Annotated[Member, Depends(current_member)]
EVOLUTION_DOWN = "No se pudo hablar con WhatsApp en este momento. Intenta de nuevo en un rato."


async def get_evolution() -> AsyncIterator[EvolutionClient]:
    """Un cliente de Evolution por petición; las pruebas lo cambian por uno simulado."""
    try:
        client = EvolutionClient.from_settings()
    except ValueError as exc:  # falta EVOLUTION_API_KEY
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, EVOLUTION_DOWN) from exc
    async with client:
        yield client


Evolution = Annotated[EvolutionClient, Depends(get_evolution)]


# --- Lo que recibe y devuelve la API ---


class NumberOut(BaseModel):
    id: uuid.UUID
    phone: str | None
    # created | qr_pending | connecting | open | close | banned
    status: str
    groups_enabled: bool
    last_connected_at: datetime | None
    created_at: datetime


class QrOut(BaseModel):
    image: str | None  # data:image/png;base64,... lista para un <img>
    pairing_code: str | None  # 8 letras para escribir en el teléfono


class NumberWithQrOut(BaseModel):
    number: NumberOut
    qr: QrOut | None  # None: ya está conectado


class ConnectIn(BaseModel):
    groups_enabled: bool = False


class QrIn(BaseModel):
    # Opcional: con el teléfono, además del QR llega un código para vincular sin cámara.
    phone: str | None = Field(default=None, max_length=30)


class NumberUpdateIn(BaseModel):
    groups_enabled: bool


# --- Ayudantes ---


def _out(number: numbers.NumberInfo) -> NumberOut:
    return NumberOut(
        id=number.id,
        phone=number.phone,
        status=number.status.value,
        groups_enabled=number.groups_enabled,
        last_connected_at=number.last_connected_at,
        created_at=number.created_at,
    )


def _with_qr(result: numbers.NumberWithQr) -> NumberWithQrOut:
    qr = None
    if result.qr is not None:
        qr = QrOut(image=result.qr.base64, pairing_code=result.qr.pairing_code)
    return NumberWithQrOut(number=_out(result.number), qr=qr)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, EvolutionError):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, EVOLUTION_DOWN)
    if isinstance(exc, members.PermissionDeniedError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, members.ConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, members.InvalidInputError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    return HTTPException(status.HTTP_404_NOT_FOUND, "Ese número no existe en este negocio")


ERRORS = (
    EvolutionError,
    members.PermissionDeniedError,
    members.ConflictError,
    members.InvalidInputError,
    members.NotFoundError,
)


# --- Rutas ---


@router.get("", response_model=list[NumberOut])
async def list_numbers(member: CurrentMember, factory: Factory) -> list[NumberOut]:
    return [_out(n) for n in await numbers.list_numbers(member, factory=factory)]


@router.post("", response_model=NumberWithQrOut, status_code=status.HTTP_201_CREATED)
async def connect_number(
    body: ConnectIn, member: CurrentMember, factory: Factory, evo: Evolution, request: Request
) -> NumberWithQrOut:
    try:
        result = await numbers.connect_number(
            member, evo, groups_enabled=body.groups_enabled, ip=client_ip(request), factory=factory
        )
    except ERRORS as exc:
        raise _http_error(exc) from exc
    return _with_qr(result)


@router.get("/{number_id}", response_model=NumberOut)
async def get_number(
    number_id: uuid.UUID, member: CurrentMember, factory: Factory, evo: Evolution
) -> NumberOut:
    try:
        return _out(await numbers.refresh_number(member, evo, number_id, factory=factory))
    except ERRORS as exc:
        raise _http_error(exc) from exc


@router.post("/{number_id}/qr", response_model=NumberWithQrOut)
async def new_qr(
    number_id: uuid.UUID, body: QrIn, member: CurrentMember, factory: Factory, evo: Evolution
) -> NumberWithQrOut:
    try:
        result = await numbers.new_qr(member, evo, number_id, phone=body.phone, factory=factory)
    except ERRORS as exc:
        raise _http_error(exc) from exc
    return _with_qr(result)


@router.patch("/{number_id}", response_model=NumberOut)
async def update_number(
    number_id: uuid.UUID,
    body: NumberUpdateIn,
    member: CurrentMember,
    factory: Factory,
    evo: Evolution,
    request: Request,
) -> NumberOut:
    try:
        result = await numbers.set_groups(
            member, evo, number_id, body.groups_enabled, ip=client_ip(request), factory=factory
        )
    except ERRORS as exc:
        raise _http_error(exc) from exc
    return _out(result)


@router.post("/{number_id}/disconnect", response_model=NumberOut)
async def disconnect_number(
    number_id: uuid.UUID, member: CurrentMember, factory: Factory, evo: Evolution, request: Request
) -> NumberOut:
    try:
        result = await numbers.disconnect_number(
            member, evo, number_id, ip=client_ip(request), factory=factory
        )
    except ERRORS as exc:
        raise _http_error(exc) from exc
    return _out(result)


@router.delete("/{number_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_number(
    number_id: uuid.UUID, member: CurrentMember, factory: Factory, evo: Evolution, request: Request
) -> Response:
    try:
        await numbers.delete_number(member, evo, number_id, ip=client_ip(request), factory=factory)
    except ERRORS as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
