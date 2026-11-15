"""Registrar un número: Evolution y la base de Saint quedan de acuerdo."""

import json
import uuid
from collections.abc import Iterator

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.security import decrypt, verify_secret
from app.db.session import tenant_session
from app.integrations.evolution import EvolutionClient
from app.models import ChannelInstance
from app.models.enums import InstanceStatus
from app.services.channel_registration import (
    WEBHOOK_SECRET_HEADER,
    refresh_connection_status,
    register_whatsapp_number,
)
from tests.factories import create_tenant

QR_PNG = "data:image/png;base64,iVBORw0KGgo="
SETTINGS = Settings(public_webhook_base_url="http://saint-api:8000/")


@pytest.fixture(autouse=True)
def fernet_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("FERNET_KEY", key)
    get_settings.cache_clear()
    yield key
    get_settings.cache_clear()


class FakeEvolution:
    """Responde como Evolution v2.3.7 y recuerda lo que le pidieron."""

    def __init__(self, state: str = "connecting") -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.state = state

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        path = request.url.path
        if path == "/instance/create":
            return httpx.Response(
                201,
                json={
                    "instance": {"instanceName": body["instanceName"], "status": "connecting"},
                    "hash": body["token"],
                    "qrcode": {"code": "2@x", "base64": QR_PNG, "count": 1},
                },
            )
        if path.startswith("/instance/connectionState/"):
            return httpx.Response(
                200, json={"instance": {"instanceName": "x", "state": self.state}}
            )
        if path == "/instance/fetchInstances":
            return httpx.Response(
                200, json=[{"name": "x", "ownerJid": "584121234567@s.whatsapp.net"}]
            )
        if path.startswith("/instance/delete/"):
            return httpx.Response(200, json={"status": "SUCCESS"})
        return httpx.Response(404, json={"response": {"message": ["no simulado"]}})

    def client(self) -> EvolutionClient:
        return EvolutionClient("http://evolution:8080", "k", transport=httpx.MockTransport(self))


@pytest.fixture
def factory(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(app_engine, expire_on_commit=False)


async def new_tenant(owner_engine: AsyncEngine) -> uuid.UUID:
    async with AsyncSession(owner_engine) as s, s.begin():
        return (await create_tenant(s, "Registro")).id


async def test_registrar_numero(owner_engine: AsyncEngine, factory, fernet_key: str) -> None:
    tenant_id = await new_tenant(owner_engine)
    fake = FakeEvolution()
    async with fake.client() as evo:
        registered = await register_whatsapp_number(
            evo, tenant_id, settings=SETTINGS, factory=factory
        )

    # Lo que se le pidió a Evolution.
    method, path, body = fake.calls[0]
    assert (method, path) == ("POST", "/instance/create")
    webhook = body["webhook"]
    assert webhook["url"] == f"http://saint-api:8000/webhooks/evolution/{registered.public_id}"
    assert "MESSAGES_UPSERT" in webhook["events"]
    assert webhook["byEvents"] is False and webhook["base64"] is False
    secret_sent = webhook["headers"][WEBHOOK_SECRET_HEADER]
    assert body["groupsIgnore"] is True

    # Lo que quedó en la base (leído con el usuario de la app, con RLS).
    async with tenant_session(tenant_id, factory=factory) as s:
        row = await s.scalar(
            select(ChannelInstance).where(ChannelInstance.id == registered.channel_instance_id)
        )
    assert row.public_id == registered.public_id
    assert row.evolution_instance_name == registered.evolution_instance_name
    assert row.status == InstanceStatus.QR_PENDING
    assert row.debounce_seconds == 15 and row.groups_enabled is False
    # Token: cifrado, pero recuperable con FERNET_KEY y es el que se mandó a Evolution.
    assert decrypt(row.instance_token_encrypted) == body["token"]
    assert body["token"].encode() not in row.instance_token_encrypted
    # Secreto: solo su huella; coincide con lo que Evolution mandará en la cabecera.
    assert secret_sent not in row.webhook_secret_hash
    assert verify_secret(secret_sent, row.webhook_secret_hash)
    assert registered.qr is not None


async def test_si_falla_la_base_se_borra_de_evolution(factory) -> None:
    fake = FakeEvolution()
    # Un negocio que no existe: la FK de tenant_id hace fallar el INSERT.
    async with fake.client() as evo:
        with pytest.raises(IntegrityError):
            await register_whatsapp_number(evo, uuid.uuid4(), settings=SETTINGS, factory=factory)
    created_name = fake.calls[0][2]["instanceName"]
    assert ("DELETE", f"/instance/delete/{created_name}", None) in fake.calls


async def test_estado_al_conectar(owner_engine: AsyncEngine, factory) -> None:
    tenant_id = await new_tenant(owner_engine)
    fake = FakeEvolution(state="connecting")
    async with fake.client() as evo:
        registered = await register_whatsapp_number(
            evo, tenant_id, settings=SETTINGS, factory=factory
        )
        cid = registered.channel_instance_id
        # Mientras no escanean, sigue esperando el QR.
        assert (
            await refresh_connection_status(evo, tenant_id, cid, factory=factory)
            == InstanceStatus.QR_PENDING
        )
        fake.state = "open"
        assert (
            await refresh_connection_status(evo, tenant_id, cid, factory=factory)
            == InstanceStatus.OPEN
        )

    async with tenant_session(tenant_id, factory=factory) as s:
        row = await s.get(ChannelInstance, cid)
    assert row.status == InstanceStatus.OPEN
    assert row.display_identifier == "+584121234567"
    assert row.last_connected_at is not None


async def test_otro_negocio_no_puede_ver_el_numero(owner_engine: AsyncEngine, factory) -> None:
    tenant_a = await new_tenant(owner_engine)
    tenant_b = await new_tenant(owner_engine)
    async with FakeEvolution().client() as evo:
        registered = await register_whatsapp_number(
            evo, tenant_a, settings=SETTINGS, factory=factory
        )
        with pytest.raises(LookupError):
            await refresh_connection_status(
                evo, tenant_b, registered.channel_instance_id, factory=factory
            )
