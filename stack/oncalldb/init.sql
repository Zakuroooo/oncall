-- ─────────────────────────────────────────────────────────────────────────────
-- oncall-db — control plane ka database. Schema TDD.md §6a ka implementation.
--
-- 🔴 Ye shopdb se ALAG database hai, alag container, alag port (5433 vs 5432).
--    Wajah D9: F2/F3/F4 faults shopdb ko todte hain. Agar OnCall apna evidence
--    usi shopdb mein likhta, toh jis pal fault inject hota, us pal OnCall ka
--    apna record likhna band ho jaata — aur incident ka wo hissa jo sabse
--    important hai (fault ke turant baad ka) hamesha gayab hota.
--
--    Ek monitoring system apne monitored system pe depend nahi kar sakta.
--
-- 🔴 `pgvector` extension chahiye. Isliye image `pgvector/pgvector:pg16` hai,
--    plain `postgres:16` nahi — usme extension available hi nahi hoti.
--
-- 🔴 Ye file bhi `/docker-entrypoint-initdb.d/` wali hai: SIRF khaali data
--    directory pe chalti hai. Schema badla? `make nuke` phir `make up`.
-- ─────────────────────────────────────────────────────────────────────────────

-- pgvector ko is database mein "on" karna padta hai. Extension install hona
-- (image mein) aur enabled hona (yahan) do alag cheezein hain.
CREATE EXTENSION IF NOT EXISTS vector;

-- ═════════════════════════════════════════════════════════════════════════════
-- 1. incidents — ek fault se ek terminal state tak ka safar
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS incidents (
    id           bigserial PRIMARY KEY,
    service      text        NOT NULL,

    -- 🔴 `fault_class` (F1..F5) yahan hai, lekin `correct_action` NAHI hai.
    --    Ye line poore project ki scientific validity hai. Agar `correct_action`
    --    is table mein hota, toh agent (jo isi DB se padhta hai) theoretically
    --    use padh sakta tha — aur phir "kya LLM ne sahi action chuna" ka jawab
    --    bekaar ho jaata. `correct_action` SIRF `chaos/faults.py` mein rehta hai,
    --    aur `make lint` ka check #3/#4 iski nigrani karta hai.
    --
    --    `fault_class` khud safe kyun hai: evaluation ko per-fault-class breakdown
    --    chahiye (METRICS.md — aggregate rate banned hai). Aur "kaunsa fault tha"
    --    jaanne se "kaunsa action sahi tha" pata nahi chalta — F2 aur F4 dono
    --    ka fix restart *lagta* hai, lekin F4 ka asli fix nahi hai. Wahi is
    --    project ka result hai.
    fault_class  text        NOT NULL,

    state        text        NOT NULL,          -- state machine ka node (TDD §1b)
    attempt      int         NOT NULL DEFAULT 0 CHECK (attempt BETWEEN 0 AND 3),

    detected_at  timestamptz NOT NULL DEFAULT now(),
    closed_at    timestamptz,

    -- 🔥 Ablation. Ek boolean jo N3 metric ko ek GROUP BY bana deta hai (TDD §6b).
    rag_enabled  bool        NOT NULL,

    seed         int         NOT NULL,          -- same seed = same fault sequence

    -- 🔴 State machine ke 10 nodes. Ye CHECK constraint ek typo ko database pe
    --    rok deta hai. Iske bina `state='RETRIEVEING'` chup-chaap likha jaata
    --    aur worker usko kabhi pick nahi karta — ek incident jo hamesha ke liye
    --    latak jaata, bina kisi error ke. Sabse bura kism ka bug.
    CONSTRAINT incidents_state_valid CHECK (state IN (
        'DETECTED', 'COLLECTING', 'DIAGNOSING', 'RETRIEVING', 'PROPOSING',
        'AWAITING_APPROVAL', 'EXECUTING', 'VERIFYING', 'RECOVERED', 'ESCALATED'
    )),

    -- Terminal state ka matlab hai closed_at bhara hona chahiye. Aur ulta bhi.
    -- Ye ek data-integrity invariant hai jo "incident RECOVERED hai lekin
    -- closed_at NULL hai" jaisi aadhi-likhi rows ko rokta hai.
    CONSTRAINT incidents_closed_consistency CHECK (
        (state IN ('RECOVERED', 'ESCALATED') AND closed_at IS NOT NULL)
        OR (state NOT IN ('RECOVERED', 'ESCALATED') AND closed_at IS NULL)
    )
);

