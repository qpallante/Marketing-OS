import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function AnalyticsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Analytics</h1>
        <p className="text-muted-foreground mt-1 text-sm">Performance media e insights</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Insights</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">
            Dashboard insights, predittivi e ROI tracking arriveranno con il Data Lake (Sessioni
            7-8).
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
