import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { backendFetch } from "@/lib/api/server-fetch";
import type { ClientStatus, ListClientsResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Lista clients per super_admin. Server Component → fetch sincrono via
 * `backendFetch` (cookie attached automaticamente). RLS garantisce che
 * arriviamo qui solo se l'utente è super_admin (vedi `admin/layout.tsx`),
 * ma il backend ricontrolla via `Depends(require_super_admin)` —
 * defense-in-depth.
 *
 * **Cache**: `backendFetch` usa `cache: "no-store"` di default. La lista è
 * dinamica (gli admin creano client) e `revalidatePath("/admin/clients")` da
 * `createClientAction` invalida questa rotta per la prossima visita.
 */
export default async function AdminClientsPage() {
  const res = await backendFetch("/api/v1/admin/clients");

  if (!res.ok) {
    return <ListErrorState status={res.status} />;
  }

  const data = (await res.json()) as ListClientsResponse;
  const clients = data.clients;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Clients</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Tutti i clienti gestiti dalla piattaforma
          </p>
        </div>
        <Button render={<Link href="/admin/clients/new" />}>Nuovo cliente</Button>
      </div>

      {clients.length === 0 ? (
        <EmptyState />
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {clients.map((c) => (
            <li key={c.id}>
              <Card>
                <CardHeader>
                  <div className="flex items-start justify-between gap-2">
                    <CardTitle>{c.name}</CardTitle>
                    <StatusBadge status={c.status} />
                  </div>
                  <CardDescription>
                    <code className="text-xs">{c.slug}</code>
                  </CardDescription>
                </CardHeader>
                {c.created_at ? (
                  <CardContent>
                    <p className="text-muted-foreground text-xs">
                      Creato il <time dateTime={c.created_at}>{formatDate(c.created_at)}</time>
                    </p>
                  </CardContent>
                ) : null}
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <Card>
      <CardContent className="py-12 text-center">
        <p className="text-muted-foreground text-sm">Nessun cliente ancora.</p>
        <div className="mt-4">
          <Button render={<Link href="/admin/clients/new" />} variant="outline" size="sm">
            Crea il primo →
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function ListErrorState({ status }: { status: number }) {
  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-semibold tracking-tight">Clients</h1>
      <Card>
        <CardContent className="py-8">
          <p className="text-destructive text-sm">
            Impossibile caricare la lista (HTTP {status}). Ricarica la pagina o riprova più tardi.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

const STATUS_STYLES: Record<ClientStatus, string> = {
  active: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300",
  paused: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300",
  archived: "bg-muted text-muted-foreground",
};

const STATUS_LABELS: Record<ClientStatus, string> = {
  active: "Attivo",
  paused: "In pausa",
  archived: "Archiviato",
};

function StatusBadge({ status }: { status: ClientStatus }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        STATUS_STYLES[status],
      )}
    >
      {STATUS_LABELS[status]}
    </span>
  );
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("it-IT", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
  } catch {
    return iso;
  }
}
