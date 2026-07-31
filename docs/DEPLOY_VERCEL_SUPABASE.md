# Deploying Shop Ops — Render + Supabase

Updated 2026-07-31. Quick path: **Supabase** (database) + **Render** (backend + worker + static frontend, all from one `render.yaml` Blueprint). Vercel remains a fine alternative for the frontend (Step 3, Option B).

## What goes where (and why)

| Piece | Host | Why |
| --- | --- | --- |
| **Frontend** (React/Vite static build) | **Vercel** | It's a static bundle — Vercel's sweet spot. Free tier is fine. |
| **Database** | **Supabase Postgres** | The project's planned DB home. Managed Postgres + backups; we use it as plain Postgres (no Supabase-specific features in app code). |
| **Backend API + background worker** | **A container host** (Render / Railway / Fly.io) **or the campus box** | ⚠️ These CANNOT run on Vercel. The worker is a long-lived loop (Odoo sync every minute-tick, notification sweeps, mailbox polls) and the API is a persistent FastAPI process. Vercel only runs short-lived serverless functions — the polite-sync architecture would silently die there. |

So "Vercel + Supabase" really means **Vercel (web) + Supabase (data) + one small always-on box for the Python services**. The campus box behind a Cloudflare Tunnel (the original plan in the brief) works; for a cloud test deployment, Render or Railway is the least setup — both run the repo's existing Docker images.

```
browser ──► Vercel (static frontend)
              │  VITE_API_BASE
              ▼
        backend API (container host / campus box) ──► Supabase Postgres
              ▲                                          ▲
        worker (same host, same image) ─────────────────┘
              └──► Odoo (reads + gated draft writes), SMTP, IMAP
```

---

## Quickstart (Render Blueprint path)

The repo's `render.yaml` Blueprint declares all three services (backend, worker, static frontend) plus a shared env group:

