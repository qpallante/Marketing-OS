import { cookies } from "next/headers";
import { NextResponse, type NextRequest } from "next/server";

/**
 * Route Handler per cancellare i cookie auth e redirigere altrove.
 *
 * Esiste perché in Next.js 15+/16 i **Server Components non possono mutare
 * cookies** (cookies().delete() funziona solo in Server Action o Route
 * Handler). Quando una pagina (es. /login o /dashboard layout) deve cleanup
 * un cookie stale a seguito di un 401 dal backend, redirige qui con `?to=...`.
 *
 * Sicuro contro open-redirect: `to` deve essere un path interno.
 */

const FALLBACK_TO = "/login";

function sanitizeTo(to: string | null): string {
  if (!to) return FALLBACK_TO;
  if (!to.startsWith("/")) return FALLBACK_TO;
  if (to.startsWith("//")) return FALLBACK_TO;
  if (to.includes("://")) return FALLBACK_TO;
  return to;
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  const cookieStore = await cookies();
  cookieStore.delete("access_token");
  cookieStore.delete("refresh_token");

  const to = sanitizeTo(request.nextUrl.searchParams.get("to"));
  const email = request.nextUrl.searchParams.get("email");

  const url = new URL(to, request.url);
  if (email) url.searchParams.set("email", email);

  return NextResponse.redirect(url);
}
