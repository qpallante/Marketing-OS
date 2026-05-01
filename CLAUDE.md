# CLAUDE.md — Marketing OS

Questo file è il riferimento principale per Claude Code quando lavora su questo progetto. Va letto a inizio di ogni sessione.

---

## Cos'è questo progetto

Il **Marketing OS** è una piattaforma proprietaria multi-tenant che orchestra dati, contenuti, performance media e analytics per ogni cliente gestito da un'agenzia di marketing AI-native.

In fase iniziale (mesi 0-12) viene utilizzato come laboratorio interno per gestire la comunicazione di tre aziende del founder:

- **Monoloco** — Format itinerante di eventi premium (target lifestyle/nightlife)
- **Nightify** — Piattaforma ticketing eventi (B2B + B2C)
- **Interfibra** — ISP regionale italiano (B2C/B2B/PA, focus Centro-Sud)

Da mese 12+ verrà aperto a clienti esterni dell'agenzia, fino al target di 20-30 clienti gestiti contemporaneamente.

---

## Principi architetturali fondamentali

Quando prendi decisioni di design o scrivi codice, ricordati sempre questi principi:

### 1. Multi-tenant nativo dal giorno uno
Ogni tabella, ogni endpoint, ogni job ha sempre `client_id` come prima dimensione. Mai scrivere codice "single-tenant" e poi rifattorizzare. L'isolamento dei dati cliente è non negoziabile.

### 2. Event-driven dove possibile
I moduli comunicano tramite code di eventi (Celery + Redis) e Supabase Realtime, non con chiamate dirette. Permette di scalare ogni modulo indipendentemente e di gestire fallimenti in modo robusto.

### 3. AI-first ma con umano sempre nel loop
Ogni decisione automatica può essere validata, modificata o annullata da un operatore umano via dashboard. Non costruire mai automatismi che non possono essere fermati.

### 4. API esterne sempre wrappate
Tutte le integrazioni (Meta, TikTok, Google, OpenAI, Anthropic) passano attraverso adapter interni. Questo facilita il cambio di provider, gestione costi/limiti, e testing.

### 5. Infrastructure as Code dove possibile
Le configurazioni (Supabase schema, Railway services, ecc.) vanno versionate. Migrations gestite via codice, non via UI.

### 6. Documentazione paranoica
Ogni decisione architetturale che prendi va documentata in `/docs/architecture/decisions/` come ADR (Architecture Decision Record). Ogni endpoint API documentato. Ogni cron job spiegato.

### 7. Costo prima di sofisticazione
Quando scegli una soluzione tecnica, sempre considerare il costo a regime con 20-30 clienti. Soluzioni "cool" che costano 5x sono da evitare. Caching aggressivo, scelta intelligente di modelli LLM (Haiku per task semplici, Sonnet per task complessi, Opus solo dove indispensabile).

### 8. Sicurezza dati cliente non negoziabile
Row-level security su Supabase per ogni tabella. Audit log su ogni accesso a dati sensibili. Secrets mai in codice, sempre in vault.

---

## Stack tecnologico

### Core
- **Linguaggio backend:** Python 3.12
- **Web framework:** FastAPI
- **Database:** Postgres (Supabase managed)
- **ORM:** SQLAlchemy 2.0 con Alembic per migrations
- **Job queue:** Celery + Redis
- **Frontend:** Next.js 16+ (App Router) + TypeScript + React 19
- **UI:** Tailwind CSS v4 (CSS-first config) + shadcn/ui
- **Auth:** Supabase Auth con JWT
- **Storage:** Supabase Storage + Backblaze B2 per media (S3-compatible)
- **Realtime:** Supabase Realtime

### AI/ML
- **LLM primario:** Anthropic Claude Sonnet 4.6 (e Opus 4.7 per task complessi)
- **LLM secondario/backup:** OpenAI GPT (varianti recenti)
- **LLM economico per task semplici:** Anthropic Haiku 4.5
- **Embeddings:** OpenAI text-embedding-3-small per cost/performance
- **Vector DB:** pgvector su Supabase (Postgres extension)
- **Orchestrazione AI:** LangGraph o framework custom Python
- **Image generation:** Flux Schnell/Pro via Replicate, Midjourney via API
- **Video generation:** Runway via API, eventualmente Veo via Vertex AI quando disponibile
- **Voice:** ElevenLabs

