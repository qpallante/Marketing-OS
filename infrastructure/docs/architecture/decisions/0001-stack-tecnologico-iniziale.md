# ADR-0001 — Stack tecnologico iniziale

- **Status**: Accepted
- **Date**: 2026-05-01
- **Authors**: Quintino Pallante

## Context

Il Marketing OS parte da zero a maggio 2026 come piattaforma multi-tenant per gestire dati, contenuti, ads e analytics di clienti dell'agenzia. Vincoli iniziali:

- **Single-developer ramp-up**: nei primi 3-4 mesi un solo sviluppatore (founder), assistito da Claude Code. Ogni minuto speso in setup di tooling è un minuto sottratto al prodotto.
- **Multi-tenant nativo**: `client_id` deve essere prima dimensione ovunque, RLS necessaria su Postgres.
- **Costo a regime con 20-30 clienti**: scelte tecniche valutate sul costo a 12-18 mesi, non solo all'inizio.
- **AI-first**: SDK Anthropic + OpenAI di prima classe; routing tra modelli (Haiku/Sonnet/Opus + GPT) gestito a livello applicativo.
- **Boring tech bias**: nessun framework custom, nessun DSL, nessuna soluzione "cool" se aumenta il TCO senza beneficio chiaro.

Le alternative valutate sono dettagliate in fondo. Lo stack scelto sotto è ciò che è stato effettivamente bootstrappato in Sessione 1.

## Decision

### Backend (`core-api`)

- **Linguaggio**: Python 3.12 (Poetry 2.3 per dependency management, `virtualenvs.in-project = true`)
- **Web framework**: FastAPI 0.136
- **ORM**: SQLAlchemy 2.0 con driver async `asyncpg`
- **Migrations**: Alembic 1.18
- **Settings**: `pydantic-settings` 2.x (env-driven, `.env.example` documentato)
- **Auth/JWT**: `python-jose[cryptography]`
- **HTTP client**: `httpx` (async)
- **Logging**: `structlog` (console renderer in dev, JSON in staging/prod)
- **AI SDK**: `anthropic` (primario, Claude 4.x family) + `openai` (secondario/embeddings)
- **ASGI server**: `uvicorn[standard]` (con `uvloop` + `httptools`)

**Tooling dev**: `ruff` (linter strict — E/W/F/I/B/UP/N/S/A/C4/PIE/RET/SIM/ARG/PTH/ERA/PL/RUF/TID/ASYNC/DTZ/T20), `black` (line-length 100), `mypy --strict` (con plugin pydantic), `pytest` + `pytest-asyncio` (asyncio_mode=auto).

### Frontend (`web-dashboard`)

- **Framework**: **Next.js 16.2** con App Router (deviazione dichiarata da CLAUDE.md, che indicava Next.js 15 — la versione bootstrappata da `create-next-app@latest` è la 16.2)
- **React**: 19.2
- **Styling**: **Tailwind CSS v4** (CSS-first config, plugin PostCSS `@tailwindcss/postcss` — niente più `tailwind.config.ts` di default)
- **UI**: shadcn/ui (init eseguito in Sessione 1; componenti aggiunti on-demand a partire da Sessione 4)
- **Linter**: ESLint 9 con flat config + preset `eslint-config-next/core-web-vitals` e `/typescript`
- **Formatter**: Prettier + `prettier-plugin-tailwindcss`
- **TypeScript**: 5.x strict
- **Bundler/dev**: Turbopack (default in Next 16)

### Database & infra

- **Postgres**: Supabase (managed, EU region) — per multi-tenant + RLS native + Auth + Realtime + Storage
- **Job queue**: Celery + Redis (Sessione successiva)
- **Hosting backend**: Railway (fase iniziale), Hetzner Cloud da considerare oltre 50+ clienti
- **Hosting frontend**: Vercel
- **Storage media**: Backblaze B2 (S3-compatible)

### AI / observability (registrate qui per coerenza, build effettivo nelle sessioni 9-12)

