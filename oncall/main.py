"""OnCall control plane ka entrypoint.

Do tareeke (dono same app dete hain):
    uvicorn oncall.main:app --host 127.0.0.1 --port 8000   # README ka tareeka
    .venv/bin/python -m oncall.main                        # programmatic

🔴 `app` module-level isliye hai ki uvicorn ka CLI import kar sake; bana bhi
   factory se hota hai (`create_app`) — import pe koi connection nahi khulta,
   sirf schema VERIFY hota hai (read-only, D50). Schema adhoora ho toh process
   yahin ruk jaata hai — wahi boot-time-loud-failure rule.
"""

from __future__ import annotations

from oncall.api.app import create_app
from oncall.config import LISTEN_HOST, LISTEN_PORT

# 🔴 HOST hardcoded constant se — env var SE NAHI (`config.py` ka header:
#    Docker socket root-equivalent hai, listen address security boundary hai).
HOST = LISTEN_HOST
PORT = LISTEN_PORT

app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