### DevOps
- **Hosting backend:** Railway (initial), eventually Hetzner Cloud per costi a scala
- **Hosting frontend:** Vercel
- **CI/CD:** GitHub Actions
- **Monitoring:** Sentry per errori, Better Stack per uptime
- **Analytics:** PostHog (self-hosted possibile)
- **Secret management:** Doppler

---

## Struttura del repository

Il progetto è organizzato in 4 repository (eventualmente monorepo con Nx/Turborepo):

```
marketing-os/
├── core-api/           # Backend FastAPI
│   ├── app/
│   │   ├── adapters/    # Integrazioni con piattaforme esterne (Meta, TikTok, Google, ecc.)
│   │   ├── agents/      # Agent AI (Trend Scout, Caption Agent, ecc.)
│   │   ├── models/      # SQLAlchemy models
│   │   ├── routers/     # FastAPI routers (un file per area: clients, content, ads, ecc.)
│   │   ├── services/    # Business logic
│   │   ├── workers/     # Celery tasks
│   │   ├── core/        # Config, security, deps
│   │   └── main.py      # Entry point FastAPI
│   ├── alembic/          # Database migrations
│   ├── tests/
│   └── pyproject.toml
│
├── web-dashboard/       # Frontend Next.js
│   ├── src/
│   │   ├── app/         # App Router routes
│   │   ├── components/  # Componenti riutilizzabili
│   │   ├── lib/         # Utility, API client, ecc.
│   │   └── styles/
│   ├── package.json
│   └── tsconfig.json
│
├── pipelines/           # Job batch e pipeline AI standalone (Python)
│   ├── content_generation/
│   ├── data_ingestion/
│   ├── predictive/
│   └── shared/
│
└── infrastructure/      # IaC, docs, scripts
    ├── docs/
    │   ├── architecture/
    │   │   └── decisions/   # ADR (Architecture Decision Records)
    │   ├── runbooks/
    │   └── api/
    ├── scripts/             # Utility scripts
    └── supabase/            # Schema definitions, RLS policies
```

---

## Modello dati: principi

### Schema multi-tenant
Ogni tabella di dominio ha sempre:
```sql
client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE
```
con indice composito su `(client_id, ...)` per query efficienti.

### Row-Level Security
Ogni tabella ha policy RLS che restringe accesso ai dati del solo client_id autorizzato dall'utente. Le policy usano `auth.jwt()` di Supabase per leggere il client_id dall'utente connesso.

### Naming conventions
- Tabelle: `snake_case`, plurale (`campaigns`, `ad_creatives`)
- Colonne: `snake_case` (`created_at`, `client_id`)
- Indici: `idx_<table>_<columns>` (`idx_campaigns_client_id_created_at`)
- Foreign keys: sempre con `_id` suffix

### Timestamps
Ogni tabella ha:
```sql
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
```
con trigger `update_updated_at` su update.

---

## Convenzioni di codice

### Python
- **Type hints obbligatori** su tutti i parametri e return values
- **Pydantic models** per tutti i DTO/request/response
- **Linter:** Ruff (configurazione strict)
- **Formatter:** Black (line length 100)
- **Test framework:** pytest, fixture in `conftest.py`
- **Async by default** per I/O (FastAPI, asyncpg, httpx)
- **Logging strutturato** con `structlog`, JSON in produzione

### TypeScript
- **Strict mode** sempre attivo
- **Linter:** ESLint con configurazione Next.js + custom rules
- **Formatter:** Prettier
- **Type safety end-to-end** dal database alla UI (usa Zod per validazione runtime)
- **Server Components** per default, Client Components solo dove serve interattività

### Git
- **Branch naming:** `feature/`, `fix/`, `chore/`, `refactor/`
- **Commit:** Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, ecc.)
- **PR:** descrizione che spiega COSA cambia e PERCHÉ, non come (il codice spiega il come)

