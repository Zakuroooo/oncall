/**
 * Rate limiting for the auth routes.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * The problem this solves
 * ────────────────────────────────────────────────────────────────────────────
 * scrypt makes each password guess expensive, which is most of the defence.
 * But "expensive" is not "impossible", and an unthrottled login endpoint lets
 * an attacker take unlimited attempts at whatever pace the server allows. It
 * also makes the endpoint a self-inflicted denial of service: every guess
 * costs US ~16 MB and real CPU, so a few thousand requests a minute will eat
 * the machine long before they crack anything.
 *
 * Two independent buckets, and both must pass:
 *
 *   per IP     — stops one source hammering many accounts
 *   per email  — stops a distributed attempt at ONE account, which per-IP
 *                limiting alone would never see
 *
 * ────────────────────────────────────────────────────────────────────────────
 * 🔴 What this is NOT
 * ────────────────────────────────────────────────────────────────────────────
 * In-process memory. That means:
 *
 *   - it resets on restart
 *   - it does not coordinate across multiple instances
 *
 * For a single local instance that is the correct trade — Redis for a
 * localhost tool would be a dependency and a container to earn nothing. If
 * this were ever deployed behind more than one instance, this file is the
 * thing that has to move to shared storage, and pretending otherwise would be
 * the mistake. Written down rather than discovered later.
 *
 * The algorithm is a fixed window, not a token bucket: simpler to read, and
 * its only real weakness (double the limit across a window boundary) does not
 * matter when the limit is 5 and the window is 15 minutes.
 */

type Bucket = { count: number; resetAt: number };

const buckets = new Map<string, Bucket>();

/** Stop the Map growing without bound when every key is a one-off. */
const MAX_KEYS = 10_000;

export type RateLimitResult = {
  allowed: boolean;
  remaining: number;
  retryAfterSeconds: number;
};

export function rateLimit(key: string, limit: number, windowSeconds: number): RateLimitResult {
  const now = Date.now();
  const existing = buckets.get(key);

  if (!existing || now >= existing.resetAt) {
    if (buckets.size >= MAX_KEYS) sweep(now);
    buckets.set(key, { count: 1, resetAt: now + windowSeconds * 1000 });
    return { allowed: true, remaining: limit - 1, retryAfterSeconds: 0 };
  }

  existing.count += 1;
  const retryAfterSeconds = Math.max(1, Math.ceil((existing.resetAt - now) / 1000));

  if (existing.count > limit) {
    return { allowed: false, remaining: 0, retryAfterSeconds };
  }
  return { allowed: true, remaining: limit - existing.count, retryAfterSeconds };
}

function sweep(now: number): void {
  for (const [key, bucket] of buckets) {
    if (now >= bucket.resetAt) buckets.delete(key);
  }
  // Still full of live entries? Drop the oldest insertions. Map preserves
  // insertion order, so this is the closest thing to LRU without tracking hits.
  if (buckets.size >= MAX_KEYS) {
    let toDrop = Math.ceil(MAX_KEYS / 10);
    for (const key of buckets.keys()) {
      buckets.delete(key);
      if (--toDrop <= 0) break;
    }
  }
}

/**
 * Best-effort client IP.
 *
 * 🔴 `x-forwarded-for` is attacker-controlled unless a proxy you trust
 *    overwrites it. On localhost there is no such proxy, so this value is a
 *    convenience, not a security control — which is exactly why the per-email
 *    bucket exists alongside it. An attacker who forges this header still runs
 *    into the limit on the account they are targeting.
 */
export function clientIp(request: Request): string {
  const forwarded = request.headers.get("x-forwarded-for");
  if (forwarded) return forwarded.split(",")[0].trim();
  return request.headers.get("x-real-ip") ?? "local";
}

/** Login: strict. Five wrong guesses in fifteen minutes is already generous. */
export const LOGIN_LIMIT = { limit: 5, windowSeconds: 15 * 60 };

/** Signup: looser — a real person mistyping should not be locked out. */
export const SIGNUP_LIMIT = { limit: 10, windowSeconds: 60 * 60 };

export function tooManyRequests(retryAfterSeconds: number): Response {
  const minutes = Math.ceil(retryAfterSeconds / 60);
  return new Response(
    JSON.stringify({
      error: `Too many attempts. Try again in ${minutes} minute${minutes === 1 ? "" : "s"}.`,
    }),
    {
      status: 429,
      headers: {
        "Content-Type": "application/json",
        // The standard header, so a well-behaved client backs off on its own.
        "Retry-After": String(retryAfterSeconds),
      },
    },
  );
}
