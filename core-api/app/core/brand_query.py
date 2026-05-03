"""Brand Brain RAG query helpers (S7 step 6).

Provides:
  - `build_system_prompt(client_name, form_data, retrieved_chunks)` — XML-tagged
    system prompt per Claude (Anthropic best practice). Brand identity +
    reference material + rules.

Pattern XML-tagged (vs Markdown sections): Anthropic docs §"Use XML tags"
documentano che Claude segue gli XML tag più affidabilmente del markdown
(disambigua chiaramente sezioni vs contenuto). Vedi ADR-0008 §Decisione 8
"RAG system prompt structure".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.models.brand import BrandFormData

#: Rules block costante. Include diversity rule (S7 user feedback): senza
#: il vincolo, Claude tende a generare 3 variazioni quasi identiche di una
#: stessa idea. La regola forza alternative reali.
_RULES = (
    "Sei un copywriter esperto del brand sopra descritto. Genera contenuto "
    "richiesto dall'utente mantenendo stretta coerenza col tone-of-voice e "
    "con i materiali di riferimento.\n"
    "\n"
    "REGOLE:\n"
    "- Non inventare fatti specifici (prezzi, date, eventi, persone, "
    "promozioni) non presenti nei materiali di riferimento. Se l'utente "
    "li chiede e non li trovi, dichiara apertamente \"non ho questa "
    "informazione\".\n"
    "- Rispetta i \"do\" e \"don't\" del brand.\n"
    "- Non scrivere i brand colors nel content (sono solo per riferimento).\n"
    "- Se generi più output (es. 3 caption), assicurati che siano DIVERSI "
    "tra loro (varia lunghezza, tono leggermente, angle). Non generare 3 "
    "variazioni quasi identiche."
)


def build_system_prompt(
    client_name: str,
    form_data: BrandFormData | None,
    retrieved_chunks: list[Mapping[str, Any]],
) -> str:
    """Costruisce il system prompt XML-tagged per Claude.

    Args:
        client_name: nome del client (es. "Monoloco"), iniettato nell'attr
            `<brand name=...>`.
        form_data: tone/dos/donts/colors. Se `None`, il blocco brand è
            minimale (solo nome). UI dovrebbe incoraggiare a compilare il
            form per output qualitativamente migliore.
        retrieved_chunks: top-K chunks da retrieval cosine. Lista di dict
            con almeno `chunk_text` e `asset_filename`. Lista vuota se
            client non ha asset indicizzati (graceful degrade — Claude
            genera con solo brand identity, output sarà più generico).

    Returns:
        System prompt formattato. Rules sempre presente in coda. La
        `user_prompt` viene passata SEPARATAMENTE come `user` message
        a `LLMProtocol.generate(system, user, ...)` — pattern Anthropic.
    """
    parts: list[str] = []

    # ─── Brand identity block ───────────────────────────────────────────
    if form_data is not None:
        brand_lines = [f'<brand name="{client_name}">']
        if form_data.tone_keywords:
            brand_lines.append(
                f"  <tone_of_voice>{', '.join(form_data.tone_keywords)}</tone_of_voice>",
            )
        if form_data.dos:
            dos_inner = "\n".join(f"    - {d}" for d in form_data.dos)
            brand_lines.append(f"  <dos>\n{dos_inner}\n  </dos>")
        if form_data.donts:
            donts_inner = "\n".join(f"    - {d}" for d in form_data.donts)
            brand_lines.append(f"  <donts>\n{donts_inner}\n  </donts>")
        if form_data.colors_hex:
            brand_lines.append(
                f"  <brand_colors>{', '.join(form_data.colors_hex)}</brand_colors>",
            )
        brand_lines.append("</brand>")
        parts.append("\n".join(brand_lines))
    else:
        # Graceful: client_name presente, no form details.
        parts.append(f'<brand name="{client_name}"><!-- no form data --></brand>')

    # ─── Reference material (retrieved chunks) ──────────────────────────
    if retrieved_chunks:
        ref_lines = ["<reference_material>"]
        for chunk in retrieved_chunks:
            filename = str(chunk.get("asset_filename", "unknown"))
            chunk_text = str(chunk.get("chunk_text", ""))
            ref_lines.append(f'  <chunk source="{filename}">')
            # Indenta il chunk_text per leggibilità del prompt
            indented = "\n".join("    " + line for line in chunk_text.splitlines())
            ref_lines.append(indented)
            ref_lines.append("  </chunk>")
        ref_lines.append("</reference_material>")
        parts.append("\n".join(ref_lines))
    # else: niente reference block. Rules dice già "non inventare fatti".

    # ─── Rules + closing ────────────────────────────────────────────────
    parts.append(_RULES)

    return "\n\n".join(parts)
