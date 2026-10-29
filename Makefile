
# ─────────────────────────────────────────────────────────────────────────────
# OnCall — Makefile
#
# Kyun Makefile aur koi shell script nahi:
#   `make doctor` ek naam hai jo yaad rehta hai. `./scripts/check_env_v2.sh`
#   nahi rehta. Aur ye self-documenting hai — `make help` saari targets dikhata
#   hai. macOS pe `make` pehle se hai, install kuch nahi karna.
#
# 🔴 macOS ka default make GNU make 3.81 hai — `.RECIPEPREFIX` support nahi
#    karta. Isliye recipes TAB se indent hain, spaces se nahi. Agar koi editor
#    inko spaces mein convert kar de, make "missing separator" error dega.
# ─────────────────────────────────────────────────────────────────────────────

PY := /opt/homebrew/bin/python3.11
VENV := .venv
VENV_PY := $(VENV)/bin/python
VENV_PIP := $(VENV)/bin/pip
COMPOSE := docker compose

.DEFAULT_GOAL := help
.PHONY: help doctor venv install install-ingest up down logs ps stats health serve stop migrate migrate-write seed seed-write faults inject inject-write heal heal-all test lint docs-lint verify fmt clean nuke

help: ## Ye list dikhao
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ─────────────────────────────────────────────────────────────────────────────
# doctor — R16 ka tripwire.
#
# 2026-08-22 pe teen environment assumptions galat nikli (Docker Desktop gayab,
# 14 GB disk not 216, python3 = 3.14). Wo teeno ek command se pakdi jaati thi.
# Ab wo command exist karti hai. Isko har naye machine pe PEHLE chalao.
#
# 🔴 Har check jaan-boojh kar failure-tolerant hai. Pehla version `sysctl` ke
#    output ko seedha `$$(( ))` mein daalta tha — jis machine pe sysctl block
#    tha, wahan poora target "Error 127" pe mar gaya aur baaki checks chale hi
#    nahi. Ek doctor jo aadhe raste mein crash kare, no doctor se bura hai
#    (CLAUDE.md §2: "a verification command that fails is worse than none").
#    Isliye har value pehle variable mein aati hai, phir empty-test hota hai.
# ─────────────────────────────────────────────────────────────────────────────
doctor: ## Prerequisites check karo — ye PEHLE chalao
	@echo "── OnCall environment check ──────────────────────────────"
	@printf "%-22s" "python3.11"; \
		if [ -x "$(PY)" ]; then echo "✅ $$($(PY) --version)"; \
		else echo "❌ nahi mila — brew install python@3.11 (D23)"; fi
	@printf "%-22s" "venv"; \
		if [ -x "$(VENV_PY)" ]; then echo "✅ $$($(VENV_PY) --version)"; \
		else echo "⚠️  nahi bana — make venv"; fi
	@printf "%-22s" "docker CLI"; \
		if docker --version >/dev/null 2>&1; then echo "✅ $$(docker --version)"; \
		else echo "❌ toota hua / nahi hai — brew install colima docker docker-compose (D22)"; fi
	@printf "%-22s" "docker daemon"; \
		if docker info >/dev/null 2>&1; then echo "✅ reachable"; \
		else echo "❌ daemon down — colima start --cpu 2 --memory 4 --disk 20"; fi
	@printf "%-22s" "docker compose"; \
		if docker compose version >/dev/null 2>&1; then echo "✅ $$(docker compose version --short 2>/dev/null)"; \
		else echo "❌ nahi mila — D22 ka falsifier #3"; fi
	@printf "%-22s" "free disk"; \
		avail=$$(df -g / 2>/dev/null | awk 'NR==2 {print $$4}'); \
		if [ -z "$$avail" ]; then echo "⚠️  df padha nahi gaya"; \
		elif [ "$$avail" -ge 6 ]; then echo "✅ $${avail} GB"; \
		else echo "❌ sirf $${avail} GB — R3, 6 GB chahiye"; fi
	@printf "%-22s" "RAM"; \
		bytes=$$(sysctl -n hw.memsize 2>/dev/null); \
		if [ -n "$$bytes" ]; then echo "$$(( bytes / 1073741824 )) GB total (R3: 8 GB pe tight)"; \
		else echo "⚠️  sysctl padha nahi gaya"; fi
	@printf "%-22s" ".env"; \
		if [ -f .env ]; then echo "✅ hai"; else echo "⚠️  nahi — cp .env.example .env"; fi
	@printf "%-22s" "GROQ_API_KEY"; \
		if [ -f .env ] && grep -q '^GROQ_API_KEY=gsk_' .env 2>/dev/null; then echo "✅ set (validity ⚠️ unverified — Phase 5)"; \
		else echo "⚠️  set nahi — Phase 5 tak zarurat nahi"; fi
	@echo "──────────────────────────────────────────────────────────"
	@echo "🔴 Folder ka naam: $$(basename $$(pwd)) — 'oncall' hona chahiye (CLAUDE.md §7)"

