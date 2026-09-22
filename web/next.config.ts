import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /**
   * 🔴 Why this exists — a bug that broke login entirely.
   *
   * Next 16 treats `localhost` as the canonical dev origin and BLOCKS its dev
   * resources (`/_next/static/chunks/…`, the HMR socket) when the page is
   * loaded from any other host. `127.0.0.1` counts as "any other host", even
   * though it is the same machine.
   *
   * The failure was not obvious. The page rendered fine — server-side HTML
   * does not need those chunks. But the client bundle 403'd, so React never
   * hydrated, so the login form's onSubmit handler was never attached, so the
   * browser fell back to a NATIVE form submission.
   *
   * And a native <form> with no method attribute submits GET. Which put the
   * user's password in the URL.
   *
   * Two fixes, and both were needed: this option so the chunks load, and
   * `method="post"` on the form so that a hydration failure can never leak
   * credentials again. Depending on this option alone would mean any future
   * JS failure — an extension, a network blip, a runtime error — reopens the
   * same hole.
   */
  allowedDevOrigins: ["127.0.0.1"],
};

export default nextConfig;
