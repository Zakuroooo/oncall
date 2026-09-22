/**
 * Postgres access for the web tier.
 *
 * 🔴 READ-ONLY against the control plane's tables. This module reads
 *    `incidents`, `events`, `verdicts` and friends; it never writes to them,
 *    and it has no path to Docker. The only tables the web tier writes are its
 *    own (`web_users`, `web_sessions`).
 *
 * Everything here is written so that a database that is DOWN is a normal,
 * handled condition rather than a crash — the control plane is expected to be
 * off most of the time (it only runs when an operator is actually running the
 * agent locally), and a marketing page must not 500 because of that.
 */
import { Pool } from "pg";

/**
 * One pool per process, cached on globalThis.
 *
 * Next.js in development hot-reloads modules on every edit. Without this cache
 * each reload would build a new pool and leak the old one's sockets, and after
 * a dozen saves Postgres starts refusing connections — which then looks
 * exactly like the fault this project simulates. Caching on globalThis is the
 * standard escape hatch because globalThis survives module reloading.
 */
declare global {
  var __oncallPool: Pool | undefined;
}

export const DATABASE_URL =
  process.env.ONCALL_DB_URL ?? "postgresql://oncall:oncall@127.0.0.1:5433/oncall";

export function getPool(): Pool {
  if (!global.__oncallPool) {
    global.__oncallPool = new Pool({
      connectionString: DATABASE_URL,
      max: 4,
      // 🔴 Short, deliberately. A page render must not hang for 30 seconds
      //    because the control plane is off. Failing in 2 s lets the data
      //    layer fall back to demo data while the visitor is still watching
      //    the page paint.
      connectionTimeoutMillis: 2000,
      idle_in_transaction_session_timeout: 5000,
    });
    // A pool emits 'error' for idle clients dropped by the server. With no
    // listener, Node treats it as an unhandled error event and kills the
    // process — one restarted container would take the website down.
    global.__oncallPool.on("error", (err) => {
      console.warn("[db] idle client error:", err.message);
    });
  }
  return global.__oncallPool;
}

/** Result of a query that is allowed to fail. */
export type DbResult<T> =
  | { ok: true; rows: T[] }
  | { ok: false; error: string };

/**
 * Run a parameterised query, converting "the database is unreachable" into a
 * value rather than an exception.
 *
 * 🔴 `params` is always a separate argument. String interpolation into SQL is
 *    forbidden here for the same reason it is forbidden in `oncall/db/queries.py`,
 *    and the rule is easier to keep when the helper makes the safe path the
 *    only convenient one.
 */
export async function tryQuery<T>(
  sql: string,
  params: unknown[] = [],
): Promise<DbResult<T>> {
  try {
    const result = await getPool().query(sql, params);
    return { ok: true, rows: result.rows as T[] };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { ok: false, error: message };
  }
}

/**
 * Run a query where failure IS exceptional — the web tier's own tables.
 *
 * Signing up must not silently succeed against a database that is not there.
 * The distinction between this and `tryQuery` is the whole design: control
 * plane data degrades to demo, account data never degrades.
 */
export async function query<T>(sql: string, params: unknown[] = []): Promise<T[]> {
  const result = await getPool().query(sql, params);
  return result.rows as T[];
}

/** Is the control plane database reachable right now? */
export async function isDbUp(): Promise<boolean> {
  const result = await tryQuery("SELECT 1");
  return result.ok;
}
