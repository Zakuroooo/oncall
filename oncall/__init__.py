"""OnCall — self-healing infrastructure agent with a human approval gate.

Is package ka ek hi non-negotiable rule hai:

    Yahan kahin bhi LLM verdict nahi deta.

`oncall/oracle/health.py` hi ek jagah hai jahan "recovered ya nahi" decide hota hai,
aur wo decision ek live HTTP probe se aata hai — kisi model ki opinion se nahi.

Do aur invariants jo grep se check hote hain (`make lint`):
  * `oncall/` ke andar `chaos/` se koi import nahi — ground truth agent ko
    invisible hai (CLAUDE.md §3).
  * `oncall/` ke andar shell spawn karne ka koi tareeka nahi. Containers sirf
    Docker SDK se, structured keyword arguments ke saath.

🔴 Ye docstring un forbidden tokens ko likhta NAHI hai, sirf describe karta hai.
   Kyun: `make lint` ka grep poore `oncall/` tree pe chalta hai aur usme koi
   exception mechanism nahi hai — na `# noqa`, na `# lint-ok`. Ek linter jise
   comment likh kar chup karaya ja sakta hai, wo security boundary nahi hai.
   Toh authoritative list do jagah rehti hai: `Makefile` ka `lint` target, aur
   `CLAUDE.md` §3. Yahan sirf pointer.
"""

__version__ = "0.1.0"
