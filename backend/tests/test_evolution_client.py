"""Pruebas del cliente de Evolution API.

No levantan Evolution: un transporte simulado (MockTransport) responde como lo hace
Evolution v2.3.7 y comprueba qué le pide nuestro cliente.
"""

import json

import httpx
import pytest

from app.integrations.evolution import EvolutionClient, EvolutionError

QR_PNG = "data:image/png;base64,iVBORw0KGgo="


def client_with(handler) -> EvolutionClient:
    return EvolutionClient(
        "http://evolution:8080/", "clave-maestra", transport=httpx.MockTransport(handler)
    )


async def test_crear_instancia_envia_lo_correcto_y_devuelve_el_qr() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"], seen["path"] = request.method, request.url.path
        seen["apikey"] = request.headers["apikey"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "instance": {
                    "instanceName": "saint-a",
                    "instanceId": "f1c2",
                    "integration": "WHATSAPP-BAILEYS",
                    "status": "connecting",
                },
                "hash": seen["body"]["token"],
                "qrcode": {"pairingCode": None, "code": "2@abc", "base64": QR_PNG, "count": 1},
            },
        )

    async with client_with(handler) as evo:
        created = await evo.create_instance("saint-a")

    assert (seen["method"], seen["path"]) == ("POST", "/instance/create")
    assert seen["apikey"] == "clave-maestra"
    assert seen["body"]["integration"] == "WHATSAPP-BAILEYS"
    assert seen["body"]["qrcode"] is True
    assert seen["body"]["groupsIgnore"] is True  # grupos apagados por defecto
    assert len(seen["body"]["token"]) >= 40  # token aleatorio largo
    assert created.token == seen["body"]["token"]
    assert created.instance_id == "f1c2"
    assert created.qr is not None and created.qr.base64 == QR_PNG


async def test_grupos_encendidos_si_se_pide() -> None:
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(201, json={"instance": {"instanceName": "x"}})

    async with client_with(handler) as evo:
        created = await evo.create_instance("x", groups_enabled=True)
    assert bodies[0]["groupsIgnore"] is False
    assert created.qr is None


async def test_pedir_qr() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/instance/connect/saint-a"
        assert request.url.params.get("number") == "584121234567"
        return httpx.Response(
            200, json={"pairingCode": "WZYEH1YY", "code": "2@x", "base64": QR_PNG, "count": 2}
        )

    async with client_with(handler) as evo:
        qr = await evo.get_qr("saint-a", phone_number="584121234567")
    assert qr is not None
    assert qr.pairing_code == "WZYEH1YY"
    assert qr.count == 2


async def test_pedir_qr_si_ya_esta_conectado_devuelve_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"instance": {"instanceName": "a", "state": "open"}})

    async with client_with(handler) as evo:
        assert await evo.get_qr("a") is None


@pytest.mark.parametrize("state", ["open", "connecting", "close"])
async def test_estado_de_conexion(state: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/instance/connectionState/a"
        return httpx.Response(200, json={"instance": {"instanceName": "a", "state": state}})

    async with client_with(handler) as evo:
        assert await evo.connection_state("a") == state


async def test_estado_desconocido_es_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"instance": {"instanceName": "a", "state": None}})

    async with client_with(handler) as evo:
        with pytest.raises(EvolutionError, match="Estado desconocido"):
            await evo.connection_state("a")


async def test_borrar_y_desvincular() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"status": "SUCCESS", "error": False})

    async with client_with(handler) as evo:
        await evo.logout("a")
        await evo.delete_instance("a")
    assert calls == [("DELETE", "/instance/logout/a"), ("DELETE", "/instance/delete/a")]


async def test_el_nombre_se_codifica_en_la_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path == b"/instance/connectionState/a%2Fb%20c"
        return httpx.Response(200, json={"instance": {"instanceName": "a/b c", "state": "close"}})

    async with client_with(handler) as evo:
        await evo.connection_state("a/b c")


async def test_error_de_evolution_trae_su_mensaje_y_codigo() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "status": 403,
                "error": "Forbidden",
                "response": {"message": ['This name "a" is already in use.']},
            },
        )

    async with client_with(handler) as evo:
        with pytest.raises(EvolutionError, match="already in use") as info:
            await evo.create_instance("a")
    assert info.value.status_code == 403


async def test_evolution_apagado() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    async with client_with(handler) as evo:
        with pytest.raises(EvolutionError, match="No se pudo contactar") as info:
            await evo.connection_state("a")
    assert info.value.status_code is None


def test_sin_clave_no_arranca() -> None:
    with pytest.raises(ValueError, match="EVOLUTION_API_KEY"):
        EvolutionClient("http://evolution:8080", "")