-- Ablation query (`GROUP BY rag_enabled, fault_class`) ka supporting index.
CREATE INDEX IF NOT EXISTS incidents_ablation_idx ON incidents (rag_enabled, fault_class, state);

-- "Abhi kaun khula hai" — UI ka main query. Partial index kyunki khule
-- incidents hamesha kam honge, aur partial index chhota + tez hota hai.
CREATE INDEX IF NOT EXISTS incidents_open_idx ON incidents (detected_at DESC)
    WHERE state NOT IN ('RECOVERED', 'ESCALATED');


-- ═════════════════════════════════════════════════════════════════════════════
-- 2. events — timeline. UI isi se render hoti hai, aur ye audit trail hai.
-- ═════════════════════════════════════════════════════════════════════════════
--
-- Kyun ek `jsonb payload` aur har event kind ke liye alag table nahi:
--   9 kinds hain aur unka shape bilkul alag hai (`health_check` mein probe
--   results hain, `diagnose` mein LLM ka text). 9 tables ka matlab 9 JOINs
--   hote timeline banane ke liye. Ek `jsonb` column ka matlab ek `ORDER BY at`.
--
-- Iski keemat imaandari se: `payload` ka koi schema enforce nahi hota. Ek typo
-- (`"proble"` instead of `"probes"`) database accept kar lega. Wo risk yahan
-- accept kiya gaya hai kyunki events sirf padhne (audit/UI) ke liye hain —
-- koi business logic `payload` ke andar ki key pe branch nahi karti.
-- 🔴 Jo cheez branch karti hai (state), wo apna column hai apne CHECK ke saath.
CREATE TABLE IF NOT EXISTS events (
    id           bigserial   PRIMARY KEY,
    incident_id  bigint      NOT NULL REFERENCES incidents (id) ON DELETE CASCADE,
    at           timestamptz NOT NULL DEFAULT now(),
    kind         text        NOT NULL,
    payload      jsonb       NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT events_kind_valid CHECK (kind IN (
        'health_check', 'logs', 'diagnose', 'retrieve', 'propose',
        'approval', 'execute', 'verify', 'escalate', 'state_change'
    ))
);

-- Timeline query: ek incident ke saare events, time order mein.
CREATE INDEX IF NOT EXISTS events_timeline_idx ON events (incident_id, at);


