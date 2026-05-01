import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function DashboardPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Dashboard</h1>
        <p className="text-muted-foreground mt-1 text-sm">Panoramica generale</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Placeholder</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">
            Sezione in costruzione. KPI, overview clienti e performance media arriveranno con il
            modulo Performance (Sessioni 9-10).
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
