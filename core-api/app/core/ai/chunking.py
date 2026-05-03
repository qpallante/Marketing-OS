"""Token-aware text chunking via tiktoken.

Splitta testo lungo in chunk di N token con overlap per mantenere context
continuity tra chunk adiacenti durante retrieval RAG.

**Default S7**: `chunk_tokens=512` + `overlap_tokens=50` (industry standard
RAG su brand content). Tokenizer `cl100k_base` (matching OpenAI v3
embeddings — mismatch provider tokenizer != embedder farebbe overshooting
del limit 8191).

**Algoritmo**: sliding window con overlap (no recursive split per separator).
- Tokenizza tutto il testo una volta.
- Estrae window di `chunk_tokens` consecutive con step di
  `(chunk_tokens - overlap_tokens)`.
- Decode ogni window in testo e crea `TextChunk`.

**Trade-off vs recursive splitter** (LangChain `RecursiveCharacterTextSplitter`):
- Pro sliding-window: semplice, deterministico, no chunks "spezzati a metà
  parola" sopra una certa soglia (tiktoken decode non spezza a metà di un
  token, e i token sono almeno 1 char).
- Con sliding-window: il boundary può cadere a metà di una frase. Per
  brand content (caption, tone-of-voice, do/dont) accettabile — il context
  RAG aggrega più chunks comunque.

Consumer step 5 (indexing pipeline): chunka il testo del PDF, embedda i
chunks via `EmbedderProtocol.embed_batch()`, INSERT in `brand_chunks` con
`token_count` per cost tracking.

Vedi ADR-0008 §"Decisione 1 — Chunk size 512 + overlap 50".
"""

from __future__ import annotations

from dataclasses import dataclass

import tiktoken

#: Default chunk size in token. NIST 800-63B-style: nessun magic number sparso.
_DEFAULT_CHUNK_TOKENS = 512
_DEFAULT_OVERLAP_TOKENS = 50

#: Encoding tiktoken matched al text-embedding-3-{small,large} (vedi
#: `embedder_openai.py::_TOKENIZER_ENCODING`). Cambiarli insieme.
_TOKENIZER_ENCODING = "cl100k_base"


class ChunkingError(Exception):
    """Errori di chunking (configurazione invalida, encoding fail)."""


@dataclass(frozen=True)
class TextChunk:
    """Singolo chunk di testo con metadata per indexing.

    `index`: 0-based, ordine sequenziale nel documento originale. UNIQUE
    constraint su `(asset_id, chunk_index)` in DB (migration 0004).

    `text`: contenuto decoded dal token range. Può iniziare/finire a metà
    parola — accettabile per RAG.

    `token_count`: numero esatto di token (post-decode). Salvato in
    `brand_chunks.token_count` per cost tracking aggregato per asset.
    """

    index: int
    text: str
    token_count: int


def split_text(
    text: str,
    *,
    chunk_tokens: int = _DEFAULT_CHUNK_TOKENS,
    overlap_tokens: int = _DEFAULT_OVERLAP_TOKENS,
) -> list[TextChunk]:
    """Splitta testo in chunk token-aware con overlap.

    Args:
        text: testo da splittare (raw, già extracted dal PDF o plain).
        chunk_tokens: dimensione chunk in token. Default 512 (S7).
        overlap_tokens: overlap in token tra chunk adiacenti. Default 50.

    Returns:
        Lista di `TextChunk` con `index` sequenziale 0, 1, 2, ...
        - Empty/whitespace-only text → `[]`.
        - Text più corto di `chunk_tokens` → `[TextChunk(index=0, ...)]` singolo.

    Raises:
        ChunkingError: se `chunk_tokens <= overlap_tokens` (window non
            avanzerebbe mai).
    """
    if chunk_tokens <= overlap_tokens:
        raise ChunkingError(
            f"chunk_tokens ({chunk_tokens}) must be > overlap_tokens ({overlap_tokens})",
        )

    if not text or not text.strip():
        return []

    tokenizer = tiktoken.get_encoding(_TOKENIZER_ENCODING)
    all_tokens = tokenizer.encode(text)

    if not all_tokens:
        return []

    chunks: list[TextChunk] = []
    step = chunk_tokens - overlap_tokens
    index = 0
    start = 0
    total_tokens = len(all_tokens)

    while start < total_tokens:
        end = min(start + chunk_tokens, total_tokens)
        window_tokens = all_tokens[start:end]
        chunk_text = tokenizer.decode(window_tokens)

        chunks.append(
            TextChunk(
                index=index,
                text=chunk_text,
                token_count=len(window_tokens),
            ),
        )

        # Ultima window: end ha raggiunto il totale → stop (evita chunk vuoto).
        if end >= total_tokens:
            break

        start += step
        index += 1

    return chunks