# ─────────────────────────────────────────────────────────────────────────────
# venv / install
#
# 🔴 `venv` .PHONY hai (line 22), yaani make usko "kaam ho gaya" yaad nahi
#    rakhta — jab bhi maanga jaaye, chalta hai. Aur `install` ka prerequisite
#    `venv` hai. Isliye `make venv && make install` recipe ko DO baar chalata
#    tha: ek baar seedha, ek baar install ke prerequisite ke roop mein.
#    2026-08-22 pe Pranay ke output mein ye dikha (pip ka upgrade do baar).
#
#    Ilaaj: recipe ke andar guard. `.venv/bin/python` maujood hai toh dobara
#    banane ka koi matlab nahi. .PHONY hata kar file-target banana "sahi" make
#    idiom hai, lekin us case mein `$(VENV_PY)` ko target likhna padta hai aur
#    padhne wale ko order-only prerequisites samajhne padte hain — 3 line ka
#    guard 1 line ke idiom se behtar hai jise samjhaana pade (CLAUDE.md §1).
# ─────────────────────────────────────────────────────────────────────────────
venv: ## Python 3.11 ka venv banao (dobara chalane pe kuch nahi karta)
	@if [ -x "$(VENV_PY)" ]; then echo "venv already hai — skip"; \
		else $(PY) -m venv $(VENV) && $(VENV_PIP) install --upgrade pip; fi

install: venv ## Runtime deps install karo (torch NAHI). Ye venv khud bana leta hai
	$(VENV_PIP) install -r requirements.txt

install-ingest: ## Embedding deps install karo (⚠️ ~2.5 GB — Phase 6)
	$(VENV_PIP) install -r requirements-ingest.txt

# ─────────────────────────────────────────────────────────────────────────────
# Stack
# ─────────────────────────────────────────────────────────────────────────────
up: ## 4 containers start karo
	$(COMPOSE) up -d --build
	@echo "shopapp  → http://127.0.0.1:8001/health"
	@echo "oncall-db → 127.0.0.1:5433"

down: ## Containers band karo (volumes rehne do)
	$(COMPOSE) down

logs: ## Saare containers ke logs follow karo
	$(COMPOSE) logs -f --tail=50

ps: ## Kaun chal raha hai
	$(COMPOSE) ps

stats: ## 🔴 R3 ka asli measurement — RAM per container
	docker stats --no-stream
	@echo "── disk ──"
	docker system df

health: ## shopapp ka /health ek baar maaro (naive probe)
	@curl -sS -w '\nHTTP %{http_code} in %{time_total}s\n' http://127.0.0.1:8001/health || true

# ─────────────────────────────────────────────────────────────────────────────
# serve / stop — control plane (API + worker) ek command mein.
#
# 🔴 Dono background processes hain aur unke logs /tmp/oncall-*.log mein jaate
#    hain — terminal block nahi hota. `make stop` inhe pid-pattern se maarta
#    hai. Worker ko RAG flag apne environment se milta hai:
#        ONCALL_RAG_ENABLED=false make serve     # ablation arm B
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# 🔴 THREE processes, not two — added 2026-08-27 after an end-to-end run.
#
# `serve` used to start only the API and the worker. The embed service was
# documented in `embed_service.py`'s docstring and had NO make target at all,
# so the normal way to start OnCall left it down.
#
# The consequence was not a loud failure. With RAG on, retrieval raised
# EmbedUnavailableError, the loop escalated (correctly — silently continuing
# with 0 runbooks would corrupt the N3 ablation), and every RAG incident died
# the same way. Injecting F4 and watching it is how this surfaced; nothing in
# `make verify` could have caught it, because it is a missing target rather
# than a wrong line.
#
# `embed` is a SEPARATE target as well as part of `serve`: it holds torch
# (~400 MB resident, D35), so anyone working on the non-RAG path should be
# able to leave it off deliberately.
# ─────────────────────────────────────────────────────────────────────────────
embed: ## Start the embed service (torch lives here — D35)
	@$(VENV_PY) -m uvicorn oncall.retrieval.embed_service:app \
		--host 127.0.0.1 --port 8010 > /tmp/oncall-embed.log 2>&1 & \
		echo "embed  → pid $$! (log: /tmp/oncall-embed.log)"