- **Vector DB**: pgvector su Supabase (no servizio dedicato finché basta)
- **Embeddings**: OpenAI `text-embedding-3-small`
- **Image gen**: Flux via Replicate, Midjourney via API
- **Errori**: Sentry; **uptime**: Better Stack; **product analytics**: PostHog (self-hosted possibile)
- **Secret management**: Doppler (no secret in repo, `.env.example` documenta solo i nomi)

## Consequences

### Positive

- **Type safety end-to-end**: `mypy --strict` su Python, `tsc --strict` su TS, Pydantic + Zod ai bordi.
- **Stack coerente con la community AI**: Anthropic e OpenAI hanno SDK Python di prima classe.
- **Tempo di setup minimo**: Poetry + create-next-app + shadcn init ≈ 30 minuti totali.
- **Costi bassi all'inizio**: Supabase free tier, Vercel free tier, Railway hobby tier coprono il bootstrap.
- **Multi-tenant idiomatico**: RLS Postgres + JWT con `client_id` claim → policy a 1-riga.

### Negative / Trade-off

- **Next.js 16 e Tailwind v4 sono recenti**: alcune librerie di terze parti potrebbero non averli ancora pienamente supportati. Mitigazione: il file `web-dashboard/AGENTS.md` ricorda esplicitamente di consultare `node_modules/next/dist/docs/` prima di scrivere codice nuovo.
- **Lock-in Supabase**: Auth + Realtime + Storage sono comodi ma non portabili senza riscrittura. Accettabile fino a 50+ clienti; rivalutare se costi/scaling lo richiedono.
- **Poetry vs uv**: scelto Poetry per allineamento al CLAUDE.md e maturità storica. `uv` (più veloce) è candidato per migrazione futura — non costa nulla cambiare in seguito.
- **Python 3.12 (non 3.13/3.14)**: scelto per stabilità di ecosistema librerie AI a maggio 2026, anche se sul Mac del founder è installato Python 3.14. `~3.12` come constraint pinned in pyproject.

### Da rivalutare quando

- Superiamo 30 clienti e Vercel/Railway iniziano a costare in modo non lineare → valutare Hetzner Cloud + self-hosted Postgres.
- Anthropic o un competitor lancia modelli con caching/contesti che invalidano i pattern attuali → ADR successivo per il routing.
- Tailwind v5 / Next.js 17 escono con migrazione non banale → ADR di upgrade.
- Multi-region diventa requisito (oggi solo EU).

## Alternatives considered

- **Backend: Django + DRF**. Più "batterie incluse" ma sovradimensionato per API-only e meno idiomatico in async. Scartato.
- **Backend: Node/TypeScript end-to-end (Hono/Elysia)**. Vantaggio: un solo linguaggio. Svantaggio: ecosistema AI Python molto più ricco (LangGraph, librerie embedding/dataset). Scartato.
- **Frontend: Remix / TanStack Start**. Buoni, ma comunità + integrazione Vercel + maturità shadcn rendono Next.js la scelta a basso rischio.
- **DB: PlanetScale / Neon**. Validi, ma Supabase offre Auth + Realtime + Storage in un solo bundle: meno servizi da gestire all'inizio.
- **Job queue: Temporal / RQ / Dramatiq**. Celery resta lo standard de-facto Python con la community più ampia. Rivalutare Temporal se serve workflow versioning.
- **Package manager Python: uv / pdm / Hatch**. uv è il più promettente, ma cambiare ora costerebbe ore di re-config tooling per zero beneficio percepito.

## References

- `CLAUDE.md` (root) — principi architetturali del progetto
- `07_Prompt_Claude_Code.md` — Sessione 1: Bootstrap del progetto
- [FastAPI](https://fastapi.tiangolo.com/) · [SQLAlchemy 2.0](https://docs.sqlalchemy.org/en/20/) · [Alembic](https://alembic.sqlalchemy.org/)
- [Next.js docs](https://nextjs.org/docs) · [Tailwind v4](https://tailwindcss.com/docs/v4-beta) · [shadcn/ui](https://ui.shadcn.com/)
- [Supabase RLS](https://supabase.com/docs/guides/auth/row-level-security)
