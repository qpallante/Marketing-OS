"use client";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { GenerateTab } from "./generate-tab";

interface BrandBrainTabsProps {
  /** UUID v4 del client. Passato dal Server Component parent. */
  clientId: string;
}

/**
 * Tab navigation per il modulo Brand Brain.
 *
 * S7 step 7b: solo "Genera" è funzionante end-to-end. "Assets" e "Storico"
 * sono placeholder con messaggio "in arrivo in S7-bis" — l'implementazione
 * full UI è scoped a una sessione successiva per ridurre il blast radius.
 *
 * Client Component perché shadcn `Tabs` (base-ui) richiede client runtime
 * per gestire lo stato del tab attivo.
 */
export function BrandBrainTabs({ clientId }: BrandBrainTabsProps) {
  return (
    <Tabs defaultValue="genera">
      <TabsList>
        <TabsTrigger value="assets">Assets</TabsTrigger>
        <TabsTrigger value="genera">Genera</TabsTrigger>
        <TabsTrigger value="storico">Storico</TabsTrigger>
      </TabsList>
      <TabsContent value="assets">
        <ComingSoonPanel
          title="Gestione asset"
          description="Upload PDF, snippet di testo, brand form (tone of voice, do/don't, colori) — in arrivo in S7-bis. Per ora popola gli asset via API: POST /api/v1/clients/{client_id}/brand/assets/text."
        />
      </TabsContent>
      <TabsContent value="genera">
        <GenerateTab clientId={clientId} />
      </TabsContent>
      <TabsContent value="storico">
        <ComingSoonPanel
          title="Storico generazioni"
          description="Cronologia delle query passate con filtri e re-run — in arrivo in S7-bis."
        />
      </TabsContent>
    </Tabs>
  );
}

function ComingSoonPanel({ title, description }: { title: string; description: string }) {
  return (
    <div className="border-border bg-muted/40 mt-6 rounded-md border border-dashed p-8 text-center">
      <p className="text-foreground text-sm font-medium">{title}</p>
      <p className="text-muted-foreground mx-auto mt-2 max-w-md text-xs">{description}</p>
    </div>
  );
}
