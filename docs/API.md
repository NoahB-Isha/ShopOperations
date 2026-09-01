# Shop Ops API — integrator's guide

The backend that powers the Shop Ops web app is a plain REST API, and every
feature the app has is available through it. This guide is for anyone building
a tool on top of it. It pairs with the machine-readable contract:

| What | Where |
|---|---|
| **OpenAPI spec (committed copy)** | [`docs/api/openapi.json`](api/openapi.json) — regenerate with `make openapi` |
| **OpenAPI spec (live)** | `{BASE}/api/openapi.json` |
| **Interactive docs (Swagger UI)** | `{BASE}/api/docs` — try requests in the browser |
| **Health check** | `{BASE}/api/v1/health` (no auth) — liveness only: `{"status","db"}` |
| **Detailed status** | `{BASE}/api/v1/health/detail` (any signed-in user) — Odoo mode, sync freshness, write posture |

`{BASE}` today:

- **Production (Render):** `https://shopops-backend.onrender.com`
  — free-tier instance: it sleeps when idle, so the first request after a
  quiet spell takes ~30–60 s to answer. Design your client to tolerate that
  (generous timeout + one retry).
- **Local dev:** `http://localhost:8000` (`make dev` brings the stack up;
  the frontend proxies to it on :5173).

Every application route lives under **`/api/v1`**. The API evolves
additively — new fields and endpoints appear, existing ones don't change
meaning or disappear without a deliberate (and loud) migration.

---

## Authentication

There are two ways in. Pick by what you're building:

### 1. Machine key (read-only) — the right choice for most tools

A single header, no login flow, no expiry, but **read-only** and limited to
the curated bot surface (`/api/v1/bot/*`). This is how skubot (the WhatsApp
lookup bot) connects.

```bash
curl -H "X-API-Key: $KEY" https://shopops-backend.onrender.com/api/v1/bot/health
# {"ok": true, "time": "2026-08-03T21:12:00+00:00"}
```

| Endpoint | What it returns |
|---|---|
| `GET /api/v1/bot/health` | connectivity + key check |
| `GET /api/v1/bot/oos?scope=org\|bwhse\|floor&category=&q=` | out-of-stock items in scope |
| `GET /api/v1/bot/coming-soon?within_days=&category=&q=` | inbound stock (transfers + Odoo pickings) |

Responses share one envelope: `{generated_at, snapshot_freshness, count,
items}` — `snapshot_freshness` tells you how old the underlying Odoo snapshot
is; surface it to your users rather than implying the numbers are live.

The key is the `SKUBOT_API_KEY` environment value on the backend (ask the
admin for it, or for a deployment with a second key). If it's unset the whole
surface answers `503`. **This surface stays read-only by design** — if your
tool needs more read endpoints, extending `backend/app/availability/bot.py`
is the sanctioned path; anything that *changes* state must go through a user
session instead.

### 2. User session (full API, role-scoped)

Exactly what the web app uses: a bearer token (JWT, ~30-day sessions),
obtained with a one-time code. What you can see and do is scoped by the
user's roles (admin, warehouse, shoppe_floor, floor_rotating,
zone_coordinator, center_orderer, dept_liaison, dept_orderer) — an
integration acting as a coordinator sees that coordinator's world, no more.

`GET /api/v1/auth/config` tells you which login mode the server runs, and which
sign-in methods it offers:

```json
{ "mode": "supabase", "oauth_providers": ["google"], "otp_enabled": false, … }
```

- **`dev` mode** (local stacks only — refused outside a development `ENV`): the
  OTP is returned in the response, so no delivery is needed.

  ```bash
  # 1. request a code
  curl -X POST {BASE}/api/v1/auth/request-code \
    -H 'Content-Type: application/json' \
    -d '{"identifier": "you@example.org"}'
  # → {"sent": true, "channel": "email", "dev_code": "123456"}

  # 2. trade it for a session token
  curl -X POST {BASE}/api/v1/auth/verify \
    -H 'Content-Type: application/json' \
    -d '{"identifier": "you@example.org", "code": "123456"}'
  # → {"token": "eyJ…", "user": {…}}

  # 3. use it
  curl -H "Authorization: Bearer eyJ…" {BASE}/api/v1/auth/me
  ```

  Note the obvious caveat: in dev mode *anyone who can reach the server can
  log in as anyone*. That is why the server now **refuses to start** in dev auth
  unless `ENV` is a development value (`dev`/`test`/`local`), and why the
  `dev_code` field can only ever populate under that same condition. It exists
  for demos and local work; don't build lasting integrations against it.

- **`supabase` mode** (real production auth): sign-in is **Google OAuth** by
  default (`oauth_providers: ["google"]`), with the email/SMS one-time-code form
  available only when `otp_enabled` is true. Either way the browser ends up with
  a Supabase access token and swaps it for an app session via
  `POST /api/v1/auth/exchange {"supabase_token": "…"}`. The `auth/config`
  response carries the Supabase URL and anon key you need.

  For a **non-browser** client, OAuth is a poor fit (it needs a redirect). Two
  better options: ask an admin for a `SKUBOT_API_KEY`-style machine key if
  read-only bot endpoints cover your need, or have an admin enable
  `SUPABASE_OTP_ENABLED` and sign in against Supabase directly
  (`POST {SUPABASE_URL}/auth/v1/otp`, then `/verify`) before calling
  `/auth/exchange`.

  One rule worth knowing if you build on `/auth/exchange`: the app links a
  Supabase identity to an app account **only on an identifier the provider says
  it verified**. A token carrying an unconfirmed email that would otherwise
  match an account is rejected with 403 rather than linked — that is deliberate,
  and it is why Google (which verifies emails) is the default provider.

