# Isha Life Shop Ops

Internal operations app for Isha Life USA: inventory visibility, internal
transfers, city-center ordering, quarterly India ordering, and reporting —
reading from (and carefully, auditably writing drafts to) Odoo 19.

> **Repo visibility:** the coordinator roster (names, emails, phone numbers)
> lived in `docs/reference/` and is still in git history — keep the repo
> private. It now lives in `./private/` (gitignored, mounted read-only).

## Quick start (no credentials needed)

```bash
make dev    # full stack: Postgres, API, worker, web (Docker Compose)
make seed   # roster import + demo users + 1,200 fixture products + 24mo sales
```

Then open http://localhost:5173 and sign in as any demo user — auth runs in
dev mode, so the one-time code is shown right on the login screen:

| Role                         | Email                          |
| ---------------------------- | ------------------------------ |
| Admin                        | admin@demo.ishalife.test       |
| Warehouse Team               | warehouse@demo.ishalife.test   |
| Inventory Flow Manager       | floor@demo.ishalife.test       |
| Floor Team                   | rotating@demo.ishalife.test    |
| Order Reviewer               | coordinator@demo.ishalife.test |
| Order Requester              | orderer@demo.ishalife.test     |
| Order Reviewer (departments) | liaison@demo.ishalife.test     |
| Order Requester (department) | kitchen@demo.ishalife.test     |

With no `ODOO_*` credentials configured, the sync runs against a
deterministic **fixture simulator** — the whole app works offline, and every
write operation renders a dry-run instead of touching anything.

## Layout

```
backend/   FastAPI app: auth, catalog, centers, admin, the Odoo layer, seeds
worker/    background process running the Odoo snapshot syncs on cadence
frontend/  React + TS + Vite + Tailwind, thin internal design system
infra/     Docker Compose, Dockerfiles, Caddyfile
docs/      reference material (ops overview, workbooks) + HISTORY.md build log
```

Key backend modules (small on purpose — see `CLAUDE.md` for the full brief):

- `app/odoo/client.py` — session-auth JSON client (no External API on this
  instance); read-only unless explicitly constructed otherwise.
- `app/odoo/simulator.py` — fixture-backed in-process Odoo used by tests, CI,
  and credential-less demos. `app/odoo/fixtures/generate.py` builds the demo set.
- `app/odoo/writer.py` — **the single write gateway.** Typed operations, audit
  log, dry-run, kill switch (`ODOO_WRITES_ENABLED`), per-operation feature
  flags, idempotent references (`ILAPP-…`), draft-only records with deep links.
- `app/odoo/canary.py` — the gated create→verify→unlink canary an admin runs
  before a write operation's flag may be enabled.
- `app/sync/` — products / stock / sales / incoming snapshot syncs with the
  self-healing rule: a failed pull never clobbers the last good snapshot.
- `app/centers/importer.py` — the messy-roster importer (flags problems for
  follow-up instead of guessing).

## Commands

```bash
make dev / logs / down / nuke   # stack lifecycle (nuke deletes the DB volume)
make seed                       # idempotent demo seed
make test                       # backend pytest + frontend typecheck & vitest
make lint / typecheck / format
make e2e                        # Playwright smoke tests (stack must be up + seeded)
make fixtures                   # regenerate demo Odoo fixtures
make migrate                    # alembic upgrade head against DATABASE_URL
```

## Configuration

Copy `.env.example` to `.env` (done automatically by `make dev`) — every
variable is documented there. The important safety ones:

- `ODOO_WRITES_ENABLED=false` — global kill switch; false forces every write
  app-wide into a dry-run.
- Leaving `ODOO_BASE_URL/DB/LOGIN/PASSWORD` blank = fixture mode.
- `AUTH_MODE=dev|supabase` — dev issues codes locally (demo); supabase uses
  Supabase Auth (Google OAuth in production; email/SMS OTP available but off),
  exchanged for an app session.

## Odoo safety posture (read before touching the write path)

1. Reads go through a snapshot cache; the app never queries Odoo per-request.
2. **Every** write goes through `OdooWriter` — typed, audited, dry-runnable,
   kill-switched, feature-flagged, idempotent. No exceptions, ever.
3. Records the app creates are left in draft with an `ILAPP-` reference and a
   deep link for a human to review — with one deliberate, flag-gated exception:
   approved inventory counts post their own adjustments (see `CLAUDE.md`).
4. New write operations ship flag-off and graduate only after an admin runs
   their canary against production (Status page → Write canary).
5. `unlink` is allowed only for records whose reference proves the app
   created them.

Testing without a staging Odoo: unit tests assert exact write payloads,
integration tests run against the recorded-fixture simulator (CI-safe), and
`pytest -m odoo_live` / `python -m app.odoo.contract` re-validate the real
schema read-only when drift is suspected.

## Production notes

The stack currently runs hosted: backend on Render (`render.yaml` — free tier,
no worker), frontend on Vercel (`docs/DEPLOY_VERCEL_SUPABASE.md` +
`frontend/vercel.json`), database + auth on Supabase. The campus-box option
below remains available if hosting moves on-prem:

- `docker compose --profile prod` adds Caddy serving the built frontend and
  proxying `/api` — put a **Cloudflare Tunnel** in front; never port-forward.
- Database and auth live on Supabase; set `DATABASE_URL` to the pooled
  connection string and `AUTH_MODE=supabase`.
- All services `restart: unless-stopped`, so the stack survives power blips.
- Schedule a `pg_dump` to storage that is not the campus box.
