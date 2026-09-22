#!/usr/bin/env bash
# ~/.docker/ mein Docker Desktop ka bacha hua malba saaf karta hai.
#
# Kyun ye script exist karti hai (poora reasoning: docs/RUNBOOK.md §0a):
#   Docker Desktop uninstall karne se uska CONFIG delete nahi hota. ~/.docker/ mein teen
#   cheezein bach jaati hain jo Colima ke saath toot ti hain:
#     1. cli-plugins/docker-compose  -> ek DANGLING symlink deleted Docker.app pe.
#        Docker CLI pehle isi dir mein dhoondta hai, naam mil jaata hai, file nahi milti,
#        aur brew ka cliPluginsExtraDirs kabhi try hi nahi hota. Isliye symlink DELETE
#        karna zaroori hai — sirf extra dir add karna kaafi NAHI hai.
#     2. config.json ka "credsStore": "desktop"  -> docker-credential-desktop PATH mein
#        nahi hai, aur `docker pull` HAR baar creds helper poochta hai (public image pe bhi).
#        Yaani ye `make up` pe phatta hai, install ke kaafi baad.
#     3. config.json ka "currentContext": "desktop-linux"  -> ek gayab socket.
#        `colima start` isko theek kar deti hai, lekin sirf tab jab wo safal ho.
#
# Ye script DATABASE ko choo ti nahi hai, isliye iska default --write hai (CLAUDE.md §3 ka
# dry-run rule DB-writing scripts pe hai). Backup hamesha liya jaata hai.
#
# Idempotent: dobara chalane se kuch nahi badalta.
#
# Usage:
#   bash scripts/fix_docker_config.sh              # fix kar do
#   bash scripts/fix_docker_config.sh --dry-run    # sirf batao kya karega

set -euo pipefail

DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --write)   DRY_RUN=0 ;;  # no-op: default already write. Sirf isliye accept karte
                                 # hain ki baaki scripts --write maangti hain, aur reader
                                 # ki muscle memory yahan galti na banaye.
        *) echo "unknown flag: $arg (use --dry-run)" >&2; exit 2 ;;
    esac
done

DOCKER_DIR="$HOME/.docker"
CONFIG="$DOCKER_DIR/config.json"
BACKUP="$CONFIG.bak-docker-desktop"
PLUGIN_DIR="$DOCKER_DIR/cli-plugins"
BREW_PLUGIN_DIR="/opt/homebrew/lib/docker/cli-plugins"

# python3.11 pinned hai (D23) — `python3` is machine pe 3.14 hai aur hum usko use nahi karte.
# Fallback rakha hai kyunki ye script venv se pehle chalti hai.
if [ -x /opt/homebrew/bin/python3.11 ]; then
    PY=/opt/homebrew/bin/python3.11
elif command -v python3 >/dev/null 2>&1; then
    PY="$(command -v python3)"
else
    echo "❌ python3 nahi mila — ye script JSON edit ke liye python use karti hai" >&2
    exit 1
fi

say() { printf '%s\n' "$*"; }
if [ "$DRY_RUN" -eq 1 ]; then
    say "🔍 DRY RUN — kuch bhi likha nahi jaayega"
else
    say "✏️  WRITE mode — backup: $BACKUP"
fi
say ""

