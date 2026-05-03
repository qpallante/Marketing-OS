"""Factory functions per ottenere un Embedder / LLM concreto.

Centralizza la decision logic provider-selection (env-driven). Le
implementations concrete sono:
  - `app/core/ai/embedder_openai.py::OpenAIEmbedder`
  - `app/core/ai/llm_anthropic.py::AnthropicLLM`

**Pattern di consumo** (services, endpoints, workers):
```python
from app.core.ai import get_embedder, get_llm

async def index_asset(text: str) -> None:
    embedder = get_embedder()  # ← Protocol return, NON OpenAIEmbedder concreto
    result = await embedder.embed(text)
    ...

async def generate_caption(prompt: str, brand_ctx: str) -> None:
    llm = get_llm()
    response = await llm.generate(system=brand_ctx, user=prompt)
    ...
```

Il consumer non importa mai la classe concreta. Switch provider via env var
(S+) tocca solo questa factory, NESSUN consumer cambia.

**Singleton lazy**: l'instance si crea alla prima chiamata di `get_*`.
Subsequent calls riusano la stessa instance — vital per OpenAI/Anthropic
SDK che mantengono HTTP connection pool internamente. Niente costo extra
per chiamata.

**`reset_factory()`** per test/tooling: invalida i singleton, utile quando
si modifica `settings.openai_api_key` runtime (es. test su keys diverse,
o `lru_cache` `get_settings()` invalidato).

Lazy import delle classi concrete: evita di caricare openai/anthropic SDK
al boot dell'app (utile per test che non usano AI). Solo alla prima call.

Vedi ADR-0008 §"Decisione 4 — Adapter factory env-driven".
"""

from __future__ import annotations

from app.core.ai.embedder import EmbedderProtocol
from app.core.ai.llm import LLMProtocol
from app.core.config import get_settings

_embedder: EmbedderProtocol | None = None
_llm: LLMProtocol | None = None


def get_embedder() -> EmbedderProtocol:
    """Ritorna l'`EmbedderProtocol` concreto configurato (singleton lazy).

    Provider attuale: OpenAI text-embedding-3-small (env `OPENAI_API_KEY` +
    `OPENAI_EMBEDDING_MODEL`). Future provider via switch env var (S+).

    Raises:
        EmbedderError: se la API key è vuota o invalida.
    """
    global _embedder  # noqa: PLW0603 — singleton lazy init
    if _embedder is None:
        from app.core.ai.embedder_openai import OpenAIEmbedder

        settings = get_settings()
        _embedder = OpenAIEmbedder(
            api_key=settings.openai_api_key,
            model=settings.openai_embedding_model,
        )
    return _embedder


def get_llm() -> LLMProtocol:
    """Ritorna l'`LLMProtocol` concreto configurato (singleton lazy).

    Provider attuale: Anthropic Claude (env `ANTHROPIC_API_KEY` +
    `ANTHROPIC_MODEL`). Future provider via switch env var (S+).

    Raises:
        LLMError: se la API key è vuota o invalida.
    """
    global _llm  # noqa: PLW0603 — singleton lazy init
    if _llm is None:
        from app.core.ai.llm_anthropic import AnthropicLLM

        settings = get_settings()
        _llm = AnthropicLLM(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
        )
    return _llm


def reset_factory() -> None:
    """Invalida i singleton — utile in test per re-instanziare con env diverso.

    Esempio: smoke test che fa `monkeypatch.setenv("OPENAI_API_KEY", "...")`
    + `get_settings.cache_clear()` + `reset_factory()` per ricreare
    l'embedder con la nuova key.

    NB: NON va usato in code di produzione. Le HTTP connection pool dei
    SDK provider sono designed per essere long-lived; ricreare per ogni
    request sarebbe wasteful.
    """
    global _embedder, _llm  # noqa: PLW0603 — test helper
    _embedder = None
    _llm = None
