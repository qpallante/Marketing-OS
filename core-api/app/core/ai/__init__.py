"""AI adapter interfaces — Protocol-based (PEP 544).

Provides:
  - `EmbedderProtocol` + `EmbeddingResult` (S7 step 3a)
  - `LLMProtocol` + `LLMResponse` (S7 step 3a)
  - `get_embedder()` / `get_llm()` factory functions (concrete impls in S7 step 3b)

**Adapter pattern motivation**: future multi-provider support (Cohere /
Voyage embeddings, GPT-4 / Gemini LLM, sentence-transformers self-hosted,
ecc.) richiede solo nuova classe + switch via env var, NESSUNA modifica al
consumer code (services, endpoints).

**Protocol vs ABC** (PEP 544 vs `abc.ABC`): scelto Protocol. Pro: structural
typing senza inheritance forzata, mypy lo enforce strutturalmente, no
boilerplate `@abstractmethod`. Contro minore: niente isinstance check
runtime — accettabile, le implementations sono DI-wired via factory, non
plugin di terze parti. Vedi ADR-0008 §"Decisione 4 — Protocol-based adapter".
"""

from app.core.ai.embedder import EmbedderError, EmbedderProtocol, EmbeddingResult
from app.core.ai.factory import get_embedder, get_llm, reset_factory
from app.core.ai.llm import LLMError, LLMProtocol, LLMResponse

__all__ = [
    "EmbedderError",
    "EmbedderProtocol",
    "EmbeddingResult",
    "LLMError",
    "LLMProtocol",
    "LLMResponse",
    "get_embedder",
    "get_llm",
    "reset_factory",
]