# ---------------------------------------------------------------- 1. dangling plugins
say "── 1. Dangling CLI plugin symlinks (deleted Docker.app ki taraf) ──"
dangling=0
if [ -d "$PLUGIN_DIR" ]; then
    for f in "$PLUGIN_DIR"/*; do
        # -L = symlink hai, ! -e = target exist nahi karta. Dono chahiye: ek asli plugin
        # binary bhi -L nahi hota, aur ek zinda symlink -e paas kar jaata hai.
        if [ -L "$f" ] && [ ! -e "$f" ]; then
            dangling=$((dangling + 1))
            say "   DANGLING  $(basename "$f") -> $(readlink "$f")"
            [ "$DRY_RUN" -eq 0 ] && rm -f "$f"
        fi
    done
fi
if [ "$dangling" -eq 0 ]; then
    say "   ✅ koi dangling symlink nahi (ya pehle hi saaf ho chuke)"
else
    [ "$DRY_RUN" -eq 0 ] && say "   ✅ $dangling symlink delete hue"
fi
say ""

# ---------------------------------------------------------------- 2. config.json
say "── 2. ~/.docker/config.json ──"
if [ ! -f "$CONFIG" ]; then
    say "   ⚠️  config.json exist nahi karta — naya banayenge"
    [ "$DRY_RUN" -eq 0 ] && mkdir -p "$DOCKER_DIR" && printf '{}\n' > "$CONFIG"
fi

if [ "$DRY_RUN" -eq 0 ] && [ ! -f "$BACKUP" ]; then
    cp "$CONFIG" "$BACKUP"
    say "   ✅ backup liya: $BACKUP"
fi

# JSON ko python se edit karte hain, sed se nahi — sed JSON ki nesting samajhta nahi aur
# ek trailing comma chhod dene se poori file invalid ho jaati hai. 20 samajhne wali line.
CONFIG_PATH="$CONFIG" BREW_DIR="$BREW_PLUGIN_DIR" DRY="$DRY_RUN" "$PY" <<'PYEOF'
import json, os, sys

path = os.environ["CONFIG_PATH"]
brew_dir = os.environ["BREW_DIR"]
dry = os.environ["DRY"] == "1"

with open(path) as fh:
    text = fh.read().strip() or "{}"
cfg = json.loads(text)

changes = []

# credsStore: docker-credential-desktop gayab hai. Hum kisi registry pe login nahi karte
# (postgres/redis/python:3.11-slim sab public), toh helper ki zaroorat hi nahi.
if "credsStore" in cfg:
    changes.append(f'removed  "credsStore": {cfg.pop("credsStore")!r}')

# currentContext: deleted Docker Desktop ka socket. `colima start` apna context set karegi.
if "currentContext" in cfg:
    changes.append(f'removed  "currentContext": {cfg.pop("currentContext")!r}')

# cliPluginsExtraDirs: brew ka caveat. List mein add karte hain, replace nahi — reader ke
# paas pehle se koi aur dir ho sakti hai.
dirs = cfg.get("cliPluginsExtraDirs") or []
if not isinstance(dirs, list):
    dirs = []
if brew_dir not in dirs:
    dirs.append(brew_dir)
    cfg["cliPluginsExtraDirs"] = dirs
    changes.append(f'added    "cliPluginsExtraDirs": {brew_dir!r}')

# auths key rakhna zaroori nahi lekin Docker isko expect karta hai, toh khaali hi rehne do.
cfg.setdefault("auths", {})

if not changes:
    print("   ✅ config.json pehle hi theek hai — koi badlaav nahi")
else:
    for c in changes:
        print(f"   {c}")
    if not dry:
        with open(path, "w") as fh:
            json.dump(cfg, fh, indent=2)
            fh.write("\n")
        print("   ✅ config.json likh diya")
PYEOF
say ""

# ---------------------------------------------------------------- 3. verify
say "── 3. Verify ──"
if [ -x "$BREW_PLUGIN_DIR/docker-compose" ] || [ -L "$BREW_PLUGIN_DIR/docker-compose" ]; then
    say "   ✅ brew ka compose plugin maujood: $BREW_PLUGIN_DIR/docker-compose"
else
    say "   ❌ $BREW_PLUGIN_DIR/docker-compose nahi mila — 'brew install docker-compose' chalao"
fi

if command -v docker >/dev/null 2>&1; then
    if [ "$DRY_RUN" -eq 1 ]; then
        say "   ⏭  'docker compose version' skip (dry run — config abhi badla nahi)"
    elif out="$(docker compose version 2>&1)"; then
        say "   ✅ $out"
    else
        say "   ❌ docker compose abhi bhi fail: $out"
        exit 1
    fi
else
    say "   ❌ docker CLI PATH mein nahi hai"
fi
say ""
say "🔴 Agla step: colima start --cpu 2 --memory 4 --disk 20"
