"""OpenAI embedder implementation (text-embedding-3-small).

Costo: $0.02/1M token (~$0.0001 per documento medio di 5000 token).
Dimension: 1536 (default per text-embedding-3-small).
Max input per request: 8191 token per testo, 2048 testi per batch.

Pattern: implementa `EmbedderProtocol` via duck typing (no inheritance
esplicita, PEP 544 structural typing). mypy verifica conformità via
factory return type.
"""

from __future__ import annotations

import structlog
import tiktoken
from openai import APIError, AsyncOpenAI, AuthenticationError, RateLimitError

from app.core.ai.embedder import EmbedderError, EmbeddingResult

log = structlog.get_logger(__name__)

#: Max token per singolo testo (limit OpenAI per text-embedding-3-small).
_OPENAI_MAX_INPUT_TOKENS = 8191

#: Max testi per singola request (limit OpenAI batch API).
_OPENAI_MAX_BATCH_SIZE = 2048

#: Tokenizer corretto per text-embedding-3-{small,large} (same encoding di gpt-4).
_TOKENIZER_ENCODING = "cl100k_base"


class OpenAIEmbedder:
    """OpenAI embedder per `text-embedding-3-small` (default S7).

    Configura un `AsyncOpenAI` client persistente (connection pool managed
    dal SDK). Singleton via `get_embedder()` factory: una sola instance
    per process.
    """

    def __init__(self, api_key: str, model: str) -> None:
        if not api_key:
            raise EmbedderError(
                "OPENAI_API_KEY is empty. Populate core-api/.env before calling embed().",
            )
        self._client = AsyncOpenAI(api_key=api_key)
        # Property-style attributes (PEP 544: instance attr soddisfa @property requirement)
        self.model: str = model
        self.dim: int = 1536  # text-embedding-3-small default
        self._tokenizer = tiktoken.get_encoding(_TOKENIZER_ENCODING)

    async def embed(self, text: str) -> EmbeddingResult:
        """Embedda un singolo testo. Trunca a 8191 token se necessario.

        Per testi che superano il limit, tronchiamo silenziosamente con WARN
        log (preferibile a fail: il caller — chunking step 4 — già splitta
        a 512 token, quindi truncation è defensive only contro input ad-hoc).
        """
        token_count = len(self._tokenizer.encode(text))
        if token_count > _OPENAI_MAX_INPUT_TOKENS:
            tokens = self._tokenizer.encode(text)[:_OPENAI_MAX_INPUT_TOKENS]
            text = self._tokenizer.decode(tokens)
            log.warning(
                "openai.embedder.truncated",
                original_tokens=token_count,
                truncated_to=_OPENAI_MAX_INPUT_TOKENS,
                model=self.model,
            )

        return await self.embed_batch([text])

    async def embed_batch(self, texts: list[str]) -> EmbeddingResult:
        """Embedda multipli testi in batch. Splitta in chunk di 2048 testi
        se la lista supera il limit OpenAI per request.

        Restituisce `vectors` nello stesso ordine di `texts` input. Aggrega
        `tokens_used` su tutti i sub-batch.
        """
        if not texts:
            raise EmbedderError("embed_batch called with empty list")

        all_vectors: list[list[float]] = []
        total_tokens = 0

        for i in range(0, len(texts), _OPENAI_MAX_BATCH_SIZE):
            batch = texts[i : i + _OPENAI_MAX_BATCH_SIZE]
            try:
                resp = await self._client.embeddings.create(
                    model=self.model,
                    input=batch,
                )
            except AuthenticationError as exc:
                raise EmbedderError(
                    f"OpenAI auth failed (check OPENAI_API_KEY): {exc}",
                ) from exc
            except RateLimitError as exc:
                # S7: no retry. S8+ aggiungeremo backoff esponenziale.
                raise EmbedderError(f"OpenAI rate limit exceeded: {exc}") from exc
            except APIError as exc:
                raise EmbedderError(f"OpenAI API error: {exc}") from exc

            # resp.data è ordinata come l'input batch
            all_vectors.extend(item.embedding for item in resp.data)
            total_tokens += resp.usage.total_tokens

        return EmbeddingResult(
            vectors=all_vectors,
            model=self.model,
            tokens_used=total_tokens,
        )