serve: ## Start API + worker + embed in the background (logs: /tmp/oncall-*.log)
	@$(VENV_PY) -m oncall.main > /tmp/oncall-api.log 2>&1 & echo "API    → pid $$! (log: /tmp/oncall-api.log)"
	@$(VENV_PY) -m oncall.queue.worker > /tmp/oncall-worker.log 2>&1 & echo "worker → pid $$! (log: /tmp/oncall-worker.log)"
	@$(MAKE) --no-print-directory embed
	@sleep 2; curl -s http://127.0.0.1:8000/health 2>/dev/null | head -c 200 || (echo "❌ API uthi nahi — /tmp/oncall-api.log dekho")
	@echo ""
	@printf "embed  : "; for i in 1 2 3 4 5 6 7 8 9 10 11 12; do \
		if curl -sf http://127.0.0.1:8010/health >/dev/null 2>&1; then echo "ready"; break; \
		elif [ $$i = 12 ]; then echo "⚠️  not ready yet — the model is probably still loading (/tmp/oncall-embed.log)"; \
		else sleep 5; fi; done

stop: ## Stop API + worker + embed (containers stay up)
	@pkill -f "python -m oncall.main" 2>/dev/null; pkill -f "oncall.main:app" 2>/dev/null; pkill -f "oncall.queue.worker" 2>/dev/null; pkill -f "oncall.retrieval.embed_service" 2>/dev/null; echo "band kar diye (ya pehle se band the)"

# 🔴 Do targets, ek module — `seed`/`seed-write` ka wahi pattern (CLAUDE.md §3).
#
#    `migrate` VERIFY karta hai, apply nahi. Ye jaan-boojh kar hai: `oncall-db`
#    container pehle boot pe `stack/oncalldb/init.sql` khud chalata hai
#    (docker-compose.yml:267 → docker-entrypoint-initdb.d). Yaani normal setup
#    mein schema pehle se aa chuka hota hai, aur is target ka kaam sirf ye
#    poochhna hai: "jo aana chahiye tha, wo aaya?"
#
#    Isliye `migrate` DRY RUN hai. `migrate-write` ki zarurat sirf tab padti hai
#    jab init.sql badla ho aur volume purana ho — aur us case mein bhi
#    `make nuke && make up` zyada saaf raasta hai.
migrate: ## Schema verify karo — kuch likhta NAHI (dry run)
	$(VENV_PY) -m oncall.db.migrate

migrate-write: ## 🔴 init.sql DOBARA chalao (idempotent, D50). Pehle `make migrate` dekho
	$(VENV_PY) -m oncall.db.migrate --write

# 🔴 Do targets, ek script. `seed` DRY RUN hai, `seed-write` asli likhta hai.
#
#    Kyun do targets aur ek `make seed WRITE=1` nahi: `make seed` type karna
#    aasan hai, aur ek aadmi jo jaldi mein hai wo `make seed` chala kar sochega
#    ki data aa gaya. Agar `seed` ka default write hota, toh ek galat `--dsn`
#    (ya galat repo) 5,000 fake orders kahin bhi daal deta. Dry-run ko DEFAULT
#    banana CLAUDE.md §3 ka rule hai, aur target ka naam bhi wahi bolna chahiye.
seed: ## shopdb mein synthetic orders — DRY RUN (kuch nahi likhta)
	$(VENV_PY) scripts/seed_shopdb.py

seed-write: ## 🔴 shopdb mein synthetic orders ASLI mein likho (~5,000 rows)
	$(VENV_PY) scripts/seed_shopdb.py --write

# ─────────────────────────────────────────────────────────────────────────────
# Chaos — fault inject / heal (`chaos/inject.py`)
#
# 🔴 `seed` wala hi pattern: dry-run ka apna naam, write ka apna naam. Wajah
#    yahan `seed` se BHI zyada strong hai — `seed` sirf rows daalta hai, `inject`
#    ek chalta hua container todta hai. `make inject F=F4` chala kar "kuch nahi
#    hua" dekhna safe hai; `make inject-write F=F4` intentional hona chahiye.
#
# 🔴 `F=` variable kyun aur `make inject-F4` jaise 5 targets kyun nahi: fault
#    ids `chaos/faults.py` mein rehti hain. Makefile mein unko dohrana matlab
#    do jagah truth rakhna, aur ek din wo drift kar jaayengi. `--list` hi
#    authoritative list hai.
#
# ⚠️ `F` khaali hone pe `chaos/inject.py` khud exit 2 deta hai apne usage message
#    ke saath — Makefile usko duplicate nahi karta.
# ─────────────────────────────────────────────────────────────────────────────
faults: ## 5 fault classes list karo (ground truth ke bina)
	$(VENV_PY) -m chaos.inject --list

