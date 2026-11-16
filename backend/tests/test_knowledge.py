"""Conocimiento del negocio (bloque 4.4): fragmentos, embeddings y documentos.

OpenRouter se simula: FakeEmbedder hace de modelo y OpenRouterEmbedder se prueba con
respuestas falsas de la API. Ninguna prueba llama a OpenRouter de verdad.
"""

import json
import math

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.base import EMBEDDING_DIM
from app.db.session import tenant_session
from app.integrations.embeddings import (
    BATCH_SIZE,
    EmbeddingError,
    Embeddings,
    OpenRouterEmbedder,
    shorten,
)
from app.models import KnowledgeChunk, KnowledgeSource
from app.models.enums import ProcessingStatus
from app.services.knowledge import (
    CHARS_PER_TOKEN,
    KnowledgeError,
    delete_source,
    load_text,
    split_into_chunks,
)
from tests import test_bot_reply
from tests.factories import create_tenant

factory = test_bot_reply.factory

HORARIOS = "Abrimos de lunes a sábado de 7:00 a 19:00.\n\nLos domingos abrimos de 8:00 a 13:00."


class FakeEmbedder:
    model = "modelo-falso"

    def __init__(self, fail: EmbeddingError | None = None) -> None:
        self.fail = fail
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> Embeddings:
        self.calls.append(texts)
        if self.fail:
            raise self.fail
        # Un vector distinto por texto, ya de largo 1.
        vectors = [shorten([float(len(t) % 7 + 1), *([1.0] * (EMBEDDING_DIM - 1))]) for t in texts]
        return Embeddings(vectors, self.model, tokens=10 * len(texts))


@pytest.fixture
async def tenant_id(owner_engine: AsyncEngine):
    async with AsyncSession(owner_engine) as s, s.begin():
        return (await create_tenant(s, "Panaderia")).id


async def chunks(tenant_id, factory) -> list[KnowledgeChunk]:
    async with tenant_session(tenant_id, factory=factory) as s:
        return list(
            await s.scalars(
                select(KnowledgeChunk).order_by(
                    KnowledgeChunk.source_id, KnowledgeChunk.chunk_index
                )
            )
        )


async def sources(tenant_id, factory) -> list[KnowledgeSource]:
    async with tenant_session(tenant_id, factory=factory) as s:
        return list(await s.scalars(select(KnowledgeSource)))


# --- Partir el texto ---


def test_texto_corto_es_un_solo_fragmento() -> None:
    assert split_into_chunks(HORARIOS) == [HORARIOS]


def test_texto_largo_se_parte_por_parrafos_con_solape() -> None:
    paragraphs = [f"Párrafo {i}. " + "pan " * 100 for i in range(20)]  # ~500 caracteres c/u
    result = split_into_chunks("\n\n".join(paragraphs), chunk_tokens=500, overlap_tokens=150)
    assert len(result) > 1
    assert all(len(c) <= 500 * CHARS_PER_TOKEN for c in result)
    # Nada se pierde y ningún párrafo se corta por la mitad.
    joined = "\n\n".join(result)
    assert all(p.strip() in joined for p in paragraphs)
    # El segundo fragmento empieza con el último párrafo del primero.
    assert result[1].split("\n\n")[0] == result[0].split("\n\n")[-1]


def test_parrafo_gigante_se_parte_por_frases_y_palabras() -> None:
    giant = " ".join(["Esta es una frase de prueba."] * 400) + " " + "x" * 5000
    result = split_into_chunks(giant, chunk_tokens=100, overlap_tokens=0)
    assert all(len(c) <= 400 for c in result)
    # Sin solape no se repite ni se pierde nada.
    assert sum(c.count("x") for c in result) == 5000
    assert sum(c.count("frase") for c in result) == 400


# --- Cliente de OpenRouter ---


