import { NextResponse } from "next/server";
import { destroySession } from "@/lib/auth";

/**
 * POST only, never GET.
 *
 * 🔴 A GET logout can be triggered by any <img src="/api/auth/logout"> on any
 *    page on the internet. It is only an annoyance rather than a breach, but
 *    it is the same class of mistake as a GET that deletes a record, and the
 *    fix costs nothing.
 */
export async function POST() {
  await destroySession();
  return NextResponse.json({ ok: true });
}
