"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
}

interface NavSection {
  /** Etichetta visiva sopra il blocco (uppercase small). */
  heading: string;
  items: readonly NavItem[];
}

const MAIN_NAV: readonly NavItem[] = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/content", label: "Contenuti" },
  { href: "/analytics", label: "Analytics" },
  { href: "/settings", label: "Impostazioni" },
];

const ADMIN_NAV: readonly NavItem[] = [{ href: "/admin/clients", label: "Clients" }];

interface SidebarProps {
  /** Se true, mostra la sezione "Admin" con sub-link a `/admin/clients`. */
  isSuperAdmin?: boolean;
}

/**
 * Sidebar Client Component (richiede `usePathname` per highlight active link
 * — hook Client only). Riceve `isSuperAdmin` dal layout server-side, che ha
 * già caricato l'utente con `getCurrentUser()`. Niente fetch qui.
 *
 * **Role-aware in S5**: se `isSuperAdmin` è true, append blocco "Admin"
 * separato visivamente (nuova heading uppercase, gap top). client_admin /
 * client_member vedono solo `MAIN_NAV` — nessun trace dell'area admin.
 */
export function Sidebar({ isSuperAdmin = false }: SidebarProps) {
  const pathname = usePathname();

  const sections: NavSection[] = [{ heading: "Marketing OS", items: MAIN_NAV }];
  if (isSuperAdmin) {
    sections.push({ heading: "Admin", items: ADMIN_NAV });
  }

  return (
    <aside className="bg-muted/40 hidden w-60 border-r p-4 md:block">
      {sections.map((section, idx) => (
        <div key={section.heading} className={cn(idx > 0 && "mt-6")}>
          <div className="mb-2 px-2">
            <h2 className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">
              {section.heading}
            </h2>
          </div>
          <nav
            className="space-y-1"
            aria-label={idx === 0 ? "Navigazione principale" : "Navigazione admin"}
          >
            {section.items.map((item) => {
              const isActive = pathname === item.href || pathname.startsWith(`${item.href}/`);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={isActive ? "page" : undefined}
                  className={cn(
                    "block rounded-md px-3 py-2 text-sm transition-colors",
                    isActive
                      ? "bg-primary text-primary-foreground"
                      : "hover:bg-accent hover:text-accent-foreground text-foreground/70",
                  )}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </div>
      ))}
    </aside>
  );
}
