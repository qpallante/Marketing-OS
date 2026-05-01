# core-api

Backend FastAPI per il **Marketing OS**.

Stack: Python 3.12, FastAPI, SQLAlchemy 2.0 (async), Alembic, Postgres (Supabase), Celery + Redis, Anthropic + OpenAI SDK.

## Setup

```bash
poetry env use /opt/homebrew/bin/python3.12
poetry install
cp .env.example .env  # popola con credenziali da Doppler
poetry run uvicorn app.main:app --reload
```

L'API sarà disponibile su `http://localhost:8000`. Documentazione interattiva su `/docs`.

## Comandi utili

```bash
poetry run pytest               # test
poetry run ruff check .         # lint
poetry run ruff format .        # formatting via ruff
poetry run black .              # formatting via black
poetry run mypy app             # type checking
poetry run alembic upgrade head # applica migrations
```

## Struttura

```
app/
├── adapters/    # Wrapper per piattaforme esterne (Meta, TikTok, Google, ecc.)
├── agents/      # Agent AI (Trend Scout, Caption Agent, ecc.)
├── core/        # Config, security, deps, middleware
├── models/      # SQLAlchemy models
├── routers/     # FastAPI routers per area funzionale
├── services/    # Business logic
├── workers/     # Celery tasks
└── main.py      # Entry point FastAPI
```

Vedi `/CLAUDE.md` (root del repo) per principi architetturali e convenzioni.
