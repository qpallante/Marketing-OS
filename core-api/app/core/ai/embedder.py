"""Embedding adapter Protocol (S7 step 3a — scaffold only).

`EmbedderProtocol` definisce l'interfaccia per generare vector embeddings da
testo, indipendente dal provider.

Implementations:
  - `OpenAIEmbedder` (S7 step 3b) — `text-embedding-3-small`, 1536-dim
  - Future: `CohereEmbedder`, `VoyageEmbedder`, `LocalEmbedder` (S+ se serve
    privacy-first per client enterprise EU con data residency)

Vedi ADR-0008 §"AI stack: embedding provider scelta + privacy disclaimer".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class EmbedderError(Exception):
    """Base error per failure dell'embedder.

    Mappa errori provider-specific (auth, rate limit, quota, network) in
    una sola classe app-level. Il consumer (services/endpoint) può loggare
    + mappare a HTTP 503/422 senza dipendere dalla classe SDK provider.

    Subclassing futuro se serve granularità (es. `EmbedderRateLimitError`,
    `EmbedderQuotaExceededError`) — S8+ se la pipeline indexing avrà
    retry/fallback logic.
    """


@dataclass(frozen=True)
class EmbeddingResult:
    """Risultato di una batch di embedding.

    Invariante: `len(vectors) == len(texts)` passati a `embed_batch`. Ogni
    vector ha `len == EmbedderProtocol.dim`.

    `tokens_used` è il totale dei token consumati nella batch (per cost
    tracking aggregato in `brand_assets.indexing_detail` o eventuale
    cost_log futuro).
    """

    vectors: list[list[float]]
    model: str
    tokens_used: int


@runtime_checkable
class EmbedderProtocol(Protocol):
    """Protocol per generare embeddings da testo.

    `dim` è la dimensione dei vettori prodotti (es. 1536 per
    text-embedding-3-small). Esposto come property così il consumer può
    validare match con DB column type — la migration 0004 ha hardcoded
    `vector(1536)`, mismatch dim → INSERT fail al runtime.

    `model` ritorna il nome del modello effettivamente in uso (per logging).

    `embed_batch` produce embeddings per una lista di testi in una sola
    chiamata API. Per testo singolo, il consumer passa `[text]` e prende
    `result.vectors[0]`. Implementations devono gestire batch chunking
    (max 100 testi per chiamata su OpenAI) trasparentemente.

    `@runtime_checkable` permette `isinstance(x, EmbedderProtocol)` se
    serve, anche se preferiamo type hints + DI factory in produzione.
    """

    @property
    def dim(self) -> int: ...

    @property
    def model(self) -> str: ...

    async def embed(self, text: str) -> EmbeddingResult:
        """Convenience: embedda un singolo testo. Implementations devono
        gestire truncation se text supera il limite token del provider
        (per OpenAI text-embedding-3-small: 8191 token).
        """
        ...

    async def embed_batch(self, texts: list[str]) -> EmbeddingResult: ...
