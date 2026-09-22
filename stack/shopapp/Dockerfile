# ─────────────────────────────────────────────────────────────────────────────
# shopapp ka Dockerfile.
#
# 🔴 `python:3.11-slim`, aur teen alternatives jo reject hue:
#
#   * `python:3.11` (full)   — ~1 GB. 14-17 GB free disk pe ye kharcha bekaar
#                              hai. Isme gcc/build tools hain jo humein nahi
#                              chahiye kyunki `psycopg[binary]` pre-compiled
#                              wheel hai.
#   * `python:3.11-alpine`   — ~50 MB, sabse chhota, LEKIN musl libc use karta
#                              hai. Bahut Python wheels manylinux (glibc) ke
#                              liye bani hain, toh alpine pe pip source se
#                              build karne lagta hai — jo 8 GB machine pe
#                              minutes leta hai aur kabhi fail hota hai.
#                              Chhota image, lamba build. Galat trade.
#   * distroless             — sabse secure, lekin usme shell nahi hai. Debug
#                              karne ke liye `docker exec -it ... sh` chahiye
#                              hota hai, aur ye ek learning project hai jise
#                              todna-jodna hai.
#
#   `slim` = glibc (wheels chalti hain) + ~130 MB. Beech ka sahi rasta.
#
# 🔴 Version TAG pinned hai (`3.11-slim`), `latest` nahi. `latest` ka matlab
#    hai ki kal ka build aaj ke build se alag ho sakta hai — jo ek
#    reproducibility project mein sabse bada paap hai.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# PYTHONUNBUFFERED=1 — 🔴 ye is project ke liye optional nahi hai.
# Iske bina Python stdout ko buffer karta hai, aur `docker logs` mein lines
# der se aati hain (ya crash pe kabhi nahi aati). OnCall ka log collector
# `docker logs` padhta hai — buffered logs ka matlab hai ki incident ke waqt
# ki asli error line gayab ho sakti hai. Ek observability project mein
# unbuffered output ek requirement hai.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 🔴 Pehle sirf requirements.txt copy, phir install, phir baaki code.
#    Kyun ye order: Docker layers cache karta hai. Agar app.py badalne pe
#    requirements.txt bhi copy hoti, toh har code change pe `pip install`
#    dobara chalta (~40 second). Is order mein pip layer cached rehti hai aur
#    code change pe rebuild ~2 second leta hai. Is project mein shopapp ko
#    dozens of baar rebuild karna hai, toh ye seconds add up hote hain.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# 🔒 Non-root user. Ye monitored service internet-facing nahi hai, lekin:
#    OnCall ke paas Docker socket hai (root-equivalent). Agar shopapp compromise
#    ho jaaye aur root ho, toh container escape ka rasta chhota ho jaata hai.
#    Defence in depth — ek line ki keemat pe (THREAT_MODEL.md §4).
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# 🔴 `--workers 1` jaan-boojh kar. Do reasons:
#    1. Pool math: POOL_MAX=10 per worker. 4 workers = 40 connections maang
#       sakte hain. Phir F4 (`max_connections=5`) ka arithmetic hi badal jaata
#       hai aur measurement worker-count pe depend karne lagta hai — 1 worker pe
#       "10 maango, 5 milega" saaf hai; 4 workers pe "40 maango, 5 milega" pe
#       pool pehle hi burst pe gir jaata aur naive probe bhi fail ho sakti thi.
#    2. RAM: har uvicorn worker ~60-80 MB leta hai. 8 GB machine pe 4 workers
#       ek monitored *test fixture* ke liye bekaar kharcha hai (RISKS.md R3).
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

# 🔴 `--host 0.0.0.0` yahan SAFE hai aur ye ek exception hai jo samajhna zaruri
#    hai. Container ke andar 0.0.0.0 ka matlab "container ke network interface
#    pe suno" hai — host pe expose hone ka faisla `docker-compose.yml` ke port
#    mapping se hota hai, aur wahan humne `127.0.0.1:8001:8000` likha hai.
#    Container ke andar 127.0.0.1 likhne se port mapping kaam hi nahi karta.
#
#    Ye OnCall ke control plane se ULTA hai — wo host pe direct chalta hai,
#    toh uska bind address asli security boundary hai aur wahan 127.0.0.1
#    hardcoded hai (oncall/config.py).
