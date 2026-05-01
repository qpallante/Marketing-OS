import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getCurrentUser } from "@/lib/api/auth-context";

export default async function SettingsPage() {
  // Cache hit: il layout ha già chiamato getCurrentUser nella stessa request
  // (React.cache memoizza per request-tree). Niente extra fetch al backend.
  const user = await getCurrentUser();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Impostazioni</h1>
        <p className="text-muted-foreground mt-1 text-sm">Account corrente</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Profilo</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <Field label="ID" value={user.id} mono />
          <Field label="Email" value={user.email} />
          <Field label="Ruolo" value={user.role} />
          <Field label="Stato" value={user.is_active ? "Attivo" : "Disattivato"} />
          {user.client ? (
            <>
              <Field label="Client" value={user.client.name} />
              <Field label="Slug" value={user.client.slug} mono />
              <Field label="Status client" value={user.client.status} />
            </>
          ) : (
            <Field label="Client" value="(super_admin — cross-tenant)" />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="grid grid-cols-[140px_1fr] items-baseline gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className={mono ? "font-mono text-xs" : undefined}>{value}</span>
    </div>
  );
}
