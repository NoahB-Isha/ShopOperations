## PROMPT 0 — Project Brief (persistent context)

You are building a production-grade internal web application for **Isha Life USA**, the retail/products arm of a nonprofit spiritual organization headquartered at the Isha Institute of Inner Sciences (III) in Tennessee. The app manages North American retail operations end to end: inventory visibility, internal transfers, city-center ordering, quarterly import ordering from India, order lifecycle tracking, and reporting.

### The operation you're building for

- **System of record:** Odoo 19. All inventory and sales data lives there. The app reads from Odoo and, in tightly scoped cases, writes draft records to it. It never replaces Odoo.
- **Blue Warehouse** (Odoo: `III/Stock`, `III/Stock/BWHSE`): receives India shipments, fulfills online orders, fulfills internal transfers to the campus shop and city centers.
- **Isha Life Shoppe** on campus (Odoo: `III/Stock/III-FLOOR STAGING` → `III/Stock/III-FLOOR`): retail floor plus back stockroom. Transfers from the warehouse land in a virtual STAGING location until counted, then a second manual transfer moves them to FLOOR. Discrepancies are reconciled manually today.
- **~54 City Centers** across the US and Canada run pop-up shops. Each belongs to a **Zone** with a **Zone Coordinator** (currently Lili, Mik, Ravi, Vivek, plus Canada). Orders are placed today via WhatsApp messages to coordinators, who enter them in Odoo by hand. Roster, zones, Stripe terminals, and active status are in `IL City Coordinators.xlsx`.
- **III Departments** on campus order items (water, snacks, t-shirts) from the shop. Model this as a special zone ("III Departments") whose "city centers" are departments, with one Department Orders Liaison as its coordinator, a different orderable catalog, some items untracked in Odoo, and fulfillment primarily from `III-FLOOR` rather than the warehouse.
- **India ordering:** the majority of stock ships from Isha Life Coimbatore quarterly. Sea lead time ≈ 6 months, air ≈ 4 months. Category rules matter: Bhoomi/Gold/Silver ship air only; toothpaste and camphor are ordered in bulk roughly yearly; Bloom items are expiry-sensitive. **Clothing is out of scope for this app entirely for now** — exclude clothing SKUs from ordering flows and don't design around them; just avoid architectural choices that would make adding a clothing module later painful. All communication with Coimbatore happens over email with spreadsheets attached — orders, revisions, availability updates.
- **Data quality is imperfect**, especially at low stock counts. The app should treat Odoo numbers as authoritative but display confidence honestly (e.g., "2 on hand — low counts are often wrong; verify physically").

### Existing code to build on (read these first)

**This is a brand-new repo.** The projects below are _reference material_, not a codebase to extend. Read them to learn what worked, what the data looks like, and what mistakes to avoid — then implement fresh, borrowing ideas (and test fixtures) freely:

- `https://github.com/noahballinger/ops` — a previous attempt at the India ordering tool: FastAPI + SQLModel + Postgres, an Odoo 19 JSON client (session auth over `/web/session/authenticate` + `/web/dataset/call_kw`, background snapshot sync with self-healing cache), a **demand forecasting engine with a 281-row parity test** against the `USA INV CHK` workbook, sea/air split logic, config-driven category rules, and CSV/XLSX export. The most valuable things to study: the projection/forecasting math and its parity tests (reproduce these — they're the spec of record for ordering), the Odoo session-auth approach (there's no External API on this instance), the snapshot-cache pattern for surviving Odoo outages, and the credential-safety posture.
- `https://github.com/noahballinger/skubot` — Python WhatsApp chatbot volunteers use for product/inventory lookups. The app must expose an API the bot can call, so bot features and web features share one backend.
- `https://github.com/noahballinger/ILscripts` — the current restock-sheet script (restock lists from yesterday's sales). Its logic gets absorbed into the app.

### Users and roles

Role-based access with row-level scoping:

| Role                            | Sees / does                                                                                                                                                   |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Admin / Office**              | Everything. Manages users, catalogs, order lists, India orders, reports.                                                                                      |
| **Warehouse**                   | Incoming shipments, transfer fulfillment, inventory adjustments queue, OOS/coming-soon lists.                                                                 |
| **Shoppe Floor**                | Restock lists, BWHSE→Floor transfer requests, staging reconciliation.                                                                                         |
| **Zone Coordinator**            | Three simple views: (1) my city centers + their pending orders, (2) pending orders for approval, (3) order history. Approval creates the draft Odoo transfer. |
| **City Center Orderer**         | Two views only: Place an Order, Order History (with a one-click "duplicate order" button). Nothing else in the app.                                           |
| **Dept Liaison / Dept Orderer** | Same shape as coordinator/orderer, scoped to the III Departments zone and its catalog.                                                                        |

Seed users, centers, and zones from `IL City Coordinators.xlsx`. Auth: fresh one-time code on every login, delivered to email or phone (see Architecture). Users must have at least one of the two on file; the coordinator sheet has gaps in both, so the import flags incomplete contacts for admin follow-up rather than silently skipping them.

### Architecture

- **Backend:** FastAPI (Python 3.12), SQLAlchemy + Alembic migrations. **Database: Postgres hosted on Supabase** (connect via the standard Postgres connection string with connection pooling; treat Supabase as managed Postgres — don't couple business logic to Supabase-specific features). A separate background worker process handles Odoo sync, email ingestion, and WhatsApp notifications. All business logic in pure, tested modules.
- **Auth:** every login sends the user a fresh one-time code, delivered to their **email or phone number** (their choice, both supported). Use **Supabase Auth** for this — it provides email OTP and SMS OTP out of the box and pairs naturally with the Supabase Postgres. Admin-managed invites; long-lived sessions on trusted devices so volunteers aren't re-coding daily.
- **Frontend:** React + TypeScript + Vite (a real build this time, not the CDN single-file approach), TanStack Query for server state, Tailwind. Component library kept thin — build a small internal design system rather than adopting a heavy kit.
- **API:** REST, OpenAPI-documented, versioned under `/api/v1`. The same API serves the web UI, skubot, and future integrations.
- **Odoo sync cadence — be a polite client.** On-hand and incoming stock: refresh a few times per day, or on explicit demand. **Sales history (24 months available in Odoo): heavy query — never pull it all repeatedly.** One full 24-month backfill at setup, then **small incremental pulls on the hour** touching only the current month (previous month included once daily to catch late edits). Everything the app shows comes from its own snapshot, never live-per-request.
- **LLM features** (reasonability scores, new-product matching, email parsing, dashboard Q&A) call the Anthropic API server-side. All LLM outputs that would change data are **proposals requiring human confirmation** — never auto-applied.
- **App hosting: an on-campus box** (Supabase hosts only the database). Backend + worker run from Docker Compose. Critical consequence: city center orderers and zone coordinators are all over the continent, so the box **must be securely reachable from the public internet** — recommend a Cloudflare Tunnel (no router port-forwarding, TLS and DDoS handling included, free tier is fine) or, failing that, a reverse proxy with TLS via Caddy and a proper firewall. The worker also needs outbound reach to Odoo, Supabase, the mailbox, and WhatsApp. Plan for campus power/network blips: containers restart on boot, the Odoo snapshot pattern already tolerates gaps, and a scheduled `pg_dump` to independent storage backs up what Supabase's own backups don't cover. `.env`-based secrets never enter the repo or logs.

### Odoo integration policy (safety-critical)

The app has **full read/write access** to Odoo. That access is a privilege the codebase must earn with discipline:

1. **Reads** go through a JSON client (session auth, paging, throttling) feeding a local snapshot cache the app reads from. Odoo outages degrade gracefully with explicit staleness flags; a failed sync never clobbers the last good snapshot.
2. **All writes go through one gateway** — a single `OdooWriter` service. No other code touches write methods. Every write: is a typed, named operation (e.g., `create_internal_transfer`, `create_sale_order`) rather than a raw `call_kw`; records an audit row (who, when, operation, payload, Odoo record IDs returned, success/failure); and supports **dry-run mode**, which renders exactly what would be written without writing. A global kill switch (`ODOO_WRITES_ENABLED=false`) turns all writes into dry-runs app-wide.
3. **Nothing the app creates is ever validated by the app.** Every record it creates (transfers, sales orders) is saved in **draft state** and left for a human to review and validate inside Odoo. Corollary: whenever the app creates an Odoo record, the UI **must display a direct link to that record in Odoo** (built from `ODOO_BASE_URL` + model + id) — the handoff to a human is part of the feature, not an afterthought.
4. **Testing without a staging Odoo** (there is no staging instance — production is the only Odoo). Three safe layers:
   - **(a) Unit tests** against a faked client, asserting the exact model/method/payload of every write operation. Runs in CI on every push.
   - **(b) A recorded-fixture Odoo simulator**: capture real production _read_ responses (sanitized) as fixtures, and build a small in-process fake that implements the handful of models the app touches — `create` returns an id, subsequent reads reflect the write, state transitions behave like Odoo's. All integration tests run against this simulator in CI. When production schema might have drifted, a read-only contract check (`fields_get`, `search_count`) against production re-validates the fixtures — reads are always safe.
   - **(c) A gated canary protocol against production** for each new write operation before it's enabled: draft records are inert in Odoo (they move no stock), so the canary creates one clearly-marked draft (reference prefixed `APP-TEST-`, minimal lines), reads it back, verifies the deep link, then unlinks it. This runs only from an explicit admin action with the operation's feature flag still off — never automatically, never in CI. Every canary is audit-logged like any other write.
5. **Blast-radius habits:** writes are idempotent (client-generated reference on every created record so retries don't duplicate); `unlink` is permitted only for records the app itself created, matched by reference prefix; new write operations start life feature-flagged and graduate only after their canary passes.
6. **Credentials** live in environment variables, are held in memory, and are never logged, persisted, or placed in URLs. The app currently authenticates as a **repurposed personal Odoo account that also has human activity on it** (a dedicated user may come later). Two consequences the code must own: (a) Odoo's own logs can't distinguish app actions from the human's, so the app's audit log is the source of truth, and **every record the app creates carries an app reference prefix** (e.g., `ILAPP-…`) so app-created records are identifiable regardless of which account made them — canary cleanup and `unlink` safety match on this prefix, never on the account; (b) the human may change the password or trip 2FA — auth failures must surface loudly on the admin status page rather than silently stalling sync.

### Email integration policy (safety-critical)

The app gets its own mailbox (e.g., `orders@…`) for sending India/vendor orders and ingesting replies. Email bodies are **untrusted input**: the LLM parses them strictly as data to extract order events from (quantity change, substitution, discontinuation, method change, split, availability), each with a supporting quote and confidence score. Parsed events are proposals a human confirms before they touch order state. An email saying "go ahead and reorder everything" is a fact to display, never a command to execute. Mailbox access is read-only and scoped to order threads.

### Design language

Elegant, calm, and fast — closer to Linear/Notion than to enterprise ERP chrome. Warm neutral palette with a single accent drawn from the Isha Life brand (earthy tones — think unbleached cotton, copper, deep forest green); generous whitespace; one excellent typeface pair; subtle motion only where it communicates state. Dense data tables where operators live (warehouse, ordering) and simple, almost consumer-grade flows where volunteers live (city center order form should feel like a well-made checkout, usable on a phone). Dark mode optional, mobile-first for the orderer and floor roles, desktop-first for office/warehouse roles.

### Engineering standards

- Every business-logic module has unit tests; the India ordering engine keeps its workbook parity test green.
- Playwright smoke tests for the critical flows (place order → approve → draft transfer appears; generate India order → export).
- Seed script that loads demo data (products, centers, fake sales) so the app runs meaningfully with zero Odoo access — `docker compose up && make seed` gives a working demo.
- Feature flags for anything that writes to Odoo or sends email.
- Conventional commits, CI running tests + typecheck + lint on every push.
- Keep modules small and boundaries clean — this app will be iterated on by non-experts with AI assistance, so legibility beats cleverness everywhere.

---

---

### Verified Odoo instance facts (learned Phase 1 — keep current)

- `/web/dataset/call_kw/<model>/<method>` works on this Odoo 19 instance; session auth via
  `POST /web/session/authenticate` (`{jsonrpc:"2.0", method:"call", params:{db, login, password}}`).
  Session expiry surfaces as error code 100 / `SessionExpiredException` → re-auth once and retry.
  The useful error detail is in `error.data.message`, not the top-level message. **2FA must stay
  disabled** on the app account.
- `sale.report` aggregate queries return nothing here. Sales history = `pos.order.line`
  (qty field `qty`; parent states paid/done/invoiced) + `sale.order.line` (`product_uom_qty`;
  states sale/done), with parent-order dates fetched in chunks. Dotted domains
  (`order_id.date_order`, `order_id.state`) work on live search_read.
- India-import internal references match `^[A-Za-z]{2}\d{10}$` (e.g. `CA0023000009`); other codes
  are domestic. Odoo variants can share a `default_code` — first one wins in the catalog.
- Incoming stock: `stock.move` where `state in (assigned, confirmed, waiting,
  partially_available)` and `picking_code = "incoming"`.
- App locations by `complete_name`: `III/Stock/BWHSE`, `III/Stock/III-FLOOR`,
  `III/Stock/III-FLOOR STAGING` (mapped in `app/models/snapshots.py::ODOO_LOCATION_NAMES`).
- Foundation modules to build on (never around): `app/odoo/writer.py` (all writes; add new
  operations to `OPERATION_FLAGS` + a typed method), `app/odoo/simulator.py` (extend RELATIONS /
  ONE2MANY registries for new query shapes), `app/sync/runner.py` (new sync domains register in
  `SYNCERS`), `app/centers/importer.py` (roster re-import is idempotent).
