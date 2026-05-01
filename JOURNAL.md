# Journal — Marketing OS

Diario operativo del progetto. Tono asciutto, onesto. Riporta come si è arrivati alle decisioni, le frizioni reali, ciò che vale ricordare a 6 mesi di distanza. Non duplica codice, ADR o CLAUDE.md.

Entry in ordine cronologico inverso (più recenti in alto). Le entry future vengono aggiunte da un agent automatico giornaliero (vedi sezione *Automazione* in fondo).

---

## 2026-05-01 — Sessione 1: Bootstrap monorepo

**Stato**: chiusa. 3 commit su `origin/main` (`afab0ae`, `013e81a`, `13f275a`).

### Cosa è stato fatto
- Struttura repo (4 cartelle): `core-api/`, `web-dashboard/`, `pipelines/`, `infrastructure/`.
- `core-api` operativo: Python 3.12 + Poetry 2.3 + FastAPI 0.136 + SQLAlchemy 2.0 async + Alembic 1.18 + Anthropic SDK 0.97 + OpenAI SDK 2.33 + structlog 25.5. `/health` risponde, ruff e mypy strict puliti.
- `web-dashboard` operativo: Next.js 16.2 + React 19 + Tailwind v4 + shadcn/ui (preset `base-nova`, neutral) + ESLint 9 + Prettier. Build, lint, typecheck verdi.
- ADR-0001 (stack iniziale) in `infrastructure/docs/architecture/decisions/`.
- Connessione a Supabase verificata (Postgres 17.6, pooler `eu-west-1`, session mode).
- Repo pubblicato su [github.com/qpallante/Marketing-OS](https://github.com/qpallante/Marketing-OS).

### Problemi incontrati
1. **`create-next-app@latest` ha installato Next.js 16.2, non 15** come previsto da CLAUDE.md. Tailwind v4 e React 19 a cascata. CLAUDE.md e ADR-0001 aggiornati per riflettere lo stack reale.
2. **Plan mode attivato a metà esecuzione**. Dovuto riscrivere il piano per le parti rimanenti (file in `~/.claude/plans/`).
3. **`shadcn init --base-color=neutral` ha fallito**: il flag non esiste più nella CLI corrente (`shadcn@4.6.0`). Risolto con `init -d` (preset `base-nova`, default neutral). Effetto collaterale: il preset ha generato `src/components/ui/button.tsx`, lasciato in repo (verrà comunque usato dalla Sessione 4).
4. **`sqlalchemy[asyncio]` non ha pullato `greenlet`** come dipendenza transitiva. Errore a runtime sul primo `engine.connect()`. Aggiunto `greenlet` come dipendenza diretta in `pyproject.toml`.
5. **Endpoint diretto `db.<ref>.supabase.co` non risolve in DNS**. I progetti Supabase nuovi (post-2024) espongono solo il connection pooler.
6. **Primo tentativo pooler `eu-central-1` ha risposto "Tenant or user not found"**. Sondate 14 regioni in parallelo: progetto effettivamente su **`eu-west-1`** (Ireland).
7. **`mv 07_Prompt_Claude_Code.md ~/Documents/`** dell'utente è fallito: Mac in italiano usa `~/Documenti`. File aggiunto a `.gitignore` (resta locale ma fuori dai commit).
8. **Primo commit con committer auto-rilevato** (`quintinopallante@MacBook-Pro-di-Quintino.local`) perché `git config --global user.name/email` non era impostata. Utente ha settato config + amendato il commit. Hash cambiato `6098547 → afab0ae`; CLAUDE.md disallineato fixato in un commit successivo (`13f275a`).
9. **`npm audit` segnala 2 moderate** in `postcss <8.5.10` annidato in `node_modules/next/`. Falso positivo: il fix proposto da npm è il downgrade a Next 9.3.3 (di 7 major version, non utilizzabile). Ignorato; rivalutare con Next 16.3+.

### Lezioni apprese
- **Le CLI cambiano interfaccia velocemente.** shadcn ha cambiato API tra v3 e v4. Sempre `<cli> --help` prima di scrivere flag a memoria, soprattutto per tool installati al volo via `npx@latest`.
- **`create-next-app@latest` ≠ stabile.** Pulla sempre la major più recente, non quella citata nei docs scritti in passato. Da accettare e documentare in ADR, non combattere.
- **SQLAlchemy `[asyncio]` extras è inaffidabile** sull'inclusione di `greenlet`. Aggiungerlo sempre esplicitamente per evitare errori a runtime.
- **Supabase: prendere il connection string dal dashboard.** Nessuna assunzione su `db.<ref>.supabase.co` (è scomparso). Pooler URL contiene username modificato (`postgres.<ref>`) e regione che va verificata caso per caso.
- **`git config --global user.name/email` PRIMA del primo commit** su una macchina fresh. Altrimenti git auto-rileva da hostname e i commit non si linkano al profilo GitHub. Force-push richiesto se ci si accorge dopo il push.
- **Mac in italiano: home folders in italiano.** `~/Documenti`, `~/Scrivania`, `~/Scaricati`. Mai assumere percorsi inglesi.
- **AGENTS.md/CLAUDE.md generati dai template** (es. `web-dashboard/CLAUDE.md` da create-next-app) vanno letti subito: avvisano di breaking changes che il modello non conosce.

### Decisioni rilevanti
- **Stack frontend**: Next.js 16 + Tailwind v4 + React 19 (deviazione dichiarata da CLAUDE.md, accettata, motivata in ADR-0001).
- **Pooler Supabase**: session mode (port 5432) per dev/migrations. Transaction mode (6543) da rivalutare per produzione serverless quando ci arriviamo.
- **Componenti shadcn**: aggiunti on-demand a partire da Sessione 4. Solo `Button` presente per effetto collaterale del preset init.
- **`07_Prompt_Claude_Code.md`**: locale, gitignored. Resta consultabile ma fuori dai commit.
- **`supabase/config.toml`**: committato (config locale CLI, no secrets). `.env` con secrets è gitignored.

### Da ricordare
- Project ref Supabase: `txmkxllfhzrfetordkap` · Region: `eu-west-1` · Postgres: `17.6`.
- Email committer corrente: `241859981+qpallante@users.noreply.github.com` (GitHub noreply).
- `web-dashboard/AGENTS.md` impone lettura di `node_modules/next/dist/docs/` prima di scrivere codice Next nuovo. Da rispettare nelle Sessioni 4+.
- Per Sessione 9 (Brand Brain): abilitare extension `pgvector` lato Supabase con `CREATE EXTENSION IF NOT EXISTS vector;`.
- CI/CD non ancora configurato. Da affrontare quando il blocco di codice supera la review locale (probabilmente Sessione 5-6).

### Prossimo
**Sessione 2** (pianificata): Alembic init async + primo schema (`Client`, `User`, `PlatformAccount`, `AuditLog`) + RLS policies + seed dev (super-admin + Monoloco + user admin client).

---

## Automazione

Le entry future del journal sono pensate per essere aggiunte da un agent giornaliero schedulato (via `/schedule`). Setup pendente — da configurare con scope da concordare:

- **Cosa fa l'agent ogni giorno**: legge la git history dall'ultima entry, somma una nuova entry datata con (a) commit del giorno, (b) update di "Prossimo step" in base al `07_Prompt_Claude_Code.md` o ai TODO emersi, (c) eventuali decisioni/problemi non ancora documentati.
- **Cadenza**: da decidere (giornaliero a fine giornata? settimanale lunedì?).
- **Skip se nessuna attività**: se zero commit nel periodo, l'agent non aggiunge entry vuote (preferibile a un journal pieno di "TBD").

Quando lo scope è confermato, l'agent viene attivato; finché non lo è, le entry future sono aggiunte manualmente nelle sessioni di lavoro.
