import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function ContentPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Contenuti</h1>
        <p className="text-muted-foreground mt-1 text-sm">Pipeline editoriale e Brand Brain</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Pipeline editoriale</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">
            Caption Agent, generazione media e calendar editoriale arriveranno con il Brand Brain
            (Sessioni 9-10).
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
