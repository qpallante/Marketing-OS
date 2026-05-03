import type { Metadata } from "next";

import { getCurrentUser } from "@/lib/api/auth-context";

import { BrandBrainTabs } from "./_components/brand-brain-tabs";

export const metadata: Metadata = {
  title: "Brand Brain | Marketing OS",
};

/**
 * `/brand-brain` — entry point del modulo Brand Brain (S7).
 *
 * Server Component: carica l'utente corrente (riusa la cache di `getCurrentUser`
 * già hit-tata dal `(dashboard)/layout.tsx`, quindi 0 fetch addizionali) e
 * passa il `client_id` ai tab Client.
 *
 * **super_admin** ha `client_id = null` (cross-tenant). Senza un client
 * context attivo non possiamo chiamare gli endpoint `/api/v1/clients/{id}/brand/*`
 * → mostriamo un empty state. La selezione di un client per super_admin è
 * out-of-scope S7 (verrà costruita quando esisterà uno switcher globale).
 */
export default async function BrandBrainPage() {
  const user = await getCurrentUser();

  if (!user.client_id) {
    return (
      <section className="space-y-2">
        <h1 className="text-2xl font-semibold">Brand Brain</h1>
        <p className="text-muted-foreground text-sm">
          Nessun client attivo. Il Brand Brain richiede un client context: usa l&apos;area Admin per
          selezionare un client.
        </p>
      </section>
    );
  }

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold">Brand Brain</h1>
        <p className="text-muted-foreground text-sm">
          {user.client?.name
            ? `Gestione asset, generazione contenuti e storico per ${user.client.name}.`
            : "Gestione asset, generazione contenuti e storico."}
        </p>
      </header>
      <BrandBrainTabs clientId={user.client_id} />
    </section>
  );
}
