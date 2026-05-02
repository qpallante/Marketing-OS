import Link from "next/link";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { backendFetch } from "@/lib/api/server-fetch";
import type { InvitationPreviewResponse } from "@/lib/types";

import { AcceptInviteForm } from "./accept-invite-form";

/**
 * Pagina pubblica `/accept-invite?token=...` — primo step del flow di
 * onboarding di un nuovo `client_admin` (S6).
 *
 * **Pre-auth**: NON dentro `(dashboard)` route group (niente sidebar/topbar,
 * niente cookie required). Il proxy NON include `/accept-invite` in
 * `PROTECTED_PREFIXES` — la pagina è raggiungibile da utenti anonimi.
 *
 * **Server Component**: chiama `GET /api/v1/auth/invitation/{token}` con
 * `auth: false`. Il backend ritorna 200 con preview oppure 404 generico per
 * QUALSIASI stato invalido (not_found / expired / accepted / revoked) — vedi
 * ADR-0007 §3 "no information disclosure". Il frontend non differenzia il
 * motivo: tutti gli stati invalidi cadono nello stesso `InvitationErrorState`
 * con messaggio generico "Link non valido o scaduto". La differenziazione UX
 * dei sub-stati avviene solo al POST submit (ADR-0007 §"410 detail
 * differenziato").
 *
 * Layout root (`src/app/layout.tsx`) è già minimale: font Inter + metadata.
 * Niente layout custom qui — la `Card` shadcn fornisce il chrome visivo.
 */

interface PageProps {
  searchParams: Promise<{ token?: string }>;
}

export default async function AcceptInvitePage({ searchParams }: PageProps) {
  const params = await searchParams;
  const token = params.token;

  // Token assente o malformato (lunghezza ≠ 43): error state immediato senza
  // chiamare il backend. Risparmia un round-trip per link palesemente rotti
  // (es. utente che incolla URL parziale).
  if (!token || token.length !== 43) {
    return <InvitationErrorState message="Link non valido o malformato." />;
  }

  // Validazione live via backend (endpoint pubblico, no Bearer).
  // `encodeURIComponent` necessario perché il token base64url-safe può
  // contenere `-` e `_`, ma defensive contro futuri changes alla shape token.
  let res: Response;
  try {
    res = await backendFetch(`/api/v1/auth/invitation/${encodeURIComponent(token)}`, {
      auth: false,
    });
  } catch {
    return <InvitationErrorState message="Errore di connessione, riprova tra qualche istante." />;
  }

  if (res.status === 404) {
    // Backend ritorna sempre 404 generico per qualsiasi stato invalido
    // (not_found / expired / accepted / revoked). Frontend non distingue.
    return (
      <InvitationErrorState message="Link non valido o scaduto. Chiedi al super_admin di rigenerarlo." />
    );
  }
  if (!res.ok) {
    return <InvitationErrorState message="Errore del server, riprova tra qualche istante." />;
  }

  let preview: InvitationPreviewResponse;
  try {
    preview = (await res.json()) as InvitationPreviewResponse;
  } catch {
    return <InvitationErrorState message="Errore di connessione, riprova tra qualche istante." />;
  }

  return (
    <main className="bg-muted/40 flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="text-2xl">Setta la tua password</CardTitle>
          <p className="text-muted-foreground mt-1 text-sm">
            Stai per diventare admin di <strong>{preview.client_name}</strong>
          </p>
        </CardHeader>
        <CardContent>
          <AcceptInviteForm
            token={token}
            email={preview.email}
            role={preview.role}
            clientName={preview.client_name}
          />
        </CardContent>
      </Card>
    </main>
  );
}

/**
 * Stato di errore riusato per i 4 casi convergenti (token assente/malformato,
 * 404 backend, network error, parse error). Messaggio generico in italiano,
 * link al login come escape hatch.
 *
 * NB: l'utente che vede questa pagina non deve poter inferire lo stato esatto
 * dell'invitation (defense-in-depth contro information disclosure: già
 * applicato lato backend con 404 sempre generico, qui ribadito a livello UI).
 */
function InvitationErrorState({ message }: { message: string }) {
  return (
    <main className="bg-muted/40 flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Invito non valido</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-muted-foreground text-sm">{message}</p>
          <Link href="/login" className="text-primary text-sm underline">
            Vai al login →
          </Link>
        </CardContent>
      </Card>
    </main>
  );
}
