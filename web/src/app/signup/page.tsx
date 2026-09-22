import { redirect } from "next/navigation";
import { AuthForm } from "@/components/AuthForm";
import { getSessionUser } from "@/lib/auth";

export const metadata = { title: "Create account — OnCall" };

export default async function SignupPage({
  searchParams,
}: { searchParams: Promise<{ error?: string }> }) {
  if (await getSessionUser()) redirect("/dashboard");
  // 🔴 The no-JS path's only feedback channel: the API route redirects
  //    back here with ?error=<message>. A message, never the credentials —
  //    see lib/formrequest.ts.
  const { error } = await searchParams;
  return <AuthForm mode="signup" initialError={error} />;
}
