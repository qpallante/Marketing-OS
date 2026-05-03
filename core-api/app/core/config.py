from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Marketing OS — Core API"
    app_version: str = "0.0.0"
    environment: Literal["dev", "staging", "production"] = "dev"
    debug: bool = False

    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/marketing_os",
        description="Postgres async DSN (asyncpg driver).",
    )
    redis_url: str = Field(default="redis://localhost:6379/0")

    jwt_secret_key: str = Field(
        default="CHANGE_ME_IN_PRODUCTION",
        description="Symmetric secret for HS256 JWT signing.",
    )
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    refresh_token_ttl_days: int = 7
    """Refresh token TTL in giorni. Default 7 in dev/staging (rotture scoperte
    presto). Override a 30 in production via env REFRESH_TOKEN_TTL_DAYS=30.
    Vedi ADR-0003."""

    # ─── AI provider settings (S7 step 3a) ─────────────────────────────────
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    #: Modello LLM Anthropic per generation. Sonnet 4.6 di default
    #: (cost/quality balance per RAG creative tasks). Override in produzione
    #: via env `ANTHROPIC_MODEL` se serve Opus 4.7 per task complessi
    #: (`claude-opus-4-7`) o Haiku 4.5 per task semplici/cheap (`claude-haiku-4-5`).
    #: Vedi ADR-0008 §AI stack + CLAUDE.md §LLM routing.
    anthropic_model: str = "claude-sonnet-4-6"

    #: Modello OpenAI per embedding. text-embedding-3-small è 1536-dim, $0.02/1M
    #: token (~zero cost). Per multilingue stretto valuteremo -large in S+.
    openai_embedding_model: str = "text-embedding-3-small"

    #: Dimensione del vector prodotto da `openai_embedding_model`. DEVE
    #: corrispondere a `vector(N)` declared su `brand_chunks.embedding`
    #: (ALTER TABLE in migration 0004). Cambiarlo richiede re-indexing
    #: completo di tutti i chunks.
    embedding_dim: int = 1536

    # ─── Brand assets storage (S7) ─────────────────────────────────────────
    #: Directory per file PDF caricati. Gitignored (vedi root .gitignore).
    #: Path relativo alla CWD del processo uvicorn (= `core-api/` dev),
    #: oppure absolute. La dir viene creata al startup app (lifespan).
    #: Migration a Supabase Storage in S+. Vedi ADR-0008.
    brand_assets_dir: str = "storage/brand_assets"

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    cors_origins: list[str] = ["http://localhost:3000"]

    frontend_url: str = Field(
        default="http://localhost:3000",
        description=(
            "URL base del frontend Next.js. Usato per costruire i link di "
            "invitation, password reset, email verification. In production "
            "override via env FRONTEND_URL (es. https://app.marketing-os.com)."
        ),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
