# Architecture Decision Records (ADR)

Questa cartella contiene gli **Architecture Decision Records** del Marketing OS.

## Cos'è un ADR

Un ADR è un documento breve che cattura **una decisione architetturale significativa**, il **contesto** in cui è stata presa e le **conseguenze** previste. Il formato deriva da [Michael Nygard, "Documenting Architecture Decisions" (2011)](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).

Lo scopo non è documentare ogni linea di codice, ma lasciare traccia delle **scelte non ovvie** che impattano l'evoluzione del sistema: scelta di stack, pattern di integrazione, trade-off di costi, deroghe a standard interni, ecc. Un buon ADR risponde a "perché abbiamo fatto così?" sei mesi dopo, quando nessuno se lo ricorda.

## Quando scrivere un ADR

Scrivi un ADR quando:

- Introduci una nuova dipendenza o servizio cloud strutturale
- Definisci un nuovo pattern architetturale (es. event-driven, multi-tenant policy, ecc.)
- Cambi una decisione precedente (Status: `Superseded by ADR-NNNN`)
- Fai una scelta tra alternative concrete dove la motivazione non è ovvia dal codice

**Non** scrivere un ADR per:

- Refactor locali, bug fix, scelte di naming
- Decisioni reversibili a basso costo (preferenze stilistiche)
- Cose già documentate in `CLAUDE.md`

## Convenzione di naming

```
NNNN-titolo-kebab-case.md
```

dove `NNNN` è un numero progressivo a 4 cifre (`0001`, `0002`, …). Una volta accettato, il numero non cambia. Mai riusare un numero.

## Stati possibili

- `Proposed` — bozza in discussione
- `Accepted` — approvato, in vigore
- `Deprecated` — non più rilevante, ma non sostituito
- `Superseded by ADR-NNNN` — sostituito da una decisione successiva

## Template

Copia [`TEMPLATE.md`](#template-completo) in un nuovo file e compila.

### Template completo

```markdown
# ADR-NNNN — Titolo conciso della decisione

- **Status**: Proposed | Accepted | Deprecated | Superseded by ADR-NNNN
- **Date**: YYYY-MM-DD
- **Authors**: Nome Cognome

## Context

Cosa stiamo cercando di risolvere? Quali sono i vincoli (tecnici, di costo, di tempo, organizzativi)? Cosa abbiamo considerato come alternative? Sii onesto: se la decisione è guidata da pragmatismo o budget, dillo.

## Decision

Cosa abbiamo deciso, in modo dichiarativo. Frase del tipo "Useremo X per Y perché Z".

## Consequences

Cosa cambia di conseguenza? Sia positive che negative:

- **Positive**: vantaggi concreti
- **Negative / Trade-off**: limiti, debt accettato, lock-in, costi
- **Da rivalutare quando**: condizioni che potrebbero invalidare la decisione (es. "se superiamo 50 clienti", "se Anthropic lancia X")

## Alternatives considered

Breve elenco di cosa abbiamo valutato e perché è stato scartato. Aiuta chi legge sei mesi dopo a non riaprire la stessa discussione.

## References

Link a documenti, RFC, benchmark, conversazioni Slack/issue rilevanti.
```

## Indice ADR

| ID | Titolo | Status |
|----|--------|--------|
| [0001](./0001-stack-tecnologico-iniziale.md) | Stack tecnologico iniziale | Accepted |
| [0002](./0002-rls-strategy.md) | RLS Strategy: per-session settings con `current_setting` | Accepted |
| [0003](./0003-jwt-authentication-strategy.md) | JWT authentication strategy (backend) | Accepted |
| [0004](./0004-frontend-auth-strategy.md) | Frontend authentication strategy (Next.js BFF) | Accepted |
| [0005](./0005-refresh-token-auto-rotation.md) | Refresh token auto-rotation strategy | Accepted |