---

## Adapter pattern per integrazioni esterne

Ogni integrazione con un'API esterna segue questo pattern:

```python
from abc import ABC, abstractmethod

class PlatformAdapter(ABC):
    """Base adapter for external platform integrations."""

    @abstractmethod
    async def fetch_campaigns(self, client_id: UUID, since: datetime) -> list[Campaign]: ...

    @abstractmethod
    async def fetch_insights(self, campaign_id: str, date_range: DateRange) -> Insights: ...

    @abstractmethod
    async def publish_content(self, content: ContentDraft) -> PublishResult: ...

class MetaAdsAdapter(PlatformAdapter):
    """Concrete implementation for Meta (Facebook + Instagram) Ads."""
    ...
```

Ogni adapter:
- Gestisce rate limiting (retry con backoff esponenziale)
- Logga ogni chiamata API (per debugging e cost tracking)
- Normalizza i dati nello schema interno
- È testabile senza fare chiamate reali (mock dei response)

---

## Modelli AI: routing e cost optimization

### Routing logic
Per ogni invocazione LLM, scegliere il modello più economico che dia qualità accettabile:

| Task | Modello consigliato | Motivazione |
|---|---|---|
| Classificazione semplice (sentiment, tag) | Haiku | Veloce, economico, sufficiente |
| Generazione caption breve | Haiku o Sonnet | Sonnet se richiede tono complesso |
| Generazione contenuto lungo | Sonnet | Bilanciamento qualità/costo |
| Analisi strategica complessa | Opus | Solo dove la qualità conta davvero |
| Discriminator/quality scoring | Haiku | Task ripetitivo, modello veloce |

### Caching
- **Embeddings:** sempre cached, key = hash(text)
- **Generazioni deterministiche** (es. su brief identici): cache 24h
- **Trend analysis:** cache 6h
- **Brand Brain RAG queries:** cache 1h

### Budget caps
Ogni cliente ha un budget mensile di compute LLM configurato. Soft alert al 70%, hard cap al 100% (richiede override admin per sforare).

---

## Sicurezza e compliance

### Dati sensibili
- **PII clienti:** crittografati at-rest se stoccati (uso pgcrypto Postgres)
- **API keys clienti:** in vault separato (Doppler), mai in DB
- **Dati di pagamento:** mai in nostro DB, sempre via Stripe/provider esterno

### GDPR
- Ogni cliente firma DPA (Data Processing Agreement) come pre-requisito onboarding
- Dati su server EU (Supabase EU region, B2 EU region)
- Funzioni di cancellazione e portabilità dati implementate fin dall'inizio

### Audit log
Ogni accesso a dati cliente da parte di operatori (umani o automatici) viene loggato in tabella `audit_log` immutabile.

---

## Workflow di sviluppo

### Setup ambiente locale
1. Clone repo
2. `cp .env.example .env` e popola con credenziali Doppler
3. `docker compose up` per Postgres + Redis locali
4. Backend: `cd core-api && poetry install && poetry run alembic upgrade head && poetry run uvicorn app.main:app --reload`
5. Frontend: `cd web-dashboard && npm install && npm run dev`

### Testing
- Test unitari obbligatori per tutta la logica di business
- Test di integrazione per ogni adapter
- Test E2E (Playwright) per flussi critici della dashboard
- Coverage target: 70% backend, 50% frontend (focus sui flussi critici)

### Deployment
- Push su `main` → deploy automatico su staging
- Tag `v*` → deploy automatico su production
- Database migrations applicate manualmente con `alembic upgrade head` durante deploy

---

## Cosa NON fare

Per evitare di costruire complessità inutile:

- **Non usare microservizi prematuramente.** Monolite modulare nei primi 18 mesi.
- **Non usare Kubernetes.** Railway + Vercel sufficienti fino a 50+ clienti.
- **Non costruire UI custom dove shadcn/ui ha già componenti.** Standardizzazione prima di personalizzazione.
- **Non implementare feature non richieste.** YAGNI rigoroso.
- **Non scrivere DSL custom o framework "smart".** Boring tech vince.
- **Non aggiungere astrazioni "per il futuro".** Astrai solo quando hai 3 use case concreti.
- **Non dipendere da librerie poco mantenute.** Verificare salute repo (commit recenti, issue aperte, ecc.).

