"""Cliente de embeddings (OpenRouter, modelo Nemotron de NVIDIA).

Un embedding es una lista de números que representa el significado de un texto: dos
textos que dicen lo mismo con otras palabras quedan "cerca". Con eso, en el bloque 4.5,
el bot encontrará el fragmento del negocio que responde a la pregunta del cliente.

El modelo devuelve 4096 números por texto, pero la base guarda 1024 (vector(1024)).
Nemotron está entrenado para que se pueda cortar: nos quedamos con los primeros 1024 y
los volvemos a normalizar (largo 1), que es lo que espera la distancia coseno.

Ojo: el modelo gratuito (":free") guarda los textos que se le mandan. Solo sirve para
información pública del negocio (horarios, productos, precios), nunca datos privados.

Las pruebas usan un cliente falso con el mismo método `embed`, sin llamar a OpenRouter.
"""

import asyncio
import math
from dataclasses import dataclass
from typing import Any, Protocol, Self

import httpx

from app.core.config import Settings, get_settings
from app.db.base import EMBEDDING_DIM

API_URL = "https://openrouter.ai/api/v1/embeddings"
# Textos por petición: menos peticiones, sin pasarse del tamaño que acepta la API.
BATCH_SIZE = 16
TIMEOUT = httpx.Timeout(60.0, connect=10.0)
# Fallos pasajeros (límite de uso, servidor caído): se reintenta tras 2 s y 4 s.
ATTEMPTS = 3
RETRY_BASE_SECONDS = 2.0


class EmbeddingError(Exception):
    """OpenRouter no devolvió los embeddings."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        # permanent: repetir no sirve (clave mal puesta, modelo que no existe).
        self.permanent = permanent


@dataclass(frozen=True)
class Embeddings:
    vectors: list[list[float]]  # uno por texto, en el mismo orden, de EMBEDDING_DIM números
    model: str
    tokens: int  # tokens que contó OpenRouter (para costes; hoy el modelo es gratis)


class Embedder(Protocol):
    model: str

    async def embed(self, texts: list[str]) -> Embeddings: ...


def shorten(vector: list[float], dim: int = EMBEDDING_DIM) -> list[float]:
    """Se queda con los primeros `dim` números y los deja con largo 1."""
    if len(vector) < dim:
        raise EmbeddingError(f"El modelo devolvió {len(vector)} números; hacen falta {dim}")
    head = vector[:dim]
    norm = math.sqrt(sum(x * x for x in head))
    if norm == 0:
        raise EmbeddingError("El modelo devolvió un vector vacío (todo ceros)")
    return [x / norm for x in head]


class OpenRouterEmbedder:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_base_seconds: float = RETRY_BASE_SECONDS,
    ) -> None:
        if not api_key:
            raise ValueError("Falta OPENROUTER_API_KEY en el .env")
        self.model = model
        self._retry_base = retry_base_seconds
        self._http = httpx.AsyncClient(
            timeout=TIMEOUT,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}", "X-Title": "Saint"},
        )

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> Self | None:
        """None si no hay clave."""
        settings = settings or get_settings()
        key = settings.openrouter_api_key.get_secret_value()
        return cls(key, settings.embedding_model) if key else None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def embed(self, texts: list[str]) -> Embeddings:
        vectors: list[list[float]] = []
        tokens = 0
        model = self.model
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start : start + BATCH_SIZE]
            body = await self._post({"model": self.model, "input": batch})
            data = body.get("data")
            if not isinstance(data, list) or len(data) != len(batch):
                raise EmbeddingError(
                    f"Se pidieron {len(batch)} embeddings y llegaron "
                    f"{len(data) if isinstance(data, list) else 0}"
                )
            # La API indica la posición de cada uno; no se confía en el orden de llegada.
            for item in sorted(data, key=lambda d: d.get("index", 0)):
                vectors.append(shorten(item["embedding"]))
            tokens += (body.get("usage") or {}).get("prompt_tokens") or 0
            model = body.get("model") or model
        return Embeddings(vectors=vectors, model=model, tokens=tokens)

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(1, ATTEMPTS + 1):
            try:
                response = await self._http.post(API_URL, json=payload)
            except httpx.HTTPError as exc:
                error = EmbeddingError(f"OpenRouter no respondió: {exc!r}")
            else:
                if response.status_code == 200:
                    body = response.json()
                    # OpenRouter a veces responde 200 con un error dentro.
                    if "error" not in body:
                        return body
                    error = EmbeddingError(f"OpenRouter respondió con error: {body['error']}")
                else:
                    status = response.status_code
                    error = EmbeddingError(
                        f"OpenRouter respondió {status}: {response.text[:300]}",
                        # 4xx salvo 408 (tiempo) y 429 (límite de uso): repetir no sirve.
                        permanent=400 <= status < 500 and status not in (408, 429),
                    )
            if error.permanent or attempt == ATTEMPTS:
                raise error
            await asyncio.sleep(self._retry_base * 2 ** (attempt - 1))
        raise AssertionError("inalcanzable")
