import { Button } from "@/components/ui/button";
import { logoutAction } from "@/lib/actions/auth";
import type { User } from "@/lib/types";

interface TopbarProps {
  user: User;
}

/**
 * Topbar Server Component. Usa `<form action={logoutAction}>` (Server Action
 * pattern Next.js 16 / React 19) invece di un Client Component — più semplice,
 * niente runtime JS extra, l'azione gira interamente server-side.
 */
export function Topbar({ user }: TopbarProps) {
  return (
    <header className="bg-background flex h-14 items-center justify-between border-b px-6">
      <div className="text-sm font-medium tracking-tight md:hidden">Marketing OS</div>

      <div className="ml-auto flex items-center gap-4">
        <span className="text-muted-foreground text-sm">
          <span className="font-medium">{user.email}</span>
          {user.client ? (
            <>
              <span aria-hidden> · </span>
              <span className="text-foreground">{user.client.name}</span>
            </>
          ) : null}
        </span>
        <form action={logoutAction}>
          <Button type="submit" variant="outline" size="sm">
            Logout
          </Button>
        </form>
      </div>
    </header>
  );
}
