"use client";

import { useActionState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { loginAction, type LoginFormState } from "@/lib/actions/auth";

const initialState: LoginFormState = {};

interface LoginFormProps {
  next: string;
  defaultEmail?: string;
}

export function LoginForm({ next, defaultEmail }: LoginFormProps) {
  const [state, formAction, isPending] = useActionState(loginAction, initialState);

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

      <input type="hidden" name="next" value={next} />

      <div className="space-y-1.5">
        <Label htmlFor="email">Email</Label>
        <Input
          id="email"
          name="email"
          type="email"
          autoComplete="username"
          defaultValue={defaultEmail}
          required
          disabled={isPending}
          aria-invalid={state.fieldErrors?.email ? true : undefined}
          aria-describedby={state.fieldErrors?.email ? "email-error" : undefined}
        />
        {state.fieldErrors?.email ? (
          <p id="email-error" className="text-destructive text-sm">
            {state.fieldErrors.email}
          </p>
        ) : null}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="password">Password</Label>
        <Input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          disabled={isPending}
          aria-invalid={state.fieldErrors?.password ? true : undefined}
          aria-describedby={state.fieldErrors?.password ? "password-error" : undefined}
        />
        {state.fieldErrors?.password ? (
          <p id="password-error" className="text-destructive text-sm">
            {state.fieldErrors.password}
          </p>
        ) : null}
      </div>

      <Button type="submit" disabled={isPending} className="w-full">
        {isPending ? "Accesso in corso…" : "Accedi"}
      </Button>
    </form>
  );
}
