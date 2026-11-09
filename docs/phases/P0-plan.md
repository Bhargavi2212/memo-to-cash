# Phase P0 — Build plan (how we'll execute `P0.md`)

Spec: `docs/phases/P0.md` (source of truth). This file is the working plan: order, dependencies, checks, and the gaps found while planning.
Branch: `p0-skeleton` from `main` @ `05ef20c`. FRs: none. Open check closed: #5 (Llama key + model id).
Local tools confirmed: Node 22, npm 11, Python 3.12.7, git. `gh` is not installed → PRs are opened on github.com.

## 1. Who does what, and what blocks what
| Bhargavi (P0.md §2) | Needed by |
|---|---|
| Render account + hackathon credits | step 9 |
| Neon project in **AWS US East (N. Virginia)**: `main` = prod, branches `dev`, `test` | step 4 |
| Local `.env`: `APP_ENV=dev`, `DATABASE_URL` + `DATABASE_URL_UNPOOLED` (dev), `TEST_DATABASE_URL`, `ADMIN_TOKEN`, `APP_BASE_URL`, `API_PUBLIC_URL`, other secrets — **tell Claude Code when done** (it can't read `.env`) | steps 4, 6 |
| APIMatic plugin | P1 (not P0) |
| Create the Render Blueprint, enter secrets | step 9, after the merge |

Steps 1, 2, 3, 5, 7, 8 run immediately (tests mock everything). Steps 4 and 6 wait for `.env`. Step 9 waits for Render.

## 2. Steps (one commit each; tests + ruff + tsc + lint green before every commit)
| # | Commit | Build | Check |
|---|---|---|---|
| 1 | `chore(p0): next.js scaffold` | `create-next-app@latest` in `..\mtc-scaffold`; copy only the files P0.md lists; `lint` script; ESLint ignores `.venv/**`, `backend/**`, `scripts/**`; `tsconfig` exclude `.venv`; merge `.gitignore`; delete sibling | `npm run dev` → :3000; `tsc`, `lint` clean |
| 2 | `chore(p0): python backend skeleton` | `requirements.txt` (**incl. `uvicorn[standard]`**, see G1), `requirements-dev.txt`, `pyproject.toml` (ruff 100/py312; pytest `testpaths=["tests"]`, **`pythonpath=["."]`**, G3), package `__init__.py`s, `tests/unit/`, `tests/integration/`, **`.gitattributes`** (`* text=auto eol=lf`, G4) | `pip install` in `.venv`; ruff check + format clean |
| 3 | `feat(p0): config with sandbox guard` | `backend/core/config.py`: `Settings` (pydantic-settings, `.env`, **`extra="ignore"`**, G9), `load_settings()` (strip `/`, sandbox guard, `APP_ENV ∈ dev|prod|test`), module-level `settings`; 3 required fields, rest optional; `SecretStr`; `sqlalchemy_url()` | `tests/unit/test_config.py` (`_env_file=None`): live URL raises; bad env raises; sandbox ± `/` passes; repr hides secrets; `postgres://`/`postgresql://` → `postgresql+psycopg://`, query string kept |
| 4 | `feat(p0): db session + alembic baseline` | `backend/db/session.py` (pool 5, pre_ping, recycle 300, `sslmode=require`, **`prepare_threshold=None`** G10, `get_session`); `alembic.ini`, `migrations/env.py` (same `Settings`, unpooled URL), `script.py.mako`, empty `0001_baseline` | Neon `dev`: `alembic upgrade head`; `alembic current` = 0001 |
| 5 | `feat(p0): health endpoints` | `backend/main.py` (docs `/api/docs`, **`openapi_url="/api/openapi.json"`** G11); `backend/routes/health.py`: `/api/health` `{ok,sandbox,db,env}` (DB 5 s, never 500); `/api/health/deep`: 503 if no `ADMIN_TOKEN` configured, 401 if wrong/missing header, else DB + PayPal OAuth + Llama ping in parallel (5/5/8 s, no retry); never returns secrets | `tests/integration/test_health.py`, all mocked, no DB needed (G5): shapes, `db:false` without 500, 401, 503, `paypal_token:false` when keys missing, no secret strings in responses |
| 6 | `feat(p0): llama client minimal` | `backend/agent/llm.py` (OpenAI client → Llama compat, 8 s, 1 retry; `ping(retry=False)`); `scripts/__init__.py`, `scripts/llama_smoke.py` (prints ok/model/ms; lists model ids on 404) | `python -m scripts.llama_smoke` → ok. **Fail → record error (no key) and stop for Bhargavi.** |
| 7 | `chore(p0): render blueprint + api proxy + landing shell` | `render.yaml`: `memo-to-cash-api` (Starter, python, `PYTHON_VERSION=3.12.7`, health `/api/health`), `memo-to-cash-web` (Starter, node 20, `API_INTERNAL_HOSTPORT` via `fromService … hostport`), both **`region: virginia`** (G12, matches Neon AWS US East); `mtc-shared` = non-secret only; secrets per service `sync: false`; **no `NODE_ENV=production` at build** (G6); `autoDeploy` main. `app/api/[...path]/route.ts`: runtime proxy to `API_INTERNAL_BASE` or `http://API_INTERNAL_HOSTPORT` (default `127.0.0.1:8000`); GET/POST/PUT/DELETE, query, body, `cookie`/`content-type`/`authorization`, **passes `X-Forwarded-For` through unchanged** (G2); returns status, body, every `Set-Cookie` (`getSetCookie()`); `redirect: "manual"`; 502 JSON if api unreachable. Landing shell (`page.tsx`, `layout.tsx`, `globals.css` `@theme` tokens, Inter) | uvicorn :8000 + `npm run dev` → `localhost:3000/api/health` JSON via proxy; `npm run build` passes |
| 8 | `ci(p0): github actions` | `.github/workflows/ci.yml`: env `APP_ENV=test`, sandbox URL, dummy values (valid-looking `DATABASE_URL`); Python 3.12 job: install, ruff, format check, pytest; Node 20 job: `npm ci`, tsc, lint. No Postgres service yet (added in P1, G5) | green on first push of `p0-skeleton` |
| 9 | deploy (no commit) | Claude Code pushes the branch → Bhargavi opens the PR on github.com → CI green → merge. Bhargavi creates the Blueprint + secrets, and runs `alembic upgrade head` once against prod with prod URLs in her own shell | P0.md §4 on the live URL |
| 10 | `docs(p0): status + live link` | PROJECT.md: P0 ✅, live URL, decisions line (model id), open check 5 result | — |

## 3. Done when
Exactly P0.md §4: landing shell live · `/api/health` = `{"ok":true,"sandbox":true,"db":true,"env":"prod"}` via the proxy · `/api/health/deep` all true with model id + ms, 401 without token · live-URL refusal proven by unit test · all checks green locally and in CI · clean `git status` · PROJECT.md updated.

## 4. Gaps and misalignments found while planning — all now written into `P0.md`
| # | Finding | Severity | Resolution (in P0.md) |
|---|---|---|---|
| G1 | `uvicorn` was only in `requirements-dev.txt`, but Render installs `requirements.txt` and starts `uvicorn` → api would fail to start | Blocker | Moved to `requirements.txt` (step 2) |
| G2 | Proxy was to "append the client IP" to `X-Forwarded-For`; a route handler has no socket IP and Render's edge already sets it | Spec wording | Pass the header through unchanged (step 7) |
| G3 | pytest couldn't import `backend` without `pythonpath=["."]` | Small | Added (step 2) |
| G4 | LF→CRLF warnings on every commit | Cosmetic | `.gitattributes` (step 2, §6, PROJECT.md tree) |
| G5 | CLAUDE.md §6: integration tests use real Postgres, but P0's `test_health.py` is mocked and CI has no Postgres until P1 | Alignment | Stated in step 5: needs no `TEST_DATABASE_URL`; P1 adds `conftest.py` + `postgres:16` in CI |
| G6 | `NODE_ENV=production` at build would make `npm ci` skip devDependencies → build fails | Risk | "Never set `NODE_ENV`" (step 7) |
| G7 | Proxy behaviour when the api is down was unspecified | Small | 502 `API_UNREACHABLE` in BACKEND_SCHEMA error shape; checked locally (step 7) |
| G8 | `next start` must listen on Render's `$PORT` | Check | Reads `PORT`; fallback `next start -p $PORT` (risks table) |
| G9 | pydantic-settings forbids unknown keys by default; local `.env` holds keys the backend doesn't declare (`NEXT_PUBLIC_PAYPAL_CLIENT_ID`, …) → startup error | Blocker | `extra="ignore"` (step 3) |
| G10 | psycopg 3 switches to server-side prepared statements after 5 runs of a query, which can break through Neon's connection pooler | Risk | `connect_args={"prepare_threshold": None}` (step 4) |
| G11 | FastAPI serves the OpenAPI JSON at `/openapi.json`, outside `/api/*`, so `/api/docs` would be blank through the web proxy | Small | `openapi_url="/api/openapi.json"`, `redoc_url=None` (step 5) |
| G12 | Regions unspecified: Render default vs Neon default may be on different coasts (~70 ms per DB round-trip) | Perf | Neon AWS US East + Render `virginia` (§2 step 2, step 7) |
| G13 | `PYTHON_VERSION=3.12.x` in step 7 vs "full version" in the risks table | Inconsistency | `3.12.7` everywhere |
| G14 | Step 5's test list missed 503, `paypal_token:false`, "no secrets in responses" (which this plan had) | Alignment | Added to step 5 |
| G15 | Step 9 didn't say who opens the PR (no `gh`) or who runs the production migration (needs prod secrets) | Ownership | Bhargavi does both; Claude Code never sees prod URLs (step 9) |
| G16 | §2 step 0 still read as a to-do; §6 file list missed `next-env.d.ts`, `public/`, `.gitattributes` | Cosmetic | Marked done; list completed |

## 5. Stop-and-ask points
Llama smoke fails · web → api unreachable on Render (fallback `API_INTERNAL_BASE`) · Python 3.12 not used by Render · any spec step that turns out impossible (e.g. scaffold's ESLint config shape differs — adapt and report).

## 6. Build log — deviations from P0.md found while building (P0.md updated to match)
| # | What happened | Decision |
|---|---|---|
| B1 | `create-next-app@latest` = **Next.js 16.4.5**: Tailwind v4 via the Turbopack CSS loader (no `postcss.config`), Cache Components on, `next-env.d.ts` git-ignored and generated, `AGENTS.md` written (and re-created by `next dev`) | Follow the scaffold: keep `next-env.d.ts` ignored, commit `AGENTS.md`, CI runs `next typegen` before `tsc` |
| B2 | With Cache Components, route segment configs like `export const dynamic` are removed; a handler that reads the request already runs per request | Proxy has no `dynamic` export; build output shows it as dynamic (ƒ) |
| B3 | `/api/health/deep` calls `llm.ping()` | Llama client (step 6) committed before health (step 5) |
| B4 | `render.yaml` uses `autoDeployTrigger: checksPass` | Render deploys `main` only after GitHub CI is green (matches "never deploy red CI"); if the Blueprint rejects the field, fall back to `autoDeployTrigger: commit` |
| B5 | Service names give the URLs only if free on Render | After creating the Blueprint, confirm the real URLs; if Render added a suffix, update `APP_BASE_URL`/`API_PUBLIC_URL` in `mtc-shared` |
| B6 | Scaffold sample SVGs (incl. the Vercel logo) | Removed; `public/` is empty (not tracked) |
| B7 | `sslmode=require` only when the URL doesn't set it and the host isn't local | Keeps P1's CI Postgres (no TLS) working |
