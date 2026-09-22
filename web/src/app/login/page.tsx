import { redirect } from "next/navigation";
import { AuthForm } from "@/components/AuthForm";
import { getSessionUser } from "@/lib/auth";

export const metadata = { title: "Sign in — OnCall" };

export default async function LoginPage({
  searchParams,
}: { searchParams: Promise<{ error?: string }> }) {
  // Already signed in? Skip the form. Showing a login page to a logged-in user
  // is a small thing that makes a product feel unfinished.
  if (await getSessionUser()) redirect("/dashboard");
  // 🔴 The no-JS path's only feedback channel: the API route redirects
  //    back here with ?error=<message>. A message, never the credentials —
  //    see lib/formrequest.ts.
  const { error } = await searchParams;
  return <AuthForm mode="login" initialError={error} />;
}
