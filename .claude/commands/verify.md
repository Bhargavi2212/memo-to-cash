Run every check and report results honestly (paste failures, don't summarise them away):
1. `ruff check . ; ruff format --check .`
2. `pytest -q`
3. `npx tsc --noEmit ; npm run lint`
4. `git status` — confirm no .env, .venv, node_modules, secrets are staged.
5. Grep for forbidden things: floats used for money (`float(` near amount/cents), `print(` of tokens/keys, real client names.
If anything fails, stop and fix or report. Do not claim done.