---

## Stato attuale del progetto

**Versione:** v0.0.2 (pre-alpha)
**Fase corrente:** Fase 1 — Fondamenta (Mesi 1-3)
**Sprint corrente:** Settimana 3 — Authentication, JWT, multi-tenant middleware
**Repo:** [github.com/qpallante/Marketing-OS](https://github.com/qpallante/Marketing-OS)

**Cosa è stato costruito:**
- **Sessione 1 — Bootstrap monorepo** (commit `afab0ae`, chiusa il 2026-05-01): struttura repo (`core-api/`, `web-dashboard/`, `pipelines/`, `infrastructure/`); `core-api` con Python 3.12 + Poetry + FastAPI 0.136 + SQLAlchemy 2.0 async + Alembic + Anthropic/OpenAI SDK + structlog (ruff/mypy strict puliti, `/health` operativo); `web-dashboard` con Next.js 16.2 + React 19 + Tailwind v4 + shadcn/ui (preset `base-nova`, neutral) + ESLint 9 + Prettier (build/lint/typecheck verdi); ADR-0001 in `infrastructure/docs/architecture/decisions/`; connessione a Supabase (progetto `txmkxllfhzrfetordkap`, pooler `eu-west-1` session mode, Postgres 17.6) verificata.
- **Sessione 2 — Schema multi-tenant + RLS** (commit `b5a18c2` schema, `d5763ad` RLS, chiusa il 2026-05-01): 4 tabelle (`clients`, `users`, `platform_accounts`, `audit_log`) con 4 enum tipizzati, trigger `set_updated_at`, `audit_log` append-only by design; 5 file RLS in `supabase/policies/` (helpers + per-tabella) con pattern `current_setting('app.current_client_id')` + `current_setting('app.is_super_admin')`; smoke test ✅ tutti verdi inclusa verifica fail-closed (0 righe senza `SET LOCAL`); seed Monoloco creato in DB (super_admin + client_admin). **ADR-0002** documenta il contratto del middleware: ogni transazione autenticata DEVE eseguire `SET LOCAL ROLE authenticated` prima di settare le variabili — il role `postgres` è SUPERUSER e bypassa RLS automaticamente.

**Prossima milestone:** Auth flow completo + middleware multi-tenant che applica contratto ADR-0002 (fine Sessione 3).

---

## Note operative per Claude Code

Quando lavori su questo progetto:

1. **Sempre leggi questo CLAUDE.md a inizio sessione**, anche se l'ho già letto in precedenza. Le cose cambiano.
2. **Quando proponi una decisione architetturale importante**, prima discutila con me prima di scrivere codice. Anche se ti sembra ovvia.
3. **Quando aggiungi una libreria nuova**, motivala. Lock-in e tech debt.
4. **Quando scrivi codice complesso, scrivi prima un test che lo descrive.** TDD light.
5. **Aggiorna questo CLAUDE.md** quando facciamo decisioni che riguardano l'intero progetto.
6. **Non commettere mai codice senza review.** Mostrami sempre i diff prima di committare.
7. **Una sessione = uno scope.** Non saltare tra moduli diversi nella stessa sessione di lavoro.
8. **Quando lavori sul frontend** (`web-dashboard/`), prima di scrivere codice leggi `web-dashboard/CLAUDE.md` e `web-dashboard/AGENTS.md`: avvisano che la versione di Next.js installata ha breaking changes rispetto al training data e indicano dove trovare le docs aggiornate (`web-dashboard/node_modules/next/dist/docs/`). Non assumere convenzioni "classiche" senza prima verificarle lì.

---

## Contatti del progetto

- **Founder/Tech Lead:** Quintino
- **Account & Strategy Lead:** [da assumere mese 4]
- **Riferimento documentazione strategica:** /Users/quintino/projects/agenzia2031/docs

---

Questo file è la fonte di verità. Aggiornalo ogni volta che facciamo decisioni importanti.
