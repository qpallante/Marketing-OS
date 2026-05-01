# Marketing OS

Piattaforma multi-tenant proprietaria che orchestra dati, contenuti, performance media e analytics per ogni cliente gestito da un'agenzia di marketing AI-native.

> **Status**: `v0.0.0` · Fase 1 (Fondamenta) · Sessione 1 (Bootstrap) ✓

## Cosa c'è dentro

| Cartella | Cosa contiene |
|----------|---------------|
| [`core-api/`](./core-api) | Backend FastAPI (Python 3.12, SQLAlchemy 2.0 async, Alembic, Anthropic + OpenAI SDK) |
| [`web-dashboard/`](./web-dashboard) | Frontend Next.js 16 + React 19 + Tailwind v4 + shadcn/ui |
| [`pipelines/`](./pipelines) | Job batch e pipeline AI standalone (Python) |
| [`infrastructure/`](./infrastructure) | IaC, runbook, ADR, schema Supabase |

Per i **principi architetturali** (multi-tenant, event-driven, AI-first con human-in-the-loop, cost-first, ecc.) vedi [`CLAUDE.md`](./CLAUDE.md).

## Quickstart sviluppo

### Prerequisiti
- Python 3.12 (`brew install python@3.12`)
- Node.js ≥ 20 (`brew install node`)
- Poetry 2.x (`pipx install poetry`)
- Docker (per Postgres e Redis locali, dalla Sessione 2 in poi)

### Backend (`core-api`)

```bash
cd core-api
poetry env use /opt/homebrew/bin/python3.12
poetry install
cp .env.example .env  # popola con valori reali (Doppler in produzione)
poetry run uvicorn app.main:app --reload
```

API su `http://localhost:8000`, docs interattive su `/docs`.

Comandi utili:

```bash
poetry run pytest          # test
poetry run ruff check .    # lint
poetry run mypy app        # type check
```

### Frontend (`web-dashboard`)

```bash
cd web-dashboard
npm install
npm run dev
```

Dashboard su `http://localhost:3000`.

Comandi utili:

```bash
npm run lint        # ESLint
npm run typecheck   # tsc --noEmit
npm run format      # Prettier write
npm run build       # production build (turbopack)
```

## Documentazione

- [`CLAUDE.md`](./CLAUDE.md) — riferimento principale: principi architetturali, stack, convenzioni, cosa fare e cosa non fare
- [`07_Prompt_Claude_Code.md`](./07_Prompt_Claude_Code.md) — prompt sessione-per-sessione per le prime 12 settimane
- [`infrastructure/docs/architecture/decisions/`](./infrastructure/docs/architecture/decisions/) — Architecture Decision Records (ADR)

## Roadmap iniziale

| Settimana | Sessione | Obiettivo |
|-----------|----------|-----------|
| 1 | 1 | Bootstrap repo (questa sessione) |
| 1 | 2 | Supabase + migrations base + RLS |
| 1 | 3 | Auth JWT + multi-tenant middleware |
| 1 | 4 | Dashboard frontend + login |
| 2 | 5 | Onboarding clienti |
| 2 | 6 | OAuth Meta |
| 3 | 7-8 | Data Lake + adapter Meta Ads |
| 4 | 9-10 | Brand Brain v0.1 + Caption Agent |
| 5-12 | … | (definite progressivamente) |

## Licenza

Proprietario. Codice non distribuito esternamente. Tutti i diritti riservati.
