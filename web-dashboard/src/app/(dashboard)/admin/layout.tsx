import { redirect } from "next/navigation";

import { getCurrentUser } from "@/lib/api/auth-context";

/**
 * Route group `(dashboard)/admin` — guard server-side per super_admin.
 *
 * Doppio gate (defense-in-depth):
 *  1. **Backend RLS** (autorevole): le route `/api/v1/admin/*` hanno
 *     `Depends(require_super_admin)` + RLS che blocca SELECT/INSERT a chi non
 *     è super_admin (ADR-0001). Anche con UI bypassed, un client_admin
 *     riceverebbe 403.
 *  2. **UI guard qui** (UX): redirect silenzioso a `/dashboard` per non
 *     esporre l'esistenza dell'area admin a chi non ha permessi. Niente 403
 *     page: meno information disclosure rispetto a un errore esplicito.
 *
 * `getCurrentUser()` è React.cache()-wrapped: questa fetch condivide la
 * stessa request del layout dashboard padre (`(dashboard)/layout.tsx`), zero
 * overhead aggiuntivo. Gli errori `AuthRequiredError` / `NetworkError` sono
 * già gestiti dal layout padre — qui arriviamo solo se quello ha già
 * caricato l'utente con successo.
 */
export default async function AdminLayout({ children }: { children: React.ReactNode }) {
  const user = await getCurrentUser();

  if (user.role !== "super_admin") {
    redirect("/dashboard");
  }

  return <>{children}</>;
}
