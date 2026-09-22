-- ─────────────────────────────────────────────────────────────────────────────
-- shopdb — monitored stack ka database.
--
-- 🔴 Ye file `/docker-entrypoint-initdb.d/` mein mount hoti hai. Postgres ka
--    official image usme padi *.sql files ko SIRF TAB chalata hai jab data
--    directory KHAALI ho — matlab pehli baar container banne pe.
--
--    Iska ek asli natija hai jo debugging mein confuse karta hai: is file ko
--    badal kar `docker compose restart shopdb` karne se KUCH NAHI hota.
--    `docker compose down -v` (volume delete) chahiye. Isliye `make nuke`
--    exist karta hai.
--
-- 🔴 Ye database DISPOSABLE hai (DATA.md A6). Isme jo data hai wo Faker se
--    banaya gaya synthetic data hai — koi asli customer nahi, koi PII nahi,
--    koi licence issue nahi. Chaos experiments isi ko todte hain, toh isme
--    kuch aisa nahi hona chahiye jiska nuksaan ho.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── orders: wo table jise /health chhoota hai ────────────────────────────────
--
-- Kyun ek asli table aur `SELECT 1` nahi: `SELECT 1` Postgres ke planner se
-- hi jawab paa jaata hai, storage layer ko chhoota bhi nahi. F3 fault (table
-- pe ACCESS EXCLUSIVE lock) mein `SELECT 1` pass hoga aur `SELECT count(*)
-- FROM orders` block hoga. Health check ko wahi block hona chahiye, warna wo
-- fault dikhega hi nahi.
CREATE TABLE IF NOT EXISTS orders (
    id          bigserial PRIMARY KEY,
    customer    text        NOT NULL,
    amount      numeric(10, 2) NOT NULL CHECK (amount >= 0),
    status      text        NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'paid', 'shipped', 'cancelled')),
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Index status pe, kyunki load generator `/orders` maarta hai aur wo
-- `ORDER BY id DESC` karta hai (jo PK index se aa jaata hai). Ye index
-- future queries ke liye hai aur — imaandari se — is project ke liye strictly
-- zaruri nahi. ⚠️ Isko rakha hai kyunki ek khaali schema unrealistic lagta
-- hai; performance ka koi measurement iske peeche nahi hai.
CREATE INDEX IF NOT EXISTS orders_status_idx ON orders (status);

-- ── Ek minimum seed, taaki container start hote hi /health pass ho ───────────
--
-- 🔴 Ye 3 rows Faker se NAHI aati. Kyun: `scripts/seed_shopdb.py` (Faker wala)
--    host se chalta hai aur wo tab tak nahi chal sakta jab tak container up na
--    ho. Lekin `/health` `SELECT count(*) FROM orders` karta hai — jo khaali
--    table pe bhi 0 return karke pass ho jaata hai. Toh technically ye rows
--    zaruri nahi hain.
--
--    Phir bhi hain, ek practical reason se: `docker compose up` ke turant baad
--    `curl /health` aur `curl /orders` dono meaningful jawab dene chahiye.
--    Ek khaali `/orders` "kya ye kaam kar raha hai?" ka jawab nahi deta.
--    Asli volume (⚠️ ~5,000 rows target, Phase 1 mein measure hoga) Faker se
--    aata hai.
INSERT INTO orders (customer, amount, status) VALUES
    ('smoke-test-1', 100.00, 'paid'),
    ('smoke-test-2',  49.50, 'pending'),
    ('smoke-test-3', 999.99, 'shipped')
ON CONFLICT DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────────
-- 🔴 Jo cheez yahan JAAN-BOOJH KAR NAHI HAI: `ALTER SYSTEM SET max_connections`.
--
-- F4 fault shopdb ka `max_connections` 5 pe le jaata hai. Wo chaos script ka
-- kaam hai (`chaos/faults.py` ka `_F4_BROKEN_MAX_CONN`), init.sql ka nahi. Kyun
-- ye farq maayne rakhta hai: agar default state hi toota hua ho, toh "pehle
-- healthy tha, phir toota" wala baseline hi nahi banega — aur oracle ke paas
-- compare karne ke liye kuch nahi hoga. Init ka kaam ek HEALTHY starting point
-- banana hai.
-- ─────────────────────────────────────────────────────────────────────────────
