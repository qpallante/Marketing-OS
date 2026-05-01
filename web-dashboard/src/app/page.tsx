export default function HomePage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-8">
      <div className="flex flex-col items-center gap-4 text-center">
        <span className="bg-muted text-muted-foreground rounded-full px-3 py-1 text-xs font-medium">
          v0.0.0 · pre-alpha
        </span>
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">Marketing OS</h1>
        <p className="text-muted-foreground max-w-md text-balance sm:text-lg">
          Piattaforma multi-tenant per la gestione AI-native di marketing, contenuti e performance
          media.
        </p>
        <p className="text-muted-foreground mt-8 text-sm">
          Sessione 1 — Bootstrap completato. Prossimo step: setup Supabase.
        </p>
      </div>
    </main>
  );
}
