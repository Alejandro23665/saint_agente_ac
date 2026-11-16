"""Cliente del modelo de IA (Claude, de Anthropic).

Saint solo necesita una cosa del modelo: "con estas instrucciones y esta conversación,
escribe la respuesta". Esta clase lo hace y devuelve también los tokens usados, que se
guardan en llm_runs para calcular costes.

Las pruebas usan un cliente falso con el mismo método `reply`, sin llamar a Anthropic.
"""

import time
from dataclasses import dataclass
from typing import Any, Protocol, Self

import anthropic

from app.core.config import Settings, get_settings

# Una respuesta de WhatsApp es corta: 600 tokens son unas 400 palabras.
MAX_OUTPUT_TOKENS = 600
TIMEOUT_SECONDS = 30.0


class LLMError(Exception):
    """El modelo no respondió o respondió con error."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        # permanent: no tiene sentido reintentar (clave mal puesta, petición inválida).
        self.permanent = permanent


@dataclass(frozen=True)
class LLMReply:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    latency_ms: int
    stop_reason: str | None


class LLMClient(Protocol):
    async def reply(
        self, *, model: str, system: str, messages: list[dict[str, Any]]
    ) -> LLMReply: ...


class AnthropicLLM:
    def __init__(self, api_key: str, **kwargs: Any) -> None:
        if not api_key:
            raise ValueError("Falta ANTHROPIC_API_KEY en el .env")
        # max_retries=2: el SDK reintenta solo los fallos pasajeros (429, 5xx, conexión).
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=2, **kwargs
        )

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> Self | None:
        """None si no hay clave: entonces el bot contesta con el eco del bloque 4.2."""
        settings = settings or get_settings()
        key = settings.anthropic_api_key.get_secret_value()
        return cls(key) if key else None

    async def aclose(self) -> None:
        await self._client.close()

    async def reply(self, *, model: str, system: str, messages: list[dict[str, Any]]) -> LLMReply:
        started = time.monotonic()
        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system,
                messages=messages,
                # Respuestas de atención al cliente: rápidas y baratas, sin "pensar" antes.
                thinking={"type": "disabled"},
                output_config={"effort": "low"},
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise LLMError(f"Anthropic rechazó la clave: {exc}", permanent=True) from exc
        except anthropic.BadRequestError as exc:
            raise LLMError(f"Petición inválida para Anthropic: {exc}", permanent=True) from exc
        except anthropic.APIError as exc:
            raise LLMError(f"Anthropic no respondió bien: {exc}") from exc

        text = "".join(block.text for block in response.content if block.type == "text")
        usage = response.usage
        return LLMReply(
            text=text.strip(),
            model=response.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_tokens=usage.cache_read_input_tokens or 0,
            latency_ms=int((time.monotonic() - started) * 1000),
            stop_reason=response.stop_reason,
        )
