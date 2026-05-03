"""LLM adapter Protocol (S7 step 3a — scaffold only).

`LLMProtocol` definisce l'interfaccia per generation con un Large Language
Model, indipendente dal provider.

Implementations:
  - `AnthropicLLM` (S7 step 3b) — `claude-sonnet-4-6` default
  - Future: `OpenAILLM` (GPT-4), `GeminiLLM`, ecc.

Vedi ADR-0008 §"AI stack: LLM provider scelta + cost-quality routing".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class LLMError(Exception):
    """Base error per failure dell'LLM (auth, rate limit, quota, network).

    Stesso pattern di `EmbedderError`. Subclassing futuro se serve
    granularità per retry/fallback logic (S8+).
    """


@dataclass(frozen=True)
class LLMResponse:
    """Risultato di una generation LLM.

    `text`: output testuale completo (no streaming in S7).

    `model`: il modello effettivamente usato. Importante perché un'app
    potrebbe configurare default Sonnet ma il singolo call site potrebbe
    overrridare con Haiku per task economici (S8+ routing logic).

    `tokens_input` / `tokens_output`: cost attribution precisa (provider
    billing differente per direzione). Salvati in `brand_generations` per
    analytics + budget tracking.

    `latency_ms`: end-to-end (incluso provider RTT). **Anche early-warning
    system per provider degradation**: spike anomali sono segnale di
    problemi upstream prima ancora di errori espliciti. Salvato in
    `brand_generations`. Vedi ADR-0008 §Decisione 5.
    """

    text: str
    model: str
    tokens_input: int
    tokens_output: int
    latency_ms: int


@runtime_checkable
class LLMProtocol(Protocol):
    """Protocol per generation con LLM.

    **Single-turn API**: `system` (instruction prompt) + `user` (request) →
    `LLMResponse`. Multi-turn (conversation history) deferred a S8+ se
    arriverà un use case (es. chat-style assistant). Streaming responses
    deferred a S8+ (richiede WebSocket/SSE infrastructure).

    `max_output_tokens` è cap difensivo — protegge da output runaway e
    cost spike. Default 2048 sufficiente per 3 caption Instagram + qualche
    margine. Per long-form copy (post LinkedIn ~1500 char), bumpare a 4096
    per call site.
    """

    @property
    def model(self) -> str: ...

    async def generate(
        self,
        system: str,
        user: str,
        *,
        max_output_tokens: int = 2048,
    ) -> LLMResponse: ...
