import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { backendFetch } from "@/lib/api/server-fetch";

import { LoginForm } from "./login-form";

interface LoginPageProps {
  searchParams: Promise<{ next?: string; email?: string }>;
}

const FALLBACK_NEXT = "/dashboard";

function sanitizeNext(next: string | undefined): string {
  if (!next) return FALLBACK_NEXT;
  if (!next.startsWith("/")) return FALLBACK_NEXT;
  if (next.startsWith("//")) return FALLBACK_NEXT;
  if (next.includes("://")) return FALLBACK_NEXT;
  return next;
}

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const params = await searchParams;
  const next = sanitizeNext(params.next);

  const cookieStore = await cookies();
  const hasToken = Boolean(cookieStore.get("access_token")?.value);

  if (hasToken) {
    // Cookie presente: verifica validità via /me prima di rendere /login.
    // Se valido → l'utente è già loggato, manda direttamente a destinazione.
    // Se invalido (401) → cleanup via Route Handler dedicato (i Server
    // Component non possono cancellare cookie direttamente).
    const meRes = await backendFetch("/api/v1/auth/me");
    if (meRes.ok) {
      redirect(next);
    }
    const clearParams = new URLSearchParams({ to: "/login" });
    if (params.email) clearParams.set("email", params.email);
    redirect(`/api/auth/clear?${clearParams.toString()}`);
  }

  return (
    <main className="bg-muted/40 flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader className="space-y-1">
          <CardTitle className="text-2xl">Marketing OS</CardTitle>
          <CardDescription>Accedi al tuo account</CardDescription>
        </CardHeader>
        <CardContent>
          <LoginForm next={next} defaultEmail={params.email} />
        </CardContent>
      </Card>
    </main>
  );
}