1. **[Step 1]** Create a Supabase project and grab the two connection strings (below).
2. **Restore (or migrate) the database FIRST** — see "Migrating the existing database" below. The backend's boot-time `alembic upgrade head` then sees the schema is current and no-ops.
3. Push the repo to GitHub (the Blueprint references it).
4. In [Render Dashboard](https://dashboard.render.com) → **Blueprints** → **New Blueprint Instance** → point at the repo → click through.
5. Render prompts for the `sync: false` values. Minimum to boot: `DATABASE_URL` (the 6543 pooler string). Odoo creds enable live reads; blank = fixture mode.
6. Once the backend is live, set `VITE_API_BASE` on the frontend service to the backend's URL, and `CORS_ORIGINS` (backend) + `APP_PUBLIC_URL` (env group) to the frontend's URL. Render rebuilds/redeploys on change.

## Migrating the existing database (the local stack → Supabase)

Bringing the current stack's data along preserves users, catalogs, the product blacklist, vendor rosters, app settings, and 24 months of synced history — no re-backfills.

1. Dump from the local stack (plain SQL, no owner/privilege baggage):

   ```bash
   docker compose -f infra/compose.yaml --project-directory . --project-name shopops \
     exec -T db pg_dump -U ilops -d ilops --no-owner --no-privileges \
     | gzip > backups/shopops-prod-seed.sql.gz
   ```

2. Restore into Supabase using the **direct** (5432) connection string. Plain `postgresql://` form works here (psql, not SQLAlchemy):

   ```bash
   gunzip -c backups/shopops-prod-seed.sql.gz | psql "$SUPABASE_DB_URL"
   ```

   No local `psql`? Pipe through the stack's own client:

   ```bash
   gunzip -c backups/shopops-prod-seed.sql.gz | docker compose -f infra/compose.yaml \
     --project-directory . --project-name shopops exec -T db psql "$SUPABASE_DB_URL"
   ```

3. **Post-restore hygiene — not optional:** the dump carries feature flags from the source stack, where Odoo write flags may be LIVE. Writes must be re-canaried **from the new deployment** before they're trusted, so turn everything off:

   ```bash
   psql "$SUPABASE_DB_URL" -c "UPDATE feature_flags SET enabled = false; SELECT name, enabled FROM feature_flags ORDER BY name;"
   ```

4. Fresh-start alternative (no data): skip the restore and just run migrations — the backend does it on boot (`RUN_MIGRATIONS=1`), or run `DATABASE_URL='...5432...' uv run alembic upgrade head` from `backend/`. Then seed (`python -m app.seeds.demo`) or import the roster and let the syncs backfill.

---

## Step 1 — Supabase project (the database)

1. Create a project at [supabase.com](https://supabase.com) (region close to the backend host).
2. From **Project Settings → Database**, collect the two connection strings:
   - **Session / direct** (port `5432`) — for migrations and one-off scripts.
   - **Transaction pooler** (port `6543`, PgBouncer) — for the app at runtime.
3. Convert them to SQLAlchemy form (the app uses the `psycopg` driver) and require TLS:

   ```
   postgresql+psycopg://postgres.<ref>:<PASSWORD>@<host>:6543/postgres?sslmode=require
   ```

4. Run the migrations **from your machine** against the *direct* (5432) string:

   ```bash
   cd backend
   DATABASE_URL='postgresql+psycopg://...:5432/...?sslmode=require' uv run alembic upgrade head
   ```

   (The backend container also runs `alembic upgrade head` on boot when
   `RUN_MIGRATIONS=1`, so this is belt-and-braces.)

Notes:
- The app treats Supabase as **plain Postgres** — don't enable RLS on these tables; the API owns authorization.
- Supabase's transaction pooler doesn't support `LISTEN/NOTIFY` or prepared statements across transactions — the app uses neither.
- Supabase Auth (email/SMS OTP) is the planned production login (`AUTH_MODE=supabase` + `SUPABASE_URL`/`SUPABASE_ANON_KEY`/`SUPABASE_JWT_SECRET`). For a test deployment you can stay on `AUTH_MODE=dev` — codes appear on the login screen, **anyone who can reach the URL can log in as anyone**, so dev mode + public URL should be short-lived.

## Step 2 — Backend + worker (the always-on pair)

Both services come from **one image**: `infra/docker/backend.Dockerfile` (the worker just runs a different command — see `infra/compose.yaml`).

### Option A — Render (recommended for cloud testing)

Use the `render.yaml` Blueprint (see Quickstart above). It configures both services correctly. A few Render-specific notes:

- **Background workers require the Starter plan** ($7/mo). On the free tier, deploy only the `shopops-backend` web service — the app works fine in fixture mode without sync.
- The Docker build context is the repo root (`.`), not the `infra/docker/` subfolder. Render reads `dockerContext` from the Blueprint — don't change it.
- The image bakes in the demo Odoo fixtures and the coordinator xlsx at build time (no volumes needed). First build takes ~3 min; subsequent builds use layer cache.
- After the backend deploys, seed demo data from the Render Shell tab on the backend service:
  ```bash
  python -m app.seeds.demo
  ```
  Or run it from your machine: `DATABASE_URL='...' uv run python -m app.seeds.demo`
- **Env Group tip**: in the Render dashboard, create an Env Group called `shopops-shared` with all the secrets, then attach it to both services. This keeps the two `DATABASE_URL` values in sync automatically — much better than editing each service separately.

Manual setup (without the Blueprint): create **two services from the same repo/Dockerfile**:

| | Backend | Worker |
| --- | --- | --- |
| Start command | *(image default — uvicorn)* | `python -m worker.main` |
| Dockerfile path | `infra/docker/backend.Dockerfile` | `infra/docker/backend.Dockerfile` |
| Docker context | `.` (repo root) | `.` (repo root) |
| Port | 8000, public | none |
| `RUN_MIGRATIONS` | `1` | `0` |

### Option B — the campus box (the brief's plan)

`docker compose -f infra/compose.yaml up -d` with a Cloudflare Tunnel pointing at the backend port (and optionally the built frontend via the `prod` profile) — no router port-forwarding, TLS included.

### Environment variables (both services, unless noted)

Copy `.env.example` as the checklist. The ones that matter for this deployment:

```bash
ENV=prod
DATABASE_URL=postgresql+psycopg://...:6543/...?sslmode=require   # Supabase pooler
APP_JWT_SECRET=<long random string — NOT the dev default>
AUTH_MODE=dev                      # or supabase (+ SUPABASE_* vars) when ready

# Odoo (live reads; writes stay flag-gated regardless)
ODOO_BASE_URL=https://...
ODOO_DB=...
ODOO_LOGIN=...
ODOO_PASSWORD=...
ODOO_WRITES_ENABLED=false          # keep the kill switch OFF until canaries pass on this deployment

# web
CORS_ORIGINS=https://<your-app>.vercel.app     # backend only; add custom domains too
APP_PUBLIC_URL=https://<your-app>.vercel.app   # where notification links point

# delivery (optional for a test deploy; sends simulate honestly when unset)
SMTP_HOST= SMTP_USERNAME= SMTP_PASSWORD= SMTP_FROM=...
IMAP_HOST=...                       # order-reply ingestion; blank = paste replies manually
# WhatsApp is on hold — leave WHATSAPP_BRIDGE_URL blank; the status page shows "on hold"
```

Secrets go in the host's environment settings, never in the repo.

## Step 3 — The frontend

### Option A — Render Static Site (in the Blueprint)

Nothing extra to create — `render.yaml` declares `shopops-frontend` (static build of `frontend/`, SPA rewrite included). Two values to connect after the first deploy:

1. On **shopops-frontend** → Environment: set `VITE_API_BASE=https://<the shopops-backend URL>`. Build-time only — Render rebuilds when it changes.
2. On the **shopops-shared env group / backend**: set `CORS_ORIGINS` and `APP_PUBLIC_URL` to the frontend's URL.

### Option B — Vercel

1. **Import the repo** in Vercel → set **Root Directory = `frontend`**. It auto-detects Vite (`npm run build`, output `dist`). `frontend/vercel.json` (committed) already carries the SPA rewrite so deep links like `/settings` load.
2. **Environment variable**: `VITE_API_BASE=https://<backend-host>` — the API client falls back to same-origin when unset, which only works when something proxies `/api` (dev, or the campus-box Caddy setup). On Vercel it must be set. Build-time only: change it → redeploy.
3. Deploy. Add the resulting domain to the backend's `CORS_ORIGINS` (step 2) and redeploy the backend if you set it before knowing the domain.

## Step 4 — First-boot checklist

1. `https://<backend-host>/api/v1/health` → `{"status": "ok", "db": true, ...}`.
2. Log in as an admin → **Status** page: Odoo connection "Live", trigger **Sync now** on products, then stock/sales/incoming (first sales run does the 24-month backfill — give it time).
3. Confirm every `write_*` / `*_live` **feature flag is OFF**, and `ODOO_WRITES_ENABLED=false` until you deliberately re-canary each write operation **from this deployment** (Status page → canary), then enable.
4. Time machine → **Backfill history** to reconstruct past stock from Odoo's move ledger.
5. Post a welcome notice from the inbox (bell icon) so testers see something on first login.
6. Set a backup cron somewhere independent: `pg_dump "$DATABASE_URL" | gzip > shopops-$(date +%F).sql.gz` (Supabase has its own backups; this covers the "export we control" requirement).

## Gotchas

- **Don't skip `VITE_API_BASE`** — the symptom is the login page loading fine but every request 404ing against Vercel itself.
- **CORS**: `CORS_ORIGINS` is comma-separated, scheme included, no trailing slash. The most common mistake is deploying the frontend, getting its URL, then forgetting to update `CORS_ORIGINS` on the backend and redeploy.
- **Two pool sizes**: backend + worker each hold SQLAlchemy pools; Supabase free tier caps direct connections. Using the 6543 pooler string for both services avoids this.
- **Render free tier spins down after 15 min of inactivity** — the first request after sleep takes ~30s. Use the Starter plan to keep it warm; the backend health check helps Render detect it's up after a restart.
- **`APP_JWT_SECRET` must be identical on backend and worker** — they share sessions. If you use the render.yaml Blueprint and set `generateValue: true` on one service, manually copy that value to the other. Using an Env Group (see above) is simpler.
- **Playwright e2e must never target this deployment** once write flags are enabled (`e2e/global-setup.ts` refuses when any write flag is on — don't work around it).
- The worker needs **outbound** reach to Odoo/SMTP/IMAP only — never expose it inbound.
- **Render's Docker context** must be `.` (repo root), not `infra/docker/`. The Dockerfile copies `backend/`, `worker/`, `docs/`, `pyproject.toml`, and `uv.lock` from the root — it won't build with a narrower context.