def test_recortar_a_1024_y_normalizar() -> None:
    vector = shorten([3.0, 4.0] + [0.0] * 4094)
    assert len(vector) == EMBEDDING_DIM
    assert vector[:2] == [0.6, 0.8]
    assert math.isclose(sum(x * x for x in vector), 1.0)
    with pytest.raises(EmbeddingError):
        shorten([1.0] * 10)


def api_reply(inputs: list[str], dim: int = 4096) -> dict:
    # La API puede devolverlos desordenados: cada uno trae su posición.
    data = [
        {"index": i, "embedding": [float(i + 1)] + [0.0] * (dim - 1)} for i in range(len(inputs))
    ]
    return {"data": list(reversed(data)), "model": "nvidia/nemotron", "usage": {"prompt_tokens": 7}}


async def test_cliente_de_openrouter() -> None:
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append({"body": body, "auth": request.headers["authorization"]})
        return httpx.Response(200, json=api_reply(body["input"]))

    embedder = OpenRouterEmbedder(
        "sk-or-prueba", "nvidia/nemotron", transport=httpx.MockTransport(handler)
    )
    texts = [f"texto {i}" for i in range(BATCH_SIZE + 2)]
    result = await embedder.embed(texts)
    await embedder.aclose()

    assert [len(c["body"]["input"]) for c in sent] == [BATCH_SIZE, 2]  # en tandas
    assert sent[0]["body"]["model"] == "nvidia/nemotron"
    assert sent[0]["auth"] == "Bearer sk-or-prueba"
    assert len(result.vectors) == len(texts) and result.tokens == 14
    assert all(len(v) == EMBEDDING_DIM for v in result.vectors)
    assert all(v[0] == 1.0 for v in result.vectors)  # normalizados y en su orden


async def test_openrouter_reintenta_fallos_pasajeros() -> None:
    answers = iter([httpx.Response(429, text="lento"), httpx.Response(200, json=api_reply(["a"]))])
    embedder = OpenRouterEmbedder(
        "k", "m", transport=httpx.MockTransport(lambda r: next(answers)), retry_base_seconds=0
    )
    assert len((await embedder.embed(["a"])).vectors) == 1
    await embedder.aclose()


@pytest.mark.parametrize(
    ("response", "permanent"),
    [
        (httpx.Response(401, text="clave mala"), True),
        (httpx.Response(404, text="no existe el modelo"), True),
        (httpx.Response(503, text="caído"), False),
        (httpx.Response(200, json={"error": {"message": "sin proveedor"}}), False),
        (httpx.Response(200, json={"data": []}), False),  # faltan embeddings
    ],
)
async def test_errores_de_openrouter(response: httpx.Response, permanent: bool) -> None:
    calls = []

    def handler(request):
        calls.append(request)
        return response

    embedder = OpenRouterEmbedder(
        "k", "m", transport=httpx.MockTransport(handler), retry_base_seconds=0
    )
    with pytest.raises(EmbeddingError) as info:
        await embedder.embed(["hola"])
    await embedder.aclose()
    assert info.value.permanent is permanent
    if permanent:
        assert len(calls) == 1  # no se reintenta lo que no tiene arreglo


# --- Guardar el conocimiento ---


async def test_cargar_texto(tenant_id, factory) -> None:
    embedder = FakeEmbedder()
    result = await load_text(tenant_id, "Horarios", HORARIOS, embedder=embedder, factory=factory)
    assert (result.status, result.chunk_count) == (ProcessingStatus.READY, 1)

    [source] = await sources(tenant_id, factory)
    assert (source.title, source.status, source.chunk_count) == (
        "Horarios",
        ProcessingStatus.READY,
        1,
    )
    [chunk] = await chunks(tenant_id, factory)
    assert chunk.content == HORARIOS and chunk.embedding_model == "modelo-falso"
    assert len(chunk.embedding) == EMBEDDING_DIM and chunk.token_count > 0

    # PostgreSQL calcula solo la búsqueda por palabras, en español ("domingos" = "domingo").
    async with tenant_session(tenant_id, factory=factory) as s:
        found = await s.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.tsv.op("@@")(func.plainto_tsquery("spanish", "domingo sábado")))
        )
    assert found == 1


