"use client";

import { useActionState, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { acceptInviteAction, type AcceptInviteFormState } from "@/lib/actions/auth";
import { cn } from "@/lib/utils";

const initialState: AcceptInviteFormState = {};

interface AcceptInviteFormProps {
  /** Token plaintext dal `?token=` query param. Hidden input, riemesso dal Server Action. */
  token: string;
  /** Email pre-compilata dal preview backend (readonly UI, non sub-mittibile). */
  email: string;
  /** Ruolo che l'utente assumerà — display purposes (non submit). */
  role: "client_admin" | "client_member";
  /** Nome del client per UX. */
  clientName: string;
}

/**
 * Form di accept-invite — Client Component (richiede `useActionState` +
 * `useState` per il counter live e la match check di confirm-password,
 * entrambi hook React solo client-side).
 *
 * **Validazione live (client-side, UX)**:
 *  - Counter `password.length / 12` con colore rosso<12 / verde≥12
 *  - Confirm password match check
 *  - Submit disabled finché non valido
 *
 * **Validazione autorevole (server-side, sicurezza)**:
 *  - `acceptInviteAction` ricontrolla token len=43 + password 12-128 char.
 *  - Il client può essere bypassato (DevTools, curl), il server è la fonte
 *    di verità.
 *
 * **Confirm password NON viene inviato al server**: è solo client-side guard
 * contro typo. Pattern standard, evita di duplicare logica server-side che
 * non aggiunge sicurezza (l'utente potrebbe sempre saltare il client).
 */
export function AcceptInviteForm({ token, email, role, clientName }: AcceptInviteFormProps) {
  const [state, formAction, isPending] = useActionState(acceptInviteAction, initialState);

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  const passwordValid = password.length >= 12;
  const passwordsMatch = password === confirmPassword && password.length > 0;
  const canSubmit = passwordValid && passwordsMatch && !isPending;

  return (
    <form action={formAction} className="space-y-4" noValidate>
      <input type="hidden" name="token" value={token} />

      {state.error ? (
        <div
          role="alert"
          className="bg-destructive/10 text-destructive rounded-md px-3 py-2 text-sm"
        >
          {state.error}
        </div>
      ) : null}

      <div className="space-y-1.5">
        <Label htmlFor="email-preview">Email</Label>
        <Input
          id="email-preview"
          type="email"
          value={email}
          readOnly
          disabled
          className="bg-muted/40"
        />
        <p className="text-muted-foreground text-xs">
          Diventerai <strong>{role === "client_admin" ? "admin" : "membro"}</strong> di{" "}
          <strong>{clientName}</strong>
        </p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="password">Password</Label>
        <Input
          id="password"
          name="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={12}
          maxLength={128}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={isPending}
          aria-invalid={password.length > 0 && !passwordValid ? true : undefined}
          aria-describedby="password-hint"
        />
        <p
          id="password-hint"
          className={cn("text-xs", passwordValid ? "text-emerald-600" : "text-muted-foreground")}
        >
          {password.length} / 12 caratteri minimi
        </p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="confirm-password">Conferma password</Label>
        <Input
          id="confirm-password"
          type="password"
          autoComplete="new-password"
          required
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          disabled={isPending}
          aria-invalid={confirmPassword.length > 0 && !passwordsMatch ? true : undefined}
          aria-describedby={
            confirmPassword.length > 0 && !passwordsMatch ? "confirm-error" : undefined
          }
        />
        {confirmPassword.length > 0 && !passwordsMatch ? (
          <p id="confirm-error" className="text-destructive text-xs" role="alert">
            Le password non corrispondono
          </p>
        ) : null}
      </div>

      <Button type="submit" disabled={!canSubmit} className="w-full">
        {isPending ? "Creazione account…" : "Crea account e accedi"}
      </Button>
    </form>
  );
}