Accounts are **invite-only** — there is no self-registration endpoint. Ask an
admin to create a user (with the roles your tool needs) on the Users page.

`POST /api/v1/auth/logout-everywhere` retires every session for the calling
user at once (the "I lost my phone" button). Sessions are also retired
automatically when an admin changes your roles or deactivates you, so a token
that still looks unexpired can legitimately start returning 401.

---

## Conventions

- **JSON everywhere.** Timestamps are ISO-8601 UTC; dates are `YYYY-MM-DD`.
- **Errors** are `{"detail": "human-readable message"}` with conventional
  status codes: 401 (bad/expired token), 403 (role not allowed), 404, 409
  (conflicts, e.g. duplicate SKU), 422 (validation — sometimes FastAPI's
  field-level array instead), 502 (an Odoo write failed), 503 (subsystem not
  configured).
- **Pagination** (list endpoints that page, e.g. `/products`):
  `?page=1&page_size=50` → `{items, total, page, page_size}`. Everything else
  returns plain arrays sized for their real-world cardinality.
- **Snapshot, not live.** All inventory/sales data comes from the app's own
  Odoo snapshot, refreshed on a schedule (stock every ~4 h, sales hourly).
  Endpoints that depend on freshness say so (`snapshot_freshness`, `meta`,
  staleness flags on `/health`). Don't treat any number as a live Odoo read.
- **Writes create Odoo *drafts*, and only behind feature flags.** Nothing the
  API does validates stock moves in Odoo — a human reviews every draft there.
  When a write's flag is off, the operation is *simulated*: the response's
  status fields (`picking_status`, `placement.status`, …) honestly say
  `simulated` instead of `created`. Always read those fields back rather than
  assuming a side effect happened. Records the app creates carry an
  `ILAPP-…` reference and a deep link (`…_url`) into Odoo.
- **Polling is how state moves.** Several flows advance when the *list or
  detail GET is hit* (they double as listeners for Odoo-side changes —
  transfer validation, order shipping). The web UI polls those every ~4 s;
  an external tool should poll no faster than it genuinely needs (every
  30–60 s is plenty) — especially against the free-tier deployment.
- **No hard rate limit** is enforced today. Be a polite client anyway; reads
  are cheap (local Postgres) but the box is small.

---

## Endpoint map

The spec is the authority — this is the orientation layer. Prefixes are under
`/api/v1`; "admin", "warehouse", etc. name the roles that can call them
(admin passes every gate).

| Prefix | What lives there | Mainly for |
|---|---|---|
| `/auth` | config, request-code/verify (dev), exchange (supabase), me | everyone |
| `/products` | catalog search/browse, facets, per-product **stock history** (`/{id}/stock-history`), tags & flags (PATCH/PUT, admin), blacklist sweep | all roles read, admin writes |
| `/centers`, `/zones` | city-center + zone roster, follow-ups, re-import | admin |
| `/order-lists` | the curated **catalogs** (menus) + grants to zones/centers, spreadsheet import | admin, coordinators |
| `/center-orders` | the city-center order flow: context, catalog (menu), reasonability preview, place → approve/reject → shipped; timeline events | orderers, coordinators |
| `/transfer-requests` | BWHSE→Floor requests: create, state machine (ack/sent/prepare-count/done/cancel), **`/coming-soon`**, **`/staging2`** + pallet send | floor, warehouse |
| `/restock` | floor + back-stock restock lists, check-offs, floor reset | floor, warehouse |
| `/availability` | org/bwhse/floor OOS + coming-soon lists, snapshot meta | admin, warehouse, floor |
| `/counts` | inventory counting: submissions, review queue, approve/reject/recount | floor, warehouse, wranglers |
| `/ordering` | purchasing: import candidates → draft POs (the review table), line overrides, place (exports + email), timeline/proposals, vendors + vendor quick orders, forecast analogies, India product list | admin |
| `/reports` | monthly sales overview, breakdowns, narrative, Q&A | admin |
| `/notices` | the in-app inbox (bell) | all roles |
| `/admin` | users & invites, system status, sync triggers, feature flags, canaries, audit log | admin |
| `/bot` | the key-authenticated read-only surface (above) | machine clients |

Two structural notes worth knowing before you build:

- **"Catalogs" in the UI are `order-lists` in the API** (a deliberate
  UI-only rename); likewise the "Order Notes" panel is the `reasonability`
  field. API names are the stable ones.
- **Identifiers:** products carry `global_sku` (stable app-wide key),
  `barcode` (what the floor uses), and `odoo_internal_ref` (India reference).
  Match on `global_sku` or `product_id` in API calls; display barcodes to
  humans.

---

## Regenerating the machine-readable spec

```bash
make openapi        # rewrites docs/api/openapi.json from the FastAPI app
```

CI-friendly alternative: `cd backend && uv run python scripts/export_openapi.py [output-path]`.
The live `{BASE}/api/openapi.json` always reflects the deployed version;
import either into Postman/Insomnia/openapi-generator to get a client for
free.
