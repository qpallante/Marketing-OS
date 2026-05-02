"use client";

import Link from "next/link";
import { useActionState, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { createClientAction, type CreateClientFormState } from "@/lib/actions/admin";

const initialState: CreateClientFormState = {};

/**
 * Form di creazione client. Pattern stesso di `LoginForm`:
 *  - `useActionState` per state machine pre/in-flight/post submit
 *  - `noValidate` sul form (deleghiamo validazione al Server Action — UX
 *    consistente lato client e server)
 *  - aria-invalid + aria-describedby per a11y dei field errors
 *
 * **Stato di successo**: invece di redirect, mostriamo l'`invitation_url` in
 * un blocco read-only con bottone "Copia link". Il plaintext token esiste
 * SOLO in questa response — dopo non è più recuperabile (in DB c'è SHA-256).
 * Vedi ADR-0006.
 */
export function NewClientForm() {
  const [state, formAction, isPending] = useActionState(createClientAction, initialState);

  if (state.success) {
    return <SuccessPanel data={state.success} />;
  }

  return (
    <form action={formAction} className="space-y-4" noValidate>
      {state.error ? (
        <div
          role="alert"
          className="bg-destructive/10 text-destructive rounded-md px-3 py-2 text-sm"
        >
          {state.error}
        </div>
      ) : null}

      <div className="space-y-1.5">
        <Label htmlFor="name">Nome</Label>
        <Input
          id="name"
          name="name"
          type="text"
          autoComplete="organization"
          required
          minLength={2}
          maxLength={120}
          disabled={isPending}
          placeholder="Es. Monoloco"
          aria-invalid={state.fieldErrors?.name ? true : undefined}
          aria-describedby={state.fieldErrors?.name ? "name-error" : undefined}
        />
        {state.fieldErrors?.name ? (
          <p id="name-error" className="text-destructive text-sm">
            {state.fieldErrors.name}
          </p>
        ) : null}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="slug">Slug</Label>
        <Input
          id="slug"
          name="slug"
          type="text"
          required
          pattern="^[a-z0-9]+(-[a-z0-9]+)*$"
          minLength={2}
          maxLength={100}
          disabled={isPending}
          placeholder="monoloco"
          aria-invalid={state.fieldErrors?.slug ? true : undefined}
          aria-describedby={state.fieldErrors?.slug ? "slug-error slug-hint" : "slug-hint"}
        />
        <p id="slug-hint" className="text-muted-foreground text-xs">
          Solo minuscole, numeri e trattini. Identificativo univoco usato negli URL.
        </p>
        {state.fieldErrors?.slug ? (
          <p id="slug-error" className="text-destructive text-sm">
            {state.fieldErrors.slug}
          </p>
        ) : null}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="admin_email">Email del primo amministratore</Label>
        <Input
          id="admin_email"
          name="admin_email"
          type="email"
          autoComplete="email"
          required
          disabled={isPending}
          placeholder="admin@cliente.com"
          aria-invalid={state.fieldErrors?.admin_email ? true : undefined}
          aria-describedby={state.fieldErrors?.admin_email ? "admin-email-error" : undefined}
        />
        {state.fieldErrors?.admin_email ? (
          <p id="admin-email-error" className="text-destructive text-sm">
            {state.fieldErrors.admin_email}
          </p>
        ) : null}
      </div>

      <div className="flex items-center gap-2 pt-2">
        <Button type="submit" disabled={isPending}>
          {isPending ? "Creazione…" : "Crea client"}
        </Button>
        <Button render={<Link href="/admin/clients" />} variant="ghost" size="default">
          Annulla
        </Button>
      </div>
    </form>
  );
}

interface SuccessPanelProps {
  data: NonNullable<CreateClientFormState["success"]>;
}

/**
 * Pannello mostrato dopo creazione 201. NIENTE redirect: il super_admin
 * deve copiare `invitation_url` qui — è il plaintext one-shot del token.
 */
function SuccessPanel({ data }: SuccessPanelProps) {
  const [copied, setCopied] = useState(false);
  const url = data.invitation.invitation_url;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard API può fallire se non c'è permission o non c'è https.
      // Fallback: l'utente seleziona manualmente dal campo readonly.
    }
  };

  const expires = new Date(data.invitation.expires_at);

  return (
    <div className="space-y-5">
      <div
        role="status"
        className="rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm dark:border-emerald-900/50 dark:bg-emerald-900/20"
      >
        <p className="font-medium text-emerald-900 dark:text-emerald-200">
          Client creato: {data.client.name}
        </p>
        <p className="mt-1 text-emerald-800/80 dark:text-emerald-300/80">
          Slug <code className="font-mono">{data.client.slug}</code> · status {data.client.status}
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="invitation-url">Link di invito (one-shot)</Label>
        <div className="flex gap-2">
          <input
            id="invitation-url"
            type="text"
            readOnly
            value={url}
            className="border-input bg-muted/40 flex-1 rounded-md border px-3 py-2 font-mono text-xs"
            onFocus={(e) => e.currentTarget.select()}
          />
          <Button type="button" variant="outline" onClick={handleCopy}>
            {copied ? "Copiato!" : "Copia link"}
          </Button>
        </div>
        <p className="text-muted-foreground text-xs">
          Inviato a <strong>{data.invitation.email}</strong>. Scade il{" "}
          <time dateTime={data.invitation.expires_at}>
            {expires.toLocaleDateString("it-IT", {
              day: "2-digit",
              month: "long",
              year: "numeric",
            })}
          </time>
          . Salva subito il link: per motivi di sicurezza non sarà più visibile dopo aver lasciato
          questa pagina.
        </p>
      </div>

      <div className="flex items-center gap-2 pt-2">
        <Button render={<Link href="/admin/clients" />}>Torna alla lista</Button>
      </div>
    </div>
  );
}
