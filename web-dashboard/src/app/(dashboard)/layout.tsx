import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";

import { Sidebar } from "@/components/dashboard/sidebar";
import { Topbar } from "@/components/dashboard/topbar";
import { AuthRequiredError, NetworkError, getCurrentUser } from "@/lib/api/auth-context";

/**
 * Layout autenticato per /dashboard, /content, /analytics, /settings.
 *
 * **Auth flow update Sessione 4-bis**:
 *  - PRIMA: 401 da /me → redirect `/api/auth/clear?to=/login` (utente sloggato).
 *  - DOPO:  401 da /me → redirect `/api/auth/rotate?to=<currentPath>` (tenta refresh)
 *           - rotation OK → cookies aggiornati + redirect al currentPath
 *           - rotation FAIL (refresh scaduto, network, etc.) → /api/auth/rotate
 *             stesso fa redirect a /api/auth/clear → /login
 *
 * Il layout NON gestisce più 401 con cleanup diretto. Delega tutto a
 * `/api/auth/rotate`, che è il single source of truth per la token rotation.
 *
 * **Double-check pattern** col proxy (src/proxy.ts):
 *  - proxy fa presence check del cookie e blocca anonimi prima del rendering
 *    (veloce, no DB/backend call)
 *  - questo layout fa il check serio: chiamata `/api/v1/auth/me`
 *    - 200 → user caricato, prop a sidebar/topbar
 *    - 401 → redirect a `/api/auth/rotate?to=<currentPath>` (workaround alla
 *      limitazione "Server Components non possono cookies().delete/set"
 *      in Next 15+/16)
 *    - network error → NetworkErrorPage (NO cleanup cookie: blip temporaneo,
 *      l'utente può ricaricare quando il backend torna)
 */

/**
 * Ricostruisce path + query string corrente dai header `x-pathname` e
 * `x-search` propagati da `src/proxy.ts` (workaround: Server Components
 * non hanno `usePathname()`/`useSearchParams()`, sono hook Client).
 *
 * Defensive validation: il pathname deve iniziare con `/` ma NON con `//`
 * (anti protocol-relative URL injection — scenario improbabile dietro
 * Vercel ma defense-in-depth). Il search non viene validato qui (è già
 * url-encoded dal browser).
 *
 * Ritorna pathname+search per preservare il contesto utente nella
 * redirect chain (es. /settings?tab=integrations → rotate → /settings?tab=integrations).
 */
async function getCurrentPathname(): Promise<string> {
  const h = await headers();
  const path = h.get("x-pathname");
  const search = h.get("x-search") ?? "";
  if (!path || !path.startsWith("/") || path.startsWith("//")) {
    return "/dashboard";
  }
  return path + search;
}

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
      // user disabled). Tenta rotation via Route Handler dedicato. Se il
      // refresh fallisce a sua volta, /api/auth/rotate redirige a
      // /api/auth/clear → /login (cleanup definitivo).
      const currentPath = await getCurrentPathname();
      console.info(
        JSON.stringify({
          action: "auth_rotation_triggered",
          from_path: currentPath,
          ts: new Date().toISOString(),
        }),
      );
      redirect(`/api/auth/rotate?to=${encodeURIComponent(currentPath)}`);
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
        <Sidebar isSuperAdmin={user.role === "super_admin"} />
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
