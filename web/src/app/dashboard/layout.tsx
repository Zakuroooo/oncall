import { redirect } from "next/navigation";
import { getSessionUser } from "@/lib/auth";
import { DashboardShell } from "@/components/DashboardShell";

/**
 * The auth gate for everything under /dashboard.
 *
 * 🔴 A layout, not middleware, and not a check inside each page.
 *
 *    In the App Router a layout wraps every route beneath it, so a new page
 *    added under /dashboard is protected the moment the file exists — nobody
 *    has to remember to add a guard. The failure mode of per-page checks is
 *    the page somebody forgets, and that page is never the boring one.
 *
 *    It runs on the server, so the redirect happens before any dashboard HTML
 *    is generated. Nothing is sent to an unauthenticated browser and then
 *    hidden with CSS.
 */
export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const user = await getSessionUser();
  if (!user) redirect("/login");
  return <DashboardShell user={user}>{children}</DashboardShell>;
}