inject: ## Fault ka DRY RUN — kya hota, kuch karta nahi. Use: make inject F=F4
	$(VENV_PY) -m chaos.inject $(F)

inject-write: ## 🔴 Fault ASLI mein inject karo + incident row. Use: make inject-write F=F4
	$(VENV_PY) -m chaos.inject $(F) --write

heal: ## 🔴 Ek fault ko wapas theek karo. Use: make heal F=F4
	$(VENV_PY) -m chaos.inject $(F) --heal --write

heal-all: ## 🔴 Saare 5 faults theek karo — demo se pehle clean slate
	$(VENV_PY) -m chaos.inject --heal-all --write

# ─────────────────────────────────────────────────────────────────────────────
# Tests + invariants
#
# 🔴 Yahan `-q` JAAN-BOOJH KAR nahi hai. `pyproject.toml:34` ka
#    `addopts = "-q --strict-markers --tb=short"` wo already deta hai.
#    Dono jagah likhne se pytest ko `-q -q` milta tha — aur pytest ka `-q`
#    **cumulative** hai: do baar dene pe wo summary line hi gayab kar deta hai.
#
#    2026-08-24 pe iska asli nateeja ye tha: `make test` 117 dots print karta
#    tha aur "117 passed in 0.86s" wali line **nahi**. Yaani output se ye pata
#    hi nahi chalta tha ki kitne test chale, aur kitne SKIP hue. Ek suite jo
#    apna count na bataye, usme 13 test chup-chaap skip ho sakte hain aur
#    screen bilkul green dikhegi (D51 tripwire 2 ka exact failure mode).
# ─────────────────────────────────────────────────────────────────────────────
test: ## pytest chalao (config pyproject.toml:17–90 mein hai)
	$(VENV_PY) -m pytest

# ─────────────────────────────────────────────────────────────────────────────
# lint — CLAUDE.md §3 ke wo constraints jo grep se saabit ho sakte hain.
#
# Do design choices jo interview mein pooche jaa sakte hain:
#
#   1. `--include='*.py'` kyun? Kyunki pehla version `oncall/__pycache__/*.pyc`
#      pe match kar gaya — compiled bytecode mein wahi strings padi hoti hain.
#      Binary file pe grep match "❌" deta tha jabki source clean thi. Ek
#      false positive linter ko log ignore karna sikha deta hai.
#
#   2. Koi exception mechanism NAHI hai — na `# noqa`, na `# lint-ok`.
#      Ek linter jise ek comment likh kar chup karaya ja sakta hai, wo
#      convention hai, security boundary nahi. Iski keemat: source code mein
#      forbidden token prose mein bhi nahi likh sakte (dekho
#      `oncall/__init__.py` ka docstring). Wo keemat jaan-boojh kar chuni hai.
#
#   3. Check 4 ki paanch-token list **yahi** authoritative hai (doosri jagah
#      `CLAUDE.md` §3). `tests/test_inject.py` isi rule ko enforce karta hai,
#      aur wo is line ko **padh kar** compare karta hai
#      (`test_token_list_matches_makefile`) — apni alag list nahi rakhta.
#      Kyun: pehle Makefile 2 token grep karta tha aur test 4 — ek hi rule ke
#      do enforcer, do alag definitions. Wo drift hai, aur drift mein aadmi
#      wahi enforcer maanta hai jo usko pass karta hai.
#
# Har check ka `; exit 1` nahi hai — `!` ke saath grep ka exit code invert
# hota hai, aur make khud non-zero pe ruk jaata hai.
# ─────────────────────────────────────────────────────────────────────────────
lint: ## 🔒 Security invariants — CLAUDE.md §3 ke grep-checkable rules
	@echo "── 1. oncall/ mein shell spawn karne ka koi tareeka nahi ──"
	@! grep -rnE --include='*.py' \
		'^[[:space:]]*(import|from)[[:space:]]+(subprocess|pty|commands)|os\.(system|popen|exec[lv])|shell[[:space:]]*=[[:space:]]*True|\.popen[[:space:]]*\(' \
		oncall/ || (echo "❌ shell spawn mila oncall/ mein" && exit 1)
	@echo "✅ clean"
	@echo "── 2. oncall/ mein LLM-as-judge nahi ──"
	@! grep -rni --include='*.py' 'judge' oncall/ \
		|| (echo "❌ 'judge' mila — verdict path mein model ghus gaya" && exit 1)
	@echo "✅ clean"
	@echo "── 3. oncall/ chaos/ se import nahi karta (ground truth invisible) ──"
	@! grep -rnE --include='*.py' '^[[:space:]]*(from|import)[[:space:]]+chaos' oncall/ \
		|| (echo "❌ oncall/ ne chaos/ import kiya" && exit 1)
	@echo "✅ clean"
	@echo "── 4. oncall/ ground truth fields nahi padhta ──"
	@! grep -rnE --include='*.py' \
		'correct_action|correct_target|relevant_runbooks|also_acceptable|is_relevant' \
		oncall/ || (echo "❌ ground truth field mila oncall/ mein" && exit 1)
	@echo "✅ clean"
	@echo "── 5. runtime code mein container runtime ka naam nahi (D22) ──"
	@! grep -rniE --include='*.py' 'colima|docker desktop' oncall/ \
		|| (echo "❌ runtime naam leak hua" && exit 1)
	@echo "✅ clean"
	@echo "── 6. docs/ git mein nahi (R15) ──"
	@if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then \
		if git ls-files docs/ | grep -qv '^docs/images/'; then \
			echo "❌ docs/ TRACKED hai — R15 fired" && exit 1; \
		else echo "✅ docs/ untracked (docs/images/ screenshots allowed)"; fi; \
	else echo "⚠️  git repo nahi hai — pehle git init chalao"; fi

