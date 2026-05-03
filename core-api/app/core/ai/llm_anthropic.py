"""Anthropic LLM implementation (Claude Sonnet 4.6 default S7).

Costo Sonnet 4.6 (a 2026): $3/M input, $15/M output.
Max output tokens default S7: 2048 (caption generation, brief copy).

Pattern: implementa `LLMProtocol` via duck typing (PEP 544 structural typing).
"""

from __future__ import annotations

import time

import structlog
from anthropic import APIError, AsyncAnthropic, AuthenticationError, RateLimitError

from app.core.ai.llm import LLMError, LLMResponse

log = structlog.get_logger(__name__)


class AnthropicLLM:
    """Anthropic LLM per Claude (default `claude-sonnet-4-6` S7).

    Configura un `AsyncAnthropic` client persistente (connection pool managed
    dal SDK). Singleton via `get_llm()` factory.
    """

    def __init__(self, api_key: str, model: str) -> None:
        if not api_key:
            raise LLMError(
                "ANTHROPIC_API_KEY is empty. Populate core-api/.env before calling generate().",
            )
        self._client = AsyncAnthropic(api_key=api_key)
        self.model: str = model

    async def generate(
        self,
        system: str,
        user: str,
        *,
        max_output_tokens: int = 2048,
    ) -> LLMResponse:
        """Genera testo dato `system` (instruction) + `user` (request).

        Single-turn. Ritorna `LLMResponse` con `text` aggregato (Claude può
        emettere multipli text blocks; concateniamo), tokens, latency.

        `latency_ms` misurato con `time.monotonic()` (immune a system clock
        adjustments durante la call). Salvato in `brand_generations` per
        early-warning su provider degradation.
        """
        start = time.monotonic()
        try:
            response = await self._client.messages.create(
                model=self.model,
                max_tokens=max_output_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except AuthenticationError as exc:
            raise LLMError(
                f"Anthropic auth failed (check ANTHROPIC_API_KEY): {exc}",
            ) from exc
        except RateLimitError as exc:
            raise LLMError(f"Anthropic rate limit exceeded: {exc}") from exc
        except APIError as exc:
            raise LLMError(f"Anthropic API error: {exc}") from exc
        latency_ms = int((time.monotonic() - start) * 1000)

        # Claude può ritornare multipli text blocks (raro per generation
        # single-turn ma defensive). Concateniamo i text blocks.
        text_parts: list[str] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
        text = "".join(text_parts)

        return LLMResponse(
            text=text,
            model=self.model,
            tokens_input=response.usage.input_tokens,
            tokens_output=response.usage.output_tokens,
            latency_ms=latency_ms,
        )