-- ═════════════════════════════════════════════════════════════════════════════
-- 3. llm_calls — har LLM call ka record. Langfuse ki jagah (D12).
-- ═════════════════════════════════════════════════════════════════════════════
--
-- Ye table teen sawaalon ka jawab deta hai jo interview mein aa sakte hain:
--   1. "Token kharch kitna hua?"        → SUM(prompt_tokens + completion_tokens)
--   2. "LLM ne actually kya bola?"      → raw_response
--   3. "Kaunsa provider chal raha tha?" → provider (R8 confound check)
CREATE TABLE IF NOT EXISTS llm_calls (
    id                bigserial   PRIMARY KEY,
    incident_id       bigint      NOT NULL REFERENCES incidents (id) ON DELETE CASCADE,

    -- 🔒 SIRF do call sites hain, aur ye CHECK usko enforce karta hai.
    --    Agar kal koi teesra LLM call add kare, ye constraint use rokega — aur
    --    wo rukna hi chahiye, kyunki "exactly 2 LLM call sites" is project ka
    --    ek claim hai (TDD §5). Ek claim jise database enforce karta hai, wo
    --    claim README mein likhi baat se zyada bharosemand hai.
    call_site         text        NOT NULL CHECK (call_site IN ('diagnose', 'propose')),

    attempt           int         NOT NULL DEFAULT 0,
    provider          text        NOT NULL,
    model             text        NOT NULL,

    prompt_tokens     int,
    completion_tokens int,
    latency_ms        int,

    ok                bool        NOT NULL,
    raw_response      text,       -- "LLM ne kya bola?" ka jawab. Debugging ka dil.
    error             text,       -- ok=false pe kya hua

    at                timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS llm_calls_incident_idx ON llm_calls (incident_id, at);


-- ═════════════════════════════════════════════════════════════════════════════
-- 4. approvals — 🔒 human gate ka record.
-- ═════════════════════════════════════════════════════════════════════════════
--
-- 🔴 Approval ek DATABASE ROW hai, ek blocked thread nahi. Ye design ka ek
--    important point hai (TDD §1c): agar approval ek `input()` ya ek blocked
--    HTTP request hoti, toh worker restart hone pe wo incident hamesha ke liye
--    kho jaata. Row-based approval ka matlab: worker mar bhi jaaye, incident
--    `AWAITING_APPROVAL` mein baitha rehta hai aur naya worker use utha leta hai.
--
-- 🔴 TDD §6a mein is table ka PK likha nahi tha. Yahan `id bigserial PRIMARY KEY`
--    add kiya gaya hai. Kyun ye zaruri hai: PK ke bina do identical approval
--    rows ko alag nahi kar sakte, aur ek galti se do baar insert hui approval
--    ko delete karna impossible ho jaata hai. Ek audit table ka har row
--    addressable hona chahiye.
CREATE TABLE IF NOT EXISTS approvals (
    id           bigserial   PRIMARY KEY,
    incident_id  bigint      NOT NULL REFERENCES incidents (id) ON DELETE CASCADE,
    attempt      int         NOT NULL,

    action       text        NOT NULL,     -- allowlist ka ek naam (TDD §3c)
    params       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    rationale    text,                     -- LLM #2 ne kyun ye chuna

    decision     text        CHECK (decision IN ('approved', 'rejected')),
    decided_at   timestamptz,
    decided_by   text,                     -- ⚠️ abhi hamesha 'operator' — auth nahi hai (THREAT_MODEL §5)

    created_at   timestamptz NOT NULL DEFAULT now(),

    -- 🔒 Ek incident ke ek attempt pe SIRF EK approval. Ye UNIQUE constraint
    --    double-approval race ko database level pe rokta hai: agar operator
    --    UI pe do baar "Approve" click kare (ya do tabs khuli hon), doosra
    --    insert fail hoga. Application-level check (`SELECT` phir `INSERT`)
    --    is race ko nahi rokta — do concurrent requests dono ko khaali
    --    dikhta hai. Constraint rokta hai.
    CONSTRAINT approvals_one_per_attempt UNIQUE (incident_id, attempt),

    -- Decision aur decided_at saath aate hain, ya dono NULL (pending).
    CONSTRAINT approvals_decision_consistency CHECK (
        (decision IS NULL AND decided_at IS NULL)
        OR (decision IS NOT NULL AND decided_at IS NOT NULL)
    )
);

-- "Kaunse approvals pending hain" — UI ka polling query.
CREATE INDEX IF NOT EXISTS approvals_pending_idx ON approvals (created_at)
    WHERE decision IS NULL;


-- ═════════════════════════════════════════════════════════════════════════════
-- 5. verdicts — 🔥 oracle ka output. TDD §6a mein ye table NAHI thi.
-- ═════════════════════════════════════════════════════════════════════════════
--
-- 🔴 Ye ek jaan-boojh kar ki gayi addition hai, aur iska tark TDD §6b ka apna
--    tark hai: "measurement ko schema mein pehle se build karo, baad mein
--    bolt-on na karo."
--
--    §6b ne `rag_enabled` ko column banaya taaki N3 ek GROUP BY ban jaaye.
--    Lekin N1 — is poore project ka HEADLINE number (naive oracle vs sustained
--    oracle ka disagreement) — original schema mein `events.payload` ke andar
--    jsonb mein dafn tha. Uska matlab hota ki headline metric ek
--    `payload->>'sustained'` cast ke through nikalta, jahan ek typo silently
--    NULL deta hai aur NULL comparison mein chup-chaap gayab ho jaata hai.
--
--    Do booleans, do columns. Ab N1 ye hai:
--      SELECT fault_class,
--             AVG(naive_recovered::int)     AS naive_says_fixed,
--             AVG(sustained_recovered::int) AS actually_fixed
--      FROM verdicts JOIN incidents USING (...) GROUP BY fault_class;
--
--    Yahi query pe poora project khada hai. Wo ek JSON extraction nahi honi
--    chahiye.
CREATE TABLE IF NOT EXISTS verdicts (
    id                   bigserial   PRIMARY KEY,
    incident_id          bigint      NOT NULL REFERENCES incidents (id) ON DELETE CASCADE,
    attempt              int         NOT NULL,

    -- 🔥 Asli verdict. 3 probes / 30 second / ~20 rps ke baad.
    sustained_recovered  bool        NOT NULL,

    -- 🔥 Wo verdict jo ek naive implementation deta — ek akela probe, bina load.
    --    Ye "control group" hai. Isko record karna hi N1 ko possible banata hai.
    naive_recovered      bool        NOT NULL,

    -- Har probe ka raw record: [{"n":1,"status":200,"ms":42}, ...]
    -- Ye jsonb hai kyunki ye evidence hai, query target nahi.
    probes               jsonb       NOT NULL DEFAULT '[]'::jsonb,

    load_rps             int,        -- kitna load daala gaya (config.ORACLE_LOAD_RPS)
    window_s             int,        -- kitni der (probes × gap)

    at                   timestamptz NOT NULL DEFAULT now(),

    -- Ek attempt ka ek hi verdict. Dobara verify karna = naya attempt.
    CONSTRAINT verdicts_one_per_attempt UNIQUE (incident_id, attempt)
);


-- ═════════════════════════════════════════════════════════════════════════════
-- 6. runbooks — retrieval corpus. Embeddings ek baar, yahan cache (CLAUDE.md §3).
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS runbooks (
    id          text        PRIMARY KEY,      -- 'RB-014' — human-readable, citable
    title       text        NOT NULL,
    body        text        NOT NULL,

    -- ✅ CLAUDE.md §3: har ingested record apna source, licence aur fetch date
    --    rakhta hai. Ye teen columns NOT NULL hain — matlab ek record bina
    --    provenance ke insert nahi ho sakta. Ek data-licensing claim jo
    --    database enforce karta hai.
    source      text        NOT NULL,
    source_url  text,
    licence     text        NOT NULL,
    fetched_at  date        NOT NULL,

    -- ⚠️ N4 (retrieval precision) ki ground truth. Corpus mein ~40-60 docs hain
    --    lekin sirf ~8 relevant — baaki distractors hain, taaki retrieval ka
    --    test asli ho.
    --
    -- 🔴 KHATRA: ye column usi table mein hai jise agent padhta hai.
    --    `oncall/` mein `is_relevant` likhna `make lint` check #4 se band hai,
    --    lekin `SELECT *` isko chupke se le aayega. Isliye retrieval code mein
    --    HAMESHA explicit column list — `SELECT id, title, body, ...` — kabhi
    --    `SELECT *` nahi. Ye rule test se bhi bandha hai (Phase 6).
    is_relevant bool        NOT NULL DEFAULT false,

    -- all-MiniLM-L6-v2 → 384 dimensions. 🔴 Ye number model se bandha hai;
    -- model badla toh table migrate karni padegi (vector ka dimension fixed hai).
    embedding   vector(384),

    -- Keyword search. Ye GENERATED column hai — Postgres khud maintain karta
    -- hai, insert/update pe. Kyun generated aur trigger nahi: ek trigger
    -- bhoolna possible hai (ek code path jo `tsv` set karna bhool jaaye =
    -- chupchaap sparse search se gayab record). Generated column ko bhoolna
    -- possible nahi hai — database uska zimmedaar hai.
    --
    -- `'english'` config: stemming + stopwords. "restarting" → "restart".
    tsv         tsvector    GENERATED ALWAYS AS (
                    to_tsvector('english', coalesce(title, '') || ' ' || coalesce(body, ''))
                ) STORED
);

-- ── Sparse (keyword) search ka index ─────────────────────────────────────────
-- GIN = inverted index. tsvector ke liye ye standard hai.
CREATE INDEX IF NOT EXISTS runbooks_tsv_idx ON runbooks USING gin (tsv);

-- ── Dense (semantic) search ka index ─────────────────────────────────────────
--
-- 🔴 Is index ko JAAN-BOOJH KAR nahi banaya gaya. Ye ek comment hai, code nahi.
--
--    pgvector do index types deta hai: IVFFlat aur HNSW. Dono APPROXIMATE hain
--    — wo tez hone ke liye kuch sahi jawab miss karte hain (recall < 100%).
--
--    40-60 rows pe Postgres ka sequential scan poore table ka exact cosine
--    distance nikaal leta hai, microseconds mein. Ek approximate index yahan
--    do nuksaan karta:
--      1. Recall 100% se kam ho jaata — aur N4 (retrieval precision) measure
--         karna is project ka ek metric hai. Index ki galti ko retrieval ki
--         galti samajh liya jaata. Measurement contaminate ho jaata.
--      2. IVFFlat ko `lists` parameter chahiye jo row count pe depend karta
--         hai; 50 rows pe wo tuning bekaar hai.
--
--    Matlab: is corpus size pe exact search TEZ AUR SAHI dono hai.
--    ⚠️ Ye tab galat ho jaayega jab corpus ~10,000 rows cross kare. Tab
--    HNSW add karna hoga. Wo aaj ka problem nahi hai, aur "abhi index nahi
--    banaya kyunki 50 rows hain" ek behtar interview jawab hai "index banaya
--    kyunki tutorial mein tha" se.
--
-- CREATE INDEX runbooks_embedding_idx ON runbooks USING hnsw (embedding vector_cosine_ops);


-- ═════════════════════════════════════════════════════════════════════════════
-- 6b. retrievals — 🔥 N4 ka raw data (2026-08-24 pe juda).
-- ═════════════════════════════════════════════════════════════════════════════
--
-- 🔴 Ye table TDD §6a ke original 8-table sketch mein NAHI thi, lekin
--    METRICS.md §3 N4 ki query `FROM retrievals JOIN runbooks` likhti hai —
--    yaani metric definition ek aisi table poochh rahi thi jo exist hi nahi
--    karti thi. Do options thay:
--      (a) N4 ko events.payload ke jsonb se nikalo — ek typo chup-chaap NULL
--          deta, aur headline retrieval metric ek JSON-cast ke peeche chhup
--          jaata (wahi galti jiske liye verdicts table bana thi).
--      (b) Table banao, columns ko first-class rakho.
--    (b) chuna gaya — same tark jo verdicts ke peeche hai: measurement ko
--    schema mein pehle se build karo, baad mein bolt-on na karo.
--
-- Har retrieved chunk ka ek row: kaunsa runbook, kis rank pe, RRF score kitna,
-- aur dono methods mein akela rank kya tha (RRF fusion gain ka data — D11
-- falsifier: hit@1(hybrid) − hit@1(vector-only) isi se compute hota hai).
CREATE TABLE IF NOT EXISTS retrievals (
    id           bigserial   PRIMARY KEY,
    incident_id  bigint      NOT NULL REFERENCES incidents (id) ON DELETE CASCADE,
    attempt      int         NOT NULL DEFAULT 0,

    runbook_id   text        NOT NULL,
    rank         int         NOT NULL CHECK (rank BETWEEN 1 AND 10),
    rrf_score    real        NOT NULL,
    vector_rank  int,                     -- sirf dense list mein tha toh yahan rank, warna NULL
    keyword_rank int,                     -- sirf sparse list mein tha toh yahan rank

    at           timestamptz NOT NULL DEFAULT now(),

    -- Ek incident-attempt pe ek rank ek hi baar. Dobara fusion chalana bug hai.
    CONSTRAINT retrievals_unique_rank UNIQUE (incident_id, attempt, rank)
);

CREATE INDEX IF NOT EXISTS retrievals_incident_idx ON retrievals (incident_id, attempt, rank);


-- ═════════════════════════════════════════════════════════════════════════════
-- 7. jobs — Celery ke bina job queue (D10). `FOR UPDATE SKIP LOCKED`.
-- ═════════════════════════════════════════════════════════════════════════════
--
-- Ek line mein concept: worker `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1`
-- karta hai. `FOR UPDATE` row ko lock karta hai; `SKIP LOCKED` doosre worker
-- ko us locked row pe INTEZAAR karne ki jagah AGE badhne deta hai. Isse do
-- workers kabhi same job nahi uthate, aur koi blocking bhi nahi hoti.
--
-- Kyun Celery nahi: Celery ko Redis/RabbitMQ chahiye (ek aur container, ~50 MB
-- RAM), aur uska retry/state model humare state machine se overlap karta hai —
-- do jagah "attempt" ka concept. Postgres already yahan hai. Ek kam moving part.
CREATE TABLE IF NOT EXISTS jobs (
    id           bigserial   PRIMARY KEY,
    incident_id  bigint      NOT NULL REFERENCES incidents (id) ON DELETE CASCADE,

    status       text        NOT NULL DEFAULT 'queued'
                             CHECK (status IN ('queued', 'running', 'done', 'failed')),

    locked_at    timestamptz,
    locked_by    text,        -- worker ka naam — "kaun atka hua hai" debug karne ke liye
    attempts     int         NOT NULL DEFAULT 0,
    last_error   text,

    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- 🔴 Ye index queue ka dil hai. Worker ka query hai:
--      SELECT id FROM jobs WHERE status = 'queued'
--      ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1;
--    Partial index (`WHERE status = 'queued'`) ka matlab: index mein sirf wo
--    rows hain jo pending hain. 10,000 completed jobs ke baad bhi ye index
--    chhota rehta hai — kyunki `done` rows usme nahi hain.
CREATE INDEX IF NOT EXISTS jobs_queue_idx ON jobs (id) WHERE status = 'queued';

-- Ek incident ke liye ek hi active job. Duplicate enqueue se bachne ke liye.
CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_active_per_incident_idx
    ON jobs (incident_id) WHERE status IN ('queued', 'running');


-- ═════════════════════════════════════════════════════════════════════════════
-- Smoke check — ye init.sql chali ya nahi, ye batata hai.
-- ═════════════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    n int;
BEGIN
    SELECT count(*) INTO n
    FROM information_schema.tables
    WHERE table_schema = 'public'
      AND table_name IN ('incidents', 'events', 'llm_calls', 'approvals',
                         'verdicts', 'retrievals', 'runbooks', 'jobs');
    IF n <> 8 THEN
        RAISE EXCEPTION 'oncall-db schema adhoora: 8 tables chahiye, % mili', n;
    END IF;
    RAISE NOTICE 'oncall-db schema ✅ 8 tables ready';
END $$;