# ─────────────────────────────────────────────────────────────────────────────
# docs-lint — CLAUDE.md §6 ka wo hissa jo machine se check ho sakta hai.
#
# Rule: `docs/` ki har file **Hinglish** mein hai — yaani Latin script mein
# likhi Hindi. Devanagari (देवनागरी) nahi, aur Cyrillic bilkul nahi.
#
# 🔴 Ye check kyun exist karta hai: main (Claude) ye galti **chaar baar** kar
#    chuka hoon — ek Devanagari danda, "चौथा column", "जैसे tokens", aur ek
#    Cyrillic "а" jo "Mota" ke andar ghus gaya tha. Cyrillic wali sabse buri
#    hai: `а` (U+0430) aur `a` (U+0061) screen pe **bilkul ek jaise** dikhte
#    hain. Aankh se review karna is class ki galti pe kaam hi nahi karta.
#
# ⚠️ Ye `lint` ka 7va check JAAN-BOOJH KAR nahi banaya. `lint` ka apna ek
#    contract hai — "6 security invariants" — aur wo number 8 se zyada jagah
#    docs mein cite hua hai. Ek check add kar ke usko 7 karna un saare
#    citations ko ek saath jhootha kar deta (CLAUDE.md §7 ka wahi sabak:
#    "a section number is an interface once someone links to it"). Isliye
#    alag target. Aur waise bhi ye ek **style** check hai, security nahi —
#    dono ko ek target mein milana unka matlab kamzor karta hai.
#
# `python3` use hota hai, `$(VENV_PY)` nahi — ye check `.venv` banne se
# pehle bhi chalna chahiye, kyunki docs code se pehle likhi jaati hain.
#
# Koi exception list nahi hai (`lint` ke comment #2 wali wajah). Agar kisi doc
# ko ek homoglyph ka ZIKR karna hai, toh wo character paste nahi karegi — uska
# codepoint likhegi (`U+0430`). RISKS.md §"broker ko kaise todunga" isi tarah
# 2026-08-24 pe theek hui (D60).
# ─────────────────────────────────────────────────────────────────────────────
docs-lint: ## 🔤 docs/ mein Devanagari ya Cyrillic character nahi (CLAUDE.md §6, D60)
	@python3 -c 'import pathlib,re,sys,unicodedata; \
bad=re.compile("[ऀ-ॿЀ-ӿ]"); \
hits=[(str(p),i,m.group()) for p in sorted(pathlib.Path("docs").glob("*.md")) \
      for i,l in enumerate(p.read_text().splitlines(),1) for m in bad.finditer(l)]; \
[print("❌ %s:%d  %r (%s)" % (f,i,c,unicodedata.name(c,"?"))) for f,i,c in hits]; \
print("✅ clean — %d file, koi non-Latin script nahi" % len(list(pathlib.Path("docs").glob("*.md")))) if not hits \
  else print("\n🔴 %d character mile. Hinglish = Latin script (CLAUDE.md §6)." % len(hits)); \
sys.exit(1 if hits else 0)'

