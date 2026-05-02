import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { NewClientForm } from "./new-client-form";

/**
 * Pagina di creazione di un nuovo client. Server Component thin: si limita a
 * impostare layout/header e a montare il `NewClientForm` (Client Component
 * per via di `useActionState`). La logica di submit + post-success è dentro
 * il form.
 */
export default function NewClientPage() {
  return (
    <div className="mx-auto w-full max-w-2xl space-y-6">
      <div>
        <Button render={<Link href="/admin/clients" />} variant="ghost" size="sm">
          ← Torna alla lista
        </Button>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight">Nuovo cliente</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Crea un client e invia un invito al primo amministratore.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Dettagli</CardTitle>
        </CardHeader>
        <CardContent>
          <NewClientForm />
        </CardContent>
      </Card>
    </div>
  );
}
