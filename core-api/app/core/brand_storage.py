"""Brand assets filesystem storage (S7).

Path layout: `<brand_assets_dir>/<client_id>/<asset_id>.<ext>`
Es: `storage/brand_assets/8eaa0de6-…/e5ee2dc6-….pdf`

Migration a Supabase Storage in S+. Per ora local filesystem (gitignored).
Path env-driven via `settings.brand_assets_dir`. La dir base + dir per-client
sono auto-create al primo write (`mkdir(parents=True, exist_ok=True)`).

**MIME validation**: in S7 verifichiamo i magic bytes manualmente
(`b"%PDF-"` prefix). Pattern semplice e self-contained, sufficiente per
allowlist single-MIME (`application/pdf`). Quando in S+ accetteremo altri
MIME (.docx, .pptx), valuteremo `python-magic` (richiede `libmagic` system
lib via `brew install libmagic` su macOS). `python-magic` è già nelle dep
ma non importato qui per evitare hard requirement libmagic in dev.

Vedi ADR-0008 §"Decisione 6 — File storage local for S7".
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

import structlog

from app.core.config import get_settings

log = structlog.get_logger(__name__)


class BrandStorageError(Exception):
    """Base error per filesystem brand assets."""


class UnsupportedFileTypeError(BrandStorageError):
    """MIME type non in allowlist (S7: solo `application/pdf`)."""


class FileTooBigError(BrandStorageError):
    """File supera `_MAX_FILE_SIZE_BYTES`."""


_MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB
_PDF_MAGIC_PREFIX = b"%PDF-"


def validate_and_hash_pdf(file_bytes: bytes) -> tuple[str, str]:
    """Valida che `file_bytes` sia un PDF reale (magic bytes) e ritorna sha256.

    Returns:
        Tuple `(sha256_hex, mime_type)`. `mime_type` è sempre `'application/pdf'`
        per consistency con interfaccia futura python-magic.

    Raises:
        FileTooBigError: > 20 MB.
        UnsupportedFileTypeError: i primi byte NON sono `b'%PDF-'`. Pattern
            usato anche da `pypdf` per riconoscere i PDF; un .png o .txt
            renamed `.pdf` viene rifiutato qui.
    """
    if len(file_bytes) > _MAX_FILE_SIZE_BYTES:
        raise FileTooBigError(
            f"File size {len(file_bytes)} bytes exceeds max "
            f"{_MAX_FILE_SIZE_BYTES} bytes (20 MB).",
        )

    # Magic bytes check: PDF spec ISO 32000-1 §7.5.2 mandates `%PDF-` prefix.
    # Robusto a estensioni rinominate (.pdf su file txt → fallisce qui).
    if not file_bytes.startswith(_PDF_MAGIC_PREFIX):
        raise UnsupportedFileTypeError(
            "File is not a valid PDF (magic bytes mismatch). "
            "Only application/pdf supported in S7.",
        )

    sha256 = hashlib.sha256(file_bytes).hexdigest()
    return sha256, "application/pdf"


def get_asset_path(client_id: UUID, asset_id: UUID, extension: str = "pdf") -> Path:
    """Path absolute dell'asset file. NON crea il file/directory."""
    base = Path(get_settings().brand_assets_dir).resolve()
    return base / str(client_id) / f"{asset_id}.{extension}"


def save_asset_to_filesystem(
    client_id: UUID,
    asset_id: UUID,
    file_bytes: bytes,
    extension: str = "pdf",
) -> Path:
    """Salva `file_bytes` nel filesystem. Crea directory parent se non esiste.

    **Atomic write**: scrive prima in `<path>.tmp`, poi `rename` atomicamente
    al path finale. Evita race in cui un reader vedrebbe un file parziale
    (anche se in S7 con BackgroundTasks scheduling unlikely, pattern good).

    Returns:
        Path absolute dove il file è stato salvato.
    """
    path = get_asset_path(client_id, asset_id, extension)
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_bytes(file_bytes)
    tmp_path.rename(path)
    return path


def delete_asset_from_filesystem(
    client_id: UUID,
    asset_id: UUID,
    extension: str = "pdf",
) -> bool:
    """Elimina file. Ritorna `True` se cancellato, `False` se non esisteva.

    Best-effort: non solleva se il file non esiste (caso normale dopo cleanup
    parziale o doppio DELETE). Solleva `OSError` solo per problemi reali
    (permission denied, disk error).
    """
    path = get_asset_path(client_id, asset_id, extension)
    if path.exists():
        path.unlink()
        return True
    return False