async def test_el_mismo_texto_no_se_carga_dos_veces(tenant_id, factory) -> None:
    await load_text(tenant_id, "Horarios", HORARIOS, embedder=FakeEmbedder(), factory=factory)
    embedder = FakeEmbedder()
    result = await load_text(
        tenant_id, "Horarios", HORARIOS + "\n\n", embedder=embedder, factory=factory
    )
    assert result.already_loaded and embedder.calls == []
    assert len(await sources(tenant_id, factory)) == 1


async def test_mismo_titulo_reemplaza_al_anterior(tenant_id, factory) -> None:
    await load_text(tenant_id, "Horarios", HORARIOS, embedder=FakeEmbedder(), factory=factory)
    await load_text(
        tenant_id, "Precios", "Pan dulce: 2 $", embedder=FakeEmbedder(), factory=factory
    )
    nuevo = "Ahora abrimos todos los días de 6:00 a 20:00."
    result = await load_text(tenant_id, "Horarios", nuevo, embedder=FakeEmbedder(), factory=factory)
    assert result.replaced == 1
    assert sorted(c.content for c in await chunks(tenant_id, factory)) == [nuevo, "Pan dulce: 2 $"]


async def test_si_openrouter_falla_queda_failed_y_se_puede_reintentar(tenant_id, factory) -> None:
    result = await load_text(
        tenant_id,
        "Horarios",
        HORARIOS,
        embedder=FakeEmbedder(fail=EmbeddingError("OpenRouter respondió 503")),
        factory=factory,
    )
    assert result.status == ProcessingStatus.FAILED
    [source] = await sources(tenant_id, factory)
    assert (source.status, source.error) == (ProcessingStatus.FAILED, "OpenRouter respondió 503")
    assert await chunks(tenant_id, factory) == []

    retry = await load_text(
        tenant_id, "Horarios", HORARIOS, embedder=FakeEmbedder(), factory=factory
    )
    assert (retry.source_id, retry.status) == (source.id, ProcessingStatus.READY)
    assert len(await chunks(tenant_id, factory)) == 1


async def test_textos_invalidos(tenant_id, factory) -> None:
    for title, body in (("Vacío", "  \n\n "), ("", HORARIOS)):
        with pytest.raises(KnowledgeError):
            await load_text(tenant_id, title, body, embedder=FakeEmbedder(), factory=factory)


async def test_un_negocio_no_ve_el_conocimiento_de_otro(tenant_id, factory, owner_engine) -> None:
    await load_text(tenant_id, "Horarios", HORARIOS, embedder=FakeEmbedder(), factory=factory)
    async with AsyncSession(owner_engine) as s, s.begin():
        other = (await create_tenant(s, "Otro")).id
    # El otro negocio puede cargar el mismo texto (el checksum es único por negocio)...
    result = await load_text(other, "Horarios", HORARIOS, embedder=FakeEmbedder(), factory=factory)
    assert not result.already_loaded
    # ...y cada uno ve solo lo suyo; borrar en uno no toca al otro.
    async with tenant_session(other, factory=factory) as s:
        assert await delete_source(s, "Horarios") == 1
    assert len(await chunks(tenant_id, factory)) == 1
    assert await chunks(other, factory) == []


async def test_borrar_documento_borra_sus_fragmentos(tenant_id, factory) -> None:
    await load_text(tenant_id, "Horarios", HORARIOS, embedder=FakeEmbedder(), factory=factory)
    async with tenant_session(tenant_id, factory=factory) as s:
        assert await delete_source(s, "Horarios") == 1
        assert await s.scalar(text("SELECT count(*) FROM knowledge_chunks")) == 0
