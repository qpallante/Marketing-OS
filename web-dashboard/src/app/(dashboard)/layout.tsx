import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { Sidebar } from "@/components/dashboard/sidebar";
import { Topbar } from "@/components/dashboard/topbar";
import { AuthRequiredError, NetworkError, getCurrentUser } from "@/lib/api/auth-context";

/**
 * Layout autenticato per /dashboard, /content, /analytics, /settings.
 *
 * **Double-check pattern** col proxy (src/proxy.ts):
 *  - proxy fa presence check del cookie e blocca anonimi prima del rendering
 *    (veloce, no DB/backend call)
 *  - questo layout fa il check serio: chiamata `/api/v1/auth/me`
 *    - 200 → user caricato, prop a sidebar/topbar
 *    - 401 → token scaduto/invalido: redirect a `/api/auth/clear` (Route
 *      Handler che cancella cookies, dato che i Server Components in
 *      Next 15+/16 NON possono fare cookies().delete() direttamente)
 *    - network error → NetworkErrorPage (NO cleanup cookie: sarebbe
 *      distruttivo per un blip temporaneo, l'utente può ricaricare)
 */
export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const cookieStore = await cookies();
  const hasToken = Boolean(cookieStore.get("access_token")?.value);

  if (!hasToken) {
    // Defensive: il proxy dovrebbe già aver intercettato. Se siamo qui senza
    // cookie è un edge case (race condition / proxy bypassed in dev).
    redirect("/login");
  }

  let user: Awaited<ReturnType<typeof getCurrentUser>>;
  try {
    user = await getCurrentUser();
  } catch (err) {
    if (err instanceof AuthRequiredError) {
      // Token presente ma rifiutato dal backend (scaduto / role changed /
      // user disabled). Cleanup via Route Handler.
      redirect("/api/auth/clear?to=/login");
    }
    if (err instanceof NetworkError) {
      return <NetworkErrorPage />;
    }
    throw err;
  }

  return (
    <div className="flex min-h-screen flex-col">
      <Topbar user={user} />
      <div className="flex flex-1">
        <Sidebar />
        <main className="flex-1 p-6 md:p-8">{children}</main>
      </div>
    </div>
  );
}

function NetworkErrorPage() {
  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <div className="space-y-2 text-center">
        <h1 className="text-xl font-semibold">Errore di connessione</h1>
        <p className="text-muted-foreground text-sm">
          Impossibile contattare il server. Riprova tra qualche istante.
        </p>
      </div>
    </main>
  );
}
