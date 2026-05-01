"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
}

const NAV: readonly NavItem[] = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/content", label: "Contenuti" },
  { href: "/analytics", label: "Analytics" },
  { href: "/settings", label: "Impostazioni" },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="bg-muted/40 hidden w-60 border-r p-4 md:block">
      <div className="mb-6 px-2">
        <h2 className="text-xs font-semibold tracking-wide uppercase">Marketing OS</h2>
      </div>
      <nav className="space-y-1" aria-label="Navigazione principale">
        {NAV.map((item) => {
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
    </aside>
  );
}
