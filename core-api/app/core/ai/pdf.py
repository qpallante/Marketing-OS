"""PDF text extraction utility (pypdf-based).

Per S7: estrazione testo da PDF "text-based" (PDF generati da Word/Pages/
InDesign che contengono text streams). PDF scansionati (immagini di testo)
NON sono supportati — l'estrazione fallisce con `PDFNoTextExtractedError`
e il chiamante deve segnare `brand_assets.indexing_status = 'failed'`.

OCR per PDF scansionati = scope S+ (con Tesseract via pytesseract o Apple
Vision framework). Per ora il messaggio di errore documenta il fallback.

Pure function, no I/O esterno (riceve `bytes` già letti dal chiamante).
Consumer in step 5 (indexing pipeline): legge `brand_assets.file_path`,
passa i bytes qui, riceve `str` da chunkare.
"""

from __future__ import annotations

from io import BytesIO

import pypdf
import structlog

log = structlog.get_logger(__name__)


class PDFExtractionError(Exception):
    """Base exception per errori di estrazione PDF."""


class PDFPasswordProtectedError(PDFExtractionError):
    """PDF cifrato/password-protected — non gestiamo decrypt in S7.

    Mappato a 422 lato endpoint con messaggio user-facing "PDF protetto da
    password non supportato".
    """


class PDFNoTextExtractedError(PDFExtractionError):
    """PDF leggibile ma senza testo estratto (probabilmente scansionato).

    Mappato a `brand_assets.indexing_status='failed'` + detail
    "PDF scansionato — OCR non supportato in S7. Carica versione testuale
    del documento."
    """


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Estrae tutto il testo da un PDF.

    Args:
        pdf_bytes: contenuto binario del PDF (es. da file upload).

    Returns:
        Testo concatenato di tutte le pagine, separato da `\\n\\n` tra
        pagine. Whitespace normalizzato (no triple newline, no trailing
        spaces).

    Raises:
        PDFPasswordProtectedError: PDF cifrato.
        PDFNoTextExtractedError: nessun testo estratto (PDF scansionato).
        PDFExtractionError: errori generici di parsing pypdf (stream
            corrotto, formato non riconosciuto, ecc.).
    """
    try:
        reader = pypdf.PdfReader(BytesIO(pdf_bytes))
    except pypdf.errors.PdfReadError as exc:
        raise PDFExtractionError(f"PDF read error: {exc}") from exc
    except Exception as exc:
        # Es. struct errors, ZIP errors per stream compressi corrotti, ecc.
        # Mappiamo tutto a PDFExtractionError per uniform handling lato endpoint.
        raise PDFExtractionError(f"PDF parsing failed: {type(exc).__name__}: {exc}") from exc

    if reader.is_encrypted:
        # pypdf può tentare decrypt con password vuota (a volte funziona per
        # PDF "encrypted but no password set"); ma per S7 trattiamo TUTTI i
        # PDF cifrati come unsupported. Pattern conservativo.
        raise PDFPasswordProtectedError("PDF is password-protected")

    texts: list[str] = []
    for page_num, page in enumerate(reader.pages):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:
            log.warning(
                "pdf.page_extract_failed",
                page=page_num,
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )
            continue

        # Normalize whitespace: strip + collapse multi-spaces in singolo space.
        # `text.split()` divide su qualsiasi whitespace consecutivo, `' '.join`
        # rimette singolo space. Idempotente per stringhe già pulite.
        normalized = " ".join(page_text.split())
        if normalized:
            texts.append(normalized)

    full_text = "\n\n".join(texts).strip()

    if not full_text:
        raise PDFNoTextExtractedError(
            f"No text extracted from {len(reader.pages)} pages. "
            "PDF may be scanned/image-only (OCR not supported in S7).",
        )

    return full_text