# ─────────────────────────────────────────────────────────────────────────────
# web — the public tier (Next.js). Separate from `serve`, deliberately.
#
# 🔴 Two tiers, and the split IS the security design (`web/README.md`):
#      `make serve`  → control plane, 127.0.0.1:8000, holds the Docker socket
#      `make web`    → public tier,   127.0.0.1:3000, reads only
#
#    The web tier is the one that can safely be exposed, because it cannot act.
#    Never put the control plane behind a public port to "make the buttons work".
#
# `web-install` is separate from `install` on purpose: the Python side must stay
# runnable without Node, and `make verify` must not need node_modules.
# ─────────────────────────────────────────────────────────────────────────────
web-install: ## Install the web tier deps (~380 packages)
	cd web && npm install

web-schema: ## 🔴 Create web_users + web_sessions (idempotent)
	docker exec -i oncall-db psql -U oncall -d oncall < web/db/web_schema.sql

web: ## Start the web tier dev server (http://127.0.0.1:3000)
	cd web && npm run dev

web-build: ## Production build + typecheck
	cd web && npm run build

# ─────────────────────────────────────────────────────────────────────────────
# code-lint — source code must be English (CLAUDE.md §6a, audit stream A1)
#
# The mirror image of `docs-lint`. That target asserts docs/ is Latin script;
# this one asserts that everything OUTSIDE docs/ is English.
#
# 🔴 Deliberately NOT in `verify` yet. As of 2026-08-27 the baseline is 5,180
#    Hinglish lines across 52 files, so wiring it into the gate today would
#    make `make verify` red for every unrelated change — and a gate that is
#    always red is a gate everyone learns to ignore. It moves into `verify`
#    the day the count reaches zero. That is audit stream A1's exit gate, and
#    this number is the burn-down chart for it.
# ─────────────────────────────────────────────────────────────────────────────
code-lint: ## 🔤 Source code English-only check (CLAUDE.md §6a) — reports, does not gate yet
	@python3 scripts/code_lint.py

# ─────────────────────────────────────────────────────────────────────────────
# verify — teen checks, ek naam. D60 ka falsifier #3 isi ke bina OPEN tha.
#
# Problem jo ye solve karta hai: `make test` sirf pytest chalata hai. Yaani ek
# security invariant (shell ban, ground-truth leak) TOOT sakta hai aur
# `make test` phir bhi green rahega. Aur `docs-lint` ka to koi habit hi nahi
# tha — target exist karta tha, use karne ki aadat nahi.
#
# 🔴 Order jaan-boojh kar ye hai: lint → docs-lint → test.
#    `lint` sabse sasta hai (grep) aur sabse important (security boundary).
#    `test` sabse mehnga hai (0.85 s, aur Phase 5 ke baad zyada). make pehle
#    fail pe ruk jaata hai, toh sasta-aur-critical check pehle chalna chahiye.
#
# ⚠️ Ye `make up` ki tarah kuch start nahi karta aur DB ko chhoota nahi —
#    teeno checks pure logic pe hain, isliye ye `make install` ke turant baad
#    chal sakta hai, kisi container ke bina.
# ─────────────────────────────────────────────────────────────────────────────
verify: lint docs-lint test ## ✅ Sab kuch check karo — lint + docs-lint + test
	@echo ""
	@echo "✅ verify: teeno paas — 6 security invariant, docs Hinglish, $$($(VENV_PY) -m pytest --collect-only -q 2>/dev/null | awk -F': ' '/^tests\// {s+=$$2} END {print s}') tests collected"

fmt: ## ruff se format + auto-fixable lint (⚠️ ruff alag se install karna hai)
	@command -v ruff >/dev/null 2>&1 \
		|| (echo "⚠️  ruff nahi hai — pip install ruff (runtime dep nahi hai, pyproject.toml dekho)" && exit 1)
	ruff format .
	ruff check --fix .

clean: ## __pycache__ etc hatao
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache

nuke: ## 🔴 Containers + volumes DELETE karo (shopdb ka data chala jaayega)
	$(COMPOSE) down -v
