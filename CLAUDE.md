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

**Material Design 3, vibrant and editorial — fun, colorful, quirky at times, but always functional** (Noah's calls 2026-07-10, superseding the earlier Linear-quiet direction; see DECISIONS.md). M3 dynamic color built around brand orange **#f36f21** (primary — used boldly for key actions and active states, with deep-umber on-primary for contrast; identical in every theme). Light mode ships **three switchable palettes** (Noah's picks 2026-07-27) — **Charcoal Pop** (DEFAULT: hot-magenta secondary, electric-cyan tertiary, crisp lilac-white surfaces; its values live in the `@theme` block so `data-palette="pop"` needs no override), **Neem Tree** (olive-bark secondary, neem-leaf tertiary, parchment-to-desert-sand surfaces), and **Turmeric Root** (sunflower-gold secondary wearing DARK text — gold is a light hue, slate-violet tertiary, cool lavender surfaces) — picked on `/settings` or the admin "Themes" page (`/palette-lab`); the choice is `data-palette` on `<html>` + localStorage, applied pre-paint in `index.html`, which validates stored ids and falls back to pop (retired ids — sunset/indigo/forest — can't strand anyone). `palette-lab.css` + PaletteLabPage mirror the tokens.css blocks — change one, change both. Each palette also themes inverse-surface (snackbars). Dark mode is ONE global deep slate-indigo scheme shared by all palettes, automatic via `prefers-color-scheme` (no toggle state). Surfaces are never flat grey, and text fields sit on the derived `--color-field` (lighter than the container ladder) so forms stay readable in every palette. All roles live in `frontend/src/styles/tokens.css`; legacy color names alias to M3 roles there. M3 anatomy throughout: pill buttons with state layers, tonal containers, floating-label filled text fields, chips, switches, snackbars on inverse surface, an extended FAB for a page's one big action, navigation drawer with pill indicators on desktop and a bottom navigation bar on phones (roles with ≤5 destinations). Type is oversized and editorial: Fraunces variable (WONK on) at Display/Headline scales (`.display-xl/.display-l/.headline` — page titles use display-large) + Inter at calm sizes for everything operational. Motion is springy and alive — `--ease-spring` micro-interactions (bouncy hovers, elastic clicks), staggered `.stagger-children` entrances, floating empty-state blobs — always honoring `prefers-reduced-motion`, and never animating inside dense data tables. Quirk belongs in safe places — the brand flower, empty states, container colors — never in data tables or safety-critical UI. Dense data tables where operators live (warehouse, ordering) and simple, almost consumer-grade flows where volunteers live (city center order form should feel like a well-made checkout, usable on a phone). Dark mode optional, mobile-first for the orderer and floor roles, desktop-first for office/warehouse roles.

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
- **`stock.move` has NO `name` field on this v19 instance** (Odoo 17+ removed it). The move
  description field is now `description_picking` (optional). Move create-vals use
  `description_picking`, never `name` — writing `name` fails with "Invalid field 'name' on model
  'stock.move'" (verified live 2026-07-12, `create_internal_transfer` canary). Recorded fixtures
  had drifted and masked this; the contract check now asserts `description_picking`.
- App locations by `complete_name`: `III/Stock/BWHSE`, `III/Stock/III-FLOOR`, and the staging
  location — which production RENAMED ~2026-07-17 to `III/Stock/III-FLORR-STAGING` (the FLORR
  typo is theirs, live id 2360; before that `III/Stock/III-FLOOR-STAGING`, verified 2026-07-10;
  the space spelling survives only in old fixtures). `ODOO_LOCATION_NAMES` maps ALL spellings —
  when the stock sync fails with "locations not found", check for another rename FIRST. BWHSE stores stock in
  hundreds of bin sub-locations (`III/Stock/BWHSE/A/1/1/1`), so quants MUST be matched by
  subtree (`child_of` + path-prefix classification in `app/sync/stock.py`), never by exact
  location id. Floor has a `Vending Machine` child (counts as floor). Also live:
  `III/CityCenter/<City>` per-center internal locations (54+ — the transfer target for
  city-center flows in later phases) and `III/Stock/SHIP` (live id 1234) — the
  online-fulfillment stock area, which FOLDS INTO bwhse totals (see the 2026-08-04 fix
  below): `ODOO_FOLDED_LOCATION_NAMES` in models/snapshots.py, quant classification only,
  never the canonical bwhse OdooLocation row (transfer drafts must keep sourcing from the
  real BWHSE).
- Foundation modules to build on (never around): `app/odoo/writer.py` (all writes; add new
  operations to `OPERATION_FLAGS` + a typed method), `app/odoo/simulator.py` (extend RELATIONS /
  ONE2MANY registries for new query shapes), `app/sync/runner.py` (new sync domains register in
  `SYNCERS`), `app/centers/importer.py` (roster re-import is idempotent).
- Phase 2 modules (same rule): `app/transfers/flow.py` (state machine requested →
  working_on_it → sent → counting → done; new transitions go in TRANSITIONS, never inline) +
  `app/transfers/service.py` (the Odoo side: placement draft rendered AT request creation —
  the picking name becomes the order's identity; sent-qty readback; `prepare_count_transfer`
  = copy picking STAGING→FLOOR + action_confirm + action_assign, own flag
  `write_prepare_count_transfer`; `poll_count_validation` listens for the human's barcode
  validation and reconciles counted-vs-sent into `adjustments`). The UI polls every ~4s
  (food-POS style); the list/detail GETs are the listener. **Order lists are CATALOGS, not
  orders**: no quantities; admin grants lists→zones (`order_list_zones`), coordinators grant
  zone lists→their centers (`order_list_centers`) — the phase-3 order form reads a center's
  granted lists. `app/restock/engine.py` (ILscripts accumulator; thresholds in Settings
  `restock_*`; `products.restock_exclude` keeps non-retail POS items — the Meals category,
  CX900-FLOOR — off every restock list, admin-togglable in the catalog drawer). Centers map to
  Odoo `III/CityCenter/<City>` locations by leaf-name match, REBUILT every stock sync
  (`centers.odoo_location_id`). Sales sync also fills `sales_daily` (UTC days,
  retention-pruned); restock folds it lazily on read, each calendar day exactly once
  (`restock_fold_state`). `reset_floor` = the "floor fully stocked" reset (clears every
  floor line, zeroes accumulators, `folded_through=today` so today gets amnesty and
  counting resumes with tomorrow's sales; who/when recorded on `restock_fold_state`,
  shown on the Restock page) — `POST /restock/floor/reset`, floor/warehouse roles.
  The back-stock ("From warehouse") tab has NO checkboxes — its one action is "New
  transfer from these items", which opens /transfer-requests/new prefilled via router
  state (suggested quantities; floor/admin only — warehouse can't create requests).
  `GET /transfer-requests/coming-soon` (declared BEFORE /{request_id} — route order
  matters) aggregates per-product qty on ACTIVE requests (sent qty preferred over
  requested) → the /coming-soon page in floor+warehouse navs.
- Floor OOS board (`app/oos/router.py`, /out-of-stock, floor role): Odoo's floor zeros
  (computed from StockLevel) ∪ manual `floor_oos_marks`. Marking with phantom floor stock
  calls `OdooWriter.create_inventory_reduction` — the THIRD write operation: draft picking
  on the "USA-III: Inventory Adj Reduction" type (`ODOO_REDUCTION_PICKING_TYPE`, name-ilike
  match; dest = the type's default destination — if the live type has none, the op fails
  with an actionable error), qty = floor qty at mark time, ILAPP-OOS- reference. "Back in
  stock" (`POST /oos/{id}/restock`) takes a counted qty and renders the reconciling draft:
  count higher than Odoo → `create_inventory_addition` on "USA-III: Inventory Adj  Adding
  Qty" (double-space live name — `ODOO_ADDITION_PICKING_TYPE` defaults to an ilike `%`
  wildcard, which the simulator honors; locations mirrored: type's default SOURCE → floor);
  lower → reduction; equal → nothing. Both share the writer's `_create_adjustment_draft`
  core. Flags `write_create_inventory_reduction` + `write_create_inventory_addition` ship
  OFF — canary each against the live types before enabling. Plain unmark (DELETE) removes
  the mark AND unlinks the still-draft picking. The api() client returns undefined for
  204/empty bodies (DELETEs) — don't "simplify" that away.
- Dev-auth mode skips the 60s resend throttle (nothing is delivered; e2e re-logs demo users
  within seconds) — real delivery modes keep it. E2E runs with `workers: 1` and REQUIRES the
  `write_create_internal_transfer` flag OFF so order-list AND center-order approvals stay
  simulated.
- Phase 3 modules (same build-on-never-around rule): `app/center_orders/` — `flow.py`
  (pending → approved → shipped / rejected / cancelled; SHIPPED is service-only, polled via
  `picking_checked_at` like the count listener), `catalog.py` (a center's menu = its granted
  order lists ∪ `dept_orderable` products for departments-zone centers; availability/OOS
  timeline from StockLevel + IncomingMove — "expected back mid-August" labels),
  `reasonability.py` (pure rules vs the center's own approved-order history + optional
  Anthropic polish that can only escalate; advisory, never blocking), `service.py` (approval →
  the existing `create_internal_transfer` op with `dest_odoo_location_id=center.odoo_location_id
  or 0` — 0 not None, so unmapped FIELD centers get the actionable writer error; all-untracked
  or locationless dept orders take the honest `picking_status="none"` path — that's the
  department water flow, not a failure). `app/notify/` — `transport.py` (`WhatsAppTransport`
  protocol; bridge contract `POST {url}/send {to,text}` / `GET {url}/status → {connected}`;
  SMTP fallback), `service.py` (outbox enqueued in-transaction, inline best-effort delivery
  post-commit, worker sweep w/ 2^n-minute backoff; gate ladder NOTIFY_ENABLED →
  `notify_whatsapp_live`/`notify_email_live` flags → configured; gated sends recorded as
  SIMULATED and logged on the order timeline as `notify` events). The worker probes bridge
  health into `notify_channel_state` (surfaced in admin `/status` under `notifications`).
  Center-orders list/detail GETs are the shipped-listener; UI polls ~4s. Migration
  `b9a08e5413de` (additive only). The seed gives the demo coordinator a role in AUSTIN's zone
  (wherever the roster puts Austin) — don't "simplify" that away; coordinator pings and e2e
  approvals depend on it.
- Phase 4 modules (same build-on-never-around rule): `app/ordering/` — `engine.py` +
  `forecasting.py` are PURE and parity-locked (`tests/test_workbook_parity.py` reproduces all
  281 numeric SEA rows of `docs/reference/USA INV CHK.xlsx`, committed; forecast = per-month
  MOH multipliers on the same projection, so flat forecast ≡ workbook EXACTLY — never "fix"
  the max(0, oh − demand + incoming) recurrence or the ceil_to_case epsilon). Rules =
  `rules.py` defaults merged with the `ordering_rules` AppSetting (`app_settings` table is the
  generic admin-editable JSON store; `merged()` validates by ignoring). Import candidates =
  active+stock-tracked+odoo products, not clothing, not `ordering_exclude`, India-ref
  `^[A-Za-z]{2}\d{10}$` (or india vendor) and NOT vendor-assigned; on-hand sums
  bwhse+floor+staging; sales from `sales_monthly` SPARSE (only selling months — velocity per
  in-stock month, workbook semantics), current month excluded. The review table IS the draft
  order (`suggestion_json` frozen per line, overrides move `final_*` and log qty_change
  events); placement stores CSV+XLSX as OrderAttachments, creates sea/air legs, emails via
  gate ladder NOTIFY_ENABLED → `ordering_email_live` (ships OFF) → SMTP; recipients live in
  the `ordering_email` AppSetting, NOT env. Post-place state moves ONLY via
  `timeline.apply_event` (new kinds: TRANSITIONS-style — add to OrderEventKind + apply_event,
  never inline). `parser.py`: LLM extraction w/ verbatim-quote enforcement, heuristic
  fallback (dev/test path — e2e and the acceptance email depend on it); proposals are
  human-confirmed, `product_hint` is matching scaffolding stripped before apply. Mailbox
  ingest (`mailbox.py`, worker `_poll_mailbox`) is READ-ONLY IMAP, last-UID in the
  `ordering_mailbox_state` AppSetting, matches by In-Reply-To then ILAPP-PO- token, ignores
  everything else; blank IMAP_HOST = no-op (paste replies via POST
  /ordering/orders/{id}/ingest-email). Domestic vendors: products get `vendor_id`+`moq`;
  suggestions run the same engine (`is_domestic` → MOQ-when-below-4 rule); `US-` fixture
  codes are the domestic demo pool. ForecastAnalogy: one per product, LLM/heuristic SUGGESTS,
  human confirms; graduates at ≥6 real months (checked at draft generation). Frontend:
  /purchasing (+ /purchasing/vendors, /purchasing/:id) — draft = review table (flag-chip
  filters, sparklines, qty inputs PATCH on blur), placed = timeline w/ proposal cards +
  4s-poll on `/timeline`; downloads go through `apiDownload` (bearer in header, never URLs).
  Migration `b8539cb5b38a` (additive; two NOT NULL product columns carry server_default for
  deployed rows). E2e phase4.spec.ts needs `ordering_email_live` OFF (shipped state).
  **The write flags went LIVE on the shared stack 2026-07-20 17:38** (canaried + enabled by a
  human): transfer/approval flows now render REAL draft pickings in production Odoo. The
  Playwright suite therefore REFUSES to start when any `write_*`/`*_live` flag is enabled
  (`e2e/global-setup.ts`) — never bypass that guard against this stack.
- Phase 4.x UX rework (2026-07-16): UI-ONLY rebrand — "Order lists"→"Catalogs",
  "Catalog"→"All SKUs" (backend names/tables/API paths unchanged; don't rename them).
  `app/catalog/matching.py` = the ANY-spreadsheet→products matcher (sku → barcode(8-14
  digits) → exact name → unique containment → unique token-subset; short numbers never
  match, ambiguity = unmatched, never guess). POST /order-lists/import creates a catalog
  from a file (report: matched/skipped/unmatched — skipped carries the eligibility
  reason). India generation scope = the `india_product_list` AppSetting (original file
  b64 inside for byte-identical download; PUT/GET/DELETE /ordering/product-list; when
  present `import_candidates(restrict_skus=…)` treats the list as authoritative, pattern
  check skipped). /purchasing is TABBED: India (product-list strip + import orders +
  draft FAB) | Domestic (Quick order per vendor → `send:true` creates AND places in one
  step — the plain "Dear {contact}, we kindly request… reply with an invoice" email, no
  sea/air language anywhere domestic). Vendor rosters: products get vendor_id via
  /ordering/vendors/{id}/products (POST upserts moq; one vendor per product, 409
  otherwise); VendorsPage = contacts + ProductPicker roster mgmt, ordering lives on the
  Domestic tab.
- Phase 5 modules (same build-on-never-around rule): **sales channels split at sync**
  (`app/sync/sales.py`): every pos.order is classified by its pos.config name (verified
  live 2026-07-21 — 53 configs: 'III Floor' + one per city center + campus one-offs) into
  `shoppe` / `city_center` / `campus_other`; online stays `online`; center matching is
  normalized-name vs the centers table with the `sales_channel_aliases` AppSetting
  ({config name: channel}) as the admin escape hatch, and unmatched configs land in
  campus_other honestly (per-config map in sync_state.extra.pos_config_channels). Rows
  written pre-split keep channel `pos` (displayed as Shoppe-legacy) and amounts NULL until
  an admin runs POST /admin/sync/sales/rebuild (declared BEFORE /sync/{domain}; clears the
  backfill marker → one deliberate heavy re-pull). Line revenue is captured tax-in
  (pos `price_subtotal_incl`, online `price_total`) into `amount` columns; the dashboard
  estimates NULL-amount rows at units × current retail and reports `estimated_share` —
  never silently. RESTOCK now counts `SHOPPE_CHANNELS` = (shoppe, pos-legacy) only —
  center/campus POS sales no longer inflate floor restock. `sales_center_monthly` =
  config-level center rollup (units+amount, no product dim) feeding the centers panel +
  Q&A. **Stock history**: every stock sync appends the day's totals to `stock_snapshots`
  (+`stock_snapshot_days` coverage markers — absent product on a covered day = genuinely
  zero; zero rows aren't stored), same-day re-runs replace, retention
  `stock_snapshot_retention_days` (730). The seed synthesizes ~90 days with a deliberate
  2-day gap. **Time machine** (`app/timemachine/`): past = nearest covered day ≤ target
  w/ gap-honest confidence; TODAY = live StockLevel; future (≤ rules.horizon months) =
  `snapshots_for_products` + `_project_moh` run in UNITS (scale-free — the parity test
  `test_future_view_matches_engine_projection` locks it to the engine, never re-derive).
  **Availability** (`app/availability/`): org OOS (scope org|bwhse|floor; excludes
  restock_exclude noise; `last_in_stock_on` from history) + Coming Soon (pending
  IncomingMove, soonest-first, `within_days`) reusing center_orders.catalog labels; digest
  = DigestSubscription rows (one email covers a subscriber's kinds, daily/Mon-weekly,
  hour gate `availability_digest_hour_utc`, last_sent_on stamps even simulated sends) →
  `notify.enqueue_email` (generic email-only outbox row; kind-level flag
  `availability_digest_live` ships OFF → rows pre-marked SIMULATED at enqueue, channel
  ladder still applies when on) → worker `_run_digests` every 10 min. **Bot API**
  (`/api/v1/bot/*`): X-API-Key == SKUBOT_API_KEY (blank = 503, compare_digest), read-only
  oos/coming-soon/health for skubot (phase 6 grows it — keep it read-only). **Reports**
  (`app/reporting/`): monthly aggregates only (`queries.resolve_period` presets; the
  (year,month) packed BETWEEN filter), breakdown dims category|product|channel|center;
  narrative + Q&A follow the inline-anthropic pattern (json-schema output, heuristic
  fallback when no key, source labeled model-id-or-'heuristic', cache per (period,
  facts-hash) in the `reports_narrative_cache` AppSetting). Frontend: /reports (admin),
  /time-machine (admin+warehouse), /availability (admin+warehouse+floor); chart series
  colors are the `--chart-1..4` tokens in tokens.css — validated (dataviz six checks)
  light+dark, fixed identity order shoppe/online/city_center/campus_other, and the
  chart's Table toggle is the contrast-relief channel: keep it. `app/ingestion/sources.py`
  = Amazon/Canada STUBS (interfaces + registry only, surfaced on /admin/status) — don't
  build them, extend `ExternalSalesSource` when the day comes. Migration `d1a7c9f42e10`
  (additive). E2E phase5.spec.ts is read-only except a digest subscription it removes;
  the digest test user is floor@ BECAUSE warehouse@'s weekly subscription is seeded demo
  data.
- Phase 5.x feedback round (2026-07-24, same build-on rule): **order/customer metrics**
  — the sales sync's parent-order fetch also captures `partner_id` + `amount_total`
  (~96% of POS orders and 100% of online carry a partner, verified live 2026-07-23) into
  `sales_orders_monthly` (orders, header amount, loyalty split per y/m/channel) +
  `customer_first_seen` (partner_id×channel → earliest order date, append-only min —
  the memory that keeps new-vs-returning stable across incremental windows; ONLY partner
  ids, never contact details). Reports: `SCOPE_CHANNELS` tabs (all | in_person =
  shoppe+pos-legacy+campus_other | online | city_center) thread through
  overview/breakdown; `orders_summary` returns AOV/orders/new-customers (period-exact via
  first_seen) + returning share of the latest COMPLETE month (never a half-month);
  distinct-customer counts are per-month only — the rollup has no partner dim to dedupe
  across months, don't pretend otherwise. Frontend /reports: scope tabs, CategoryBars /
  TrendLine / CustomersBars in chartBits (single-hue = the scope's channel token; new-vs-
  returning is emphasis form: returning wears the hue, new wears gray). **Time machine
  past on live**: bounds open `timemachine_min_past_days` (90) back even with zero
  history; `app/timemachine/backfill.py` reconstructs WEEKLY history from Odoo's own move
  ledger (product.product `qty_available` under a `to_date`+`location` context — verified
  live 2026-07-23; REAL data, not a guess) — admin queues it (POST
  /admin/time-machine/backfill or the TM-page button), the worker processes ONE date per
  loop pass, days get `stock_snapshot_days.source='reconstructed'` and the past view says
  so (never confidence "high"); live-captured days are never overwritten. The simulator
  computes qty_available from its quant table (any as-of date serves current state —
  documented). **Warp**: shell/warpFx.tsx + warpWorker.ts = the 4th-wall shockwave. v4
  latency chain, each stage covering the next: (1) compositor-only pop+rings (WAAPI
  transform/opacity ONLY — width/height animation relayouts and hitches) spawn
  SYNCHRONOUSLY inside the nav click's capture listener, BEFORE the router mounts the
  1,277-row page; (2) the GPU wave — raw WebGL, no Three/Pixi, one fullscreen-triangle
  refraction shader — renders in a WORKER on an OffscreenCanvas, immune to main-thread
  blocks, fed by a viewport snapshot PRE-CAPTURED during idle with html2canvas-pro
  (nav-link hover, settled TM renders, scroll/resize invalidate; stock html2canvas chokes
  on Tailwind v4's oklch/color-mix) whose ImageBitmap is pre-decoded at capture so the
  fire-path postMessage is synchronous too; (3) the wave's duration is ADAPTIVE (shared timeline in
  shell/warpWave.ts, unit-tested): expand ~1.3s → HOLD as a shimmering front while the
  destination renders → release+fade only after TimeMachinePage calls settleWarp()
  post-paint (double-rAF on settled view.data), capped at ~5s so it can never hang — the
  animation always outlives the loading, and the page beneath never moved. Amplitude/
  fringe/rim live in warpWave.WAVE + the FRAG shaders (kept in lockstep in warpFx AND
  warpWorker). Slider commits fire on pointerup (the 250ms debounce only smooths
  key/wheel streams — waiting after a deliberate gesture read as lag) but slider/date
  jumps NO LONGER WARP — Noah cut them 07-24 (broke scrubbing); the fireWarp +
  requestWarpCapture call sites are COMMENTED in TimeMachinePage for easy re-enable.
  ENTRY is the only warp; settleWarp stays wired (it releases the entry wave).
  Flip handling: bitmaps bake imageOrientation:"flipY" (worker sets UNPACK_FLIP_Y for
  unflipped fallbacks; main-thread fallback path flips at upload) — miss it and the page
  renders mirrored. History: SVG feDisplacementMap (v1/2) was CPU-bound; main-thread-rAF
  WebGL (v3) froze during mounts — don't go back to either. Ladder: no OffscreenCanvas →
  main-thread GL; no snapshot/WebGL → rings only; reduced-motion → nothing. Failsafes
  always un-stick the overlay; `window.__warpFx` = DEV console handle (HMR can split it
  from the mounted component — hard-reload before trusting probes; probes must read
  synchronously after click(), timers are blocked by the very mount they'd measure). Era type: `.tm-era-past`/`
  .tm-era-future` switch the WHOLE panel (Special Elite typewriter past / Orbitron future,
  imported via @fontsource in main.tsx — install in the frontend container too, node_modules
  is a volume). **Floor OOS board**: computed zeros are floor-RELEVANT products (floor row ∪
  shoppe sales last 30d ∪ floor snapshot history last 60d, minus restock_exclude) with
  floor qty ≤ 0 OR NO row — Odoo vacuums zero quants, so the old row-required query showed
  20 items while ~294 were actually out. StatusPage sales card gained "Rebuild history…"
  (the one deliberate heavy re-pull; fills channels+amounts+order metrics for pre-split
  months — run once after deploying this round). Migration `f3c8e21b7a54` (additive).
  Deploy note: backend/worker are BUILT images (no source mount) — `docker compose build
  backend worker && up -d` to land backend changes; the frontend container mounts source
  (HMR).
- Phase 5.y pre-deploy refinements (2026-07-25, same build-on rule): **Availability page
  + email digest are GONE** — digest model/worker-loop/endpoints/flag deleted, table
  dropped (migration `a4e9d27c81b3`; the availability router keeps only /oos,
  /coming-soon, /meta{freshness} for the merged UI + bot). /out-of-stock now carries the
  scope chips (Floor = the actionable mark board; Everywhere/Warehouse = read-only
  `availability/oos?scope=` lists; roles land on their own scope) and warehouse /incoming
  is the REAL inbound-shipments list (was the last stub page). **Blacklist**:
  `products.blacklisted` + `not_blacklisted()` (twin of `not_clothing()`) filters every
  user-facing list/report/flow — catalog default (param `blacklisted=true` = the
  manager's view), restock engine, OOS board incl. marks, center-order menus, ordering
  candidates + upload by_sku pools, matcher, time machine, reports `_grouped`; header
  metrics (AOV/orders) have no product dim and can't exclude. **/settings** (all roles,
  top-bar gear replaced the palette menu): palette picker for everyone; admins get the
  blacklist manager + Styleguide/Palette-lab links (both left the nav, routes admin-only;
  TimeMachinePage's category filter moved to /products/facets). **Notices inbox**
  (`app/notices/`, bell in both top bars): admin posts, per-user read rows, read-all on
  open — deliberately NOT the notify outbox. **floor_rotating** role = shoppe_floor minus
  create/edit-lines on transfer requests (router keeps those SHOPPE_FLOOR-only;
  transfers/flow.py FLOOR_ROLES covers transitions; in SEE_EVERYTHING_ROLES; UI entry
  points already keyed off shoppe_floor). Audit log: nav→Status-page button. WhatsApp ON
  HOLD (code intact; status page says "on hold", email carries notifications).
  Purchasing: header icon is a real gear (Icons.gear — the old inline SVG was a sun), the
  product-list strip lives INSIDE the settings dialog, domestic Quick-order hides
  Cover/Suggested below sm (no page scroll at 375px). Centers table: follow-up column
  hideBelow sm + max-w-56 badge wrap (the 181px badge column was the mobile overflow); a
  gold dot on the name carries the signal on phones. `app.seeds.clear_demo` (dry-run
  default / --apply) wiped 15,053 demo-flow rows on the shared stack 07-25 — keeps users,
  products, synced history, flags, audit; rotating@demo user added via admin API.
  VITE_API_BASE now points the client at a remote API (Vercel guide:
  docs/DEPLOY_VERCEL_SUPABASE.md + frontend/vercel.json — backend/worker CANNOT run on
  Vercel, they need an always-on host). e2e phase5.spec is now fully read-only.
  Follow-ups (2026-07-26): **blacklist sweep** `POST /products/blacklist/sweep`
  (declared BEFORE /{product_id}; preview `apply:false` → confirm) = never-stocked
  AND never-SOLD active odoo items (no snapshot qty>0 ever + nothing on hand + no
  sales_monthly rows; IL-Service hard exception, manual source exempt) ∪
  "USA"-in-name / "-USA"-sku duplicates (ilike prefilter + case-sensitive Python
  check so SQLite==Postgres). The never-SOLD half is LOAD-BEARING: snapshots are
  weekly/~6mo deep, so fast movers and digital items trade without stock history —
  the first sweep (stock-only) blacklisted 1,308 selling items (sarees/kurtas!)
  that were restored by hand 07-26; don't loosen it again. Live state: ~960
  blacklisted, ~2,950 active visible. **Clothing scope change (Noah 07-26,
  supersedes the brief's blanket exclusion): catalogs + center order menus ALLOW
  clothing** — hand-curated menus decide; `not_clothing()` and the clothing 422s
  are GONE from orders/router + center_orders/catalog, and the catalog editor's
  ProductPicker lost `excludeClothing`. Clothing stays excluded ONLY from
  purchasing (ordering/inputs candidates, analogy pools, VendorsPage roster
  picker) — test_clothing_is_curatable_but_never_purchasable locks both sides. **House partners**: POS registers put a HOUSE
  partner on walk-in orders ('… - III FLOOR POS' = ~99% of shoppe; LA's account rode
  ~50–130 campus orders/mo) — `app/sync/sales.py` detects them three ways (channel
  dominance ≥50 orders & ≥30%; per-config dominance, same thresholds; monthly volume
  ≥25 orders/partner/month), remembers pairs in sync_state.extra['house_partners'],
  scrubs them from customer_first_seen, and drops them from with_customer/distinct/
  new/returning while their ORDERS still count. Detectors are pure + threshold-
  injectable (tests lower them); a full rebuild re-ran 07-26 → in-person ≈ 0 known
  customers (true), online is the loyalty signal, known-share 96%→24%. "New" = first
  order per (partner, channel) within the 24-month window — cross-channel debuts and
  window-edge returns read as new; don't "fix" without a partner-level identity
  decision. **Order size** on /reports is a display NUMBER (period AOV + prior),
  chart removed. **ProductPicker + blacklist search keep results open after a pick**
  (multi-add; picked rows disable) — don't reinstate clear-on-pick.
- Two-way transfer sync (2026-07-27, same build-on rule): **INBOUND** — new sync
  domain `transfers` (SYNCERS + SYNC_DOMAINS + `sync_transfers_minutes`=10 + worker
  STAGGER; runs LAST in run_all because it needs the staging location the stock
  sync maps): `app/sync/transfers.py` discovers stock.pickings with
  location_dest_id child_of staging in (draft, waiting, confirmed, assigned) —
  "drafted as going to staging" counts — snapshots lines into
  `staging_inbound_moves` (replace-on-sync; validated/cancelled pickings drop out;
  app-placed pickings excluded by TransferRequest.odoo_picking_id). /coming-soon
  unions them per product (`odoo_pickings` refs, dashed chips + "Odoo · state"
  badge on the page). Simulator RELATIONS gained ("stock.picking",
  "location_dest_id") for the child_of. **OUTBOUND** —
  `transfers/service.poll_outbound_status` (throttled by new
  `transfer_requests.picking_checked_at`, same odoo_count_poll_seconds): warehouse
  actions IN ODOO on app-placed pickings drive the workflow — confirmed/assigned →
  working_on_it, done → sent (qty readback) → count transfer prepared → counting,
  cancel → cancelled; hooked into the list GET (≤8 polls/refresh, both listeners)
  and the detail GET, events note "… in Odoo — synced". Migration `e7b1f5a9c2d4`
  (additive). Test fixtures ship 2 native pickings (WH/INT/NATIVE1 assigned +
  a done twin) — writer/canary tests count pickings RELATIVE to that baseline,
  never == 0.
- Staging2 pallet flow (2026-07-27, the warehouse's REAL process): transfers get
  retargeted to **III/Staging2** (live id 2030, a TOP-LEVEL sibling of III/Stock —
  `staging2` LocationKey, OPTIONAL_LOCATION_KEYS so old fixture sets don't break the
  stock sync), accumulate, then ONE pallet goes to floor staging.
  `poll_outbound_status` therefore checks WHERE a validated picking went: dest ==
  staging → old path (sent → count prepared → counting); anything else (staging2) →
  SENT only, "waiting for the pallet", count deferred. `app/transfers/pallet.py`:
  `staging2_snapshot` (LIVE quant read — action screen; snapshot fallback when Odoo
  down), `create_pallet` (ONE draft via the existing create_internal_transfer op,
  source staging2 → dest staging, ILAPP-PLT- reference, lines frozen on
  `pallet_transfers`), `poll_pallets` (validation listener: pallet done → every
  SENT request w/ count none/failed gets its count prepared → counting; hooked into
  the transfers list GET + the staging2 GET). Endpoints: GET
  /transfer-requests/staging2 (PARTICIPANTS; declared before /{id}) + POST
  …/staging2/send-all (WAREHOUSE only). /staging2 page in the warehouse nav (floor
  may view; only warehouse sees the button). staging2 counts in org OOS scope +
  ordering/timemachine on-hand sums (NOT in bwhse scope — it's committed to the
  floor). Discovery sync now watches BOTH staging destinations and skips ILAPP-
  origin pickings (app pallets would double-count the SENT requests riding them).
  Migration `c9d4e8b2f7a1`. Fixtures: III/Staging2 + 2 staging2 quants (tests) and
  the location in generate.py — expected_stock carries the staging2 rows.
- Pre-deploy tweaks (2026-07-27, same build-on rule): **Product search is tokenized**
  everywhere — `app/catalog/search.py` (SQL `product_search_clause` for /products +
  `matches_search` for availability/timemachine/bot in-memory filters) and its frontend
  twin `frontend/src/search.ts` (draft review, coming-soon, OOS, place-order client
  filters). Semantics: alphanumeric tokens, match = any ONE field contains ALL tokens
  ("yoga mat" ↔ "Yoga-Mat-Cotton-Brown"); tokens never mix across fields (a "00" token
  must not match every barcode) — change one twin, change all three. **Sourcing from
  Odoo product tags**: tags named exactly "Domestic"/"India" (case-insensitive;
  `product.tag` + `all_product_tag_ids` verified live 2026-07-27, contract-checked) sync
  into `products.sourcing` ('' | domestic | india, domestic wins conflicts). In
  `import_candidates`: domestic = hard exclude (outranks the uploaded product list, like
  the domestic-vendor rule), india = candidate regardless of reference shape; untagged =
  the old pattern/vendor rules exactly. Sourcing shows in the catalog drawer ("Odoo tag"
  badge) and ProductOut. Migration `d8f2a6c31b90` (additive). CSV/XLSX export buttons on
  purchase orders carry `Icons.download` (nav.tsx icon set). **OOS lists hide
  never-stocked items by default** (`oos_items(include_never_stocked=)`, scope-aware:
  no snapshot ever showed stock in scope) — on live that's 1,271 of 1,652 org rows,
  and 1,240 of them SELL (612 clothing, 299 digital), so they are HIDDEN not
  blacklisted (Noah's call after seeing the numbers — the sweep's never-sold guard
  stands); "Include never-stocked" chip on /out-of-stock is the peek, bot API stays
  curated. **Themes**: light palettes are now Charcoal Pop (DEFAULT — its values live
  in the @theme block; `data-palette="pop"` needs no override), Neem Tree (parchment/
  desert-sand, olive-bark secondary, neem-leaf tertiary) and Turmeric Root (lavender-
  slate, sunflower-gold secondary wearing DARK text — gold is a light hue, on-secondary
  #402d00). Sunset/Indigo/Forest are gone; index.html validates stored ids and falls
  back to pop, so stale localStorage can't strand anyone. Each palette now also themes
  inverse-surface (snackbars). palette-lab.css + PaletteLabPage mirror tokens.css —
  change one, change both. Dark stays the ONE global scheme. **OOS lists hide
  never-stocked items by default** (`oos_items(include_never_stocked=)`, scope-aware:
  no snapshot ever showed stock in scope) — on live that's 1,271 of 1,652 org rows,
  and 1,240 of them SELL (612 clothing, 299 digital), so they are HIDDEN not
  blacklisted (Noah's call after seeing the numbers — the sweep's never-sold guard
  stands); "Include never-stocked" chip on /out-of-stock is the peek, bot API stays
  curated. **Themes**: light palettes are now Charcoal Pop (DEFAULT — its values live
  in the @theme block; `data-palette="pop"` needs no override), Neem Tree (parchment/
  desert-sand, olive-bark secondary, neem-leaf tertiary) and Turmeric Root (lavender-
  slate, sunflower-gold secondary wearing DARK text — gold is a light hue, on-secondary
  #402d00). Sunset/Indigo/Forest are gone; index.html validates stored ids and falls
  back to pop, so stale localStorage can't strand anyone. Each palette now also themes
  inverse-surface (snackbars). palette-lab.css + PaletteLabPage mirror tokens.css —
  change one, change both. Dark stays the ONE global scheme.
- Feedback round (2026-08-02, same build-on rule): **Purchasing table sort** —
  `orderingBits` gained `SortState/toggledSort/sortBy` + `SortableTh` (mirrors
  DataTable's asc⇄desc arrow + aria-sort); DraftReview sorts the FULL filtered set
  BEFORE pagination (sort in `sorted` memo, never the page slice) and QuickOrder
  sorts its items — both hand-rolled tables, the orders list already had DataTable
  sort. **Sell-through indicator** (an admin asked for a visible signal):
  `SellThroughChip` ("✓ sell-through basis", hover = SELL_THROUGH_HELP) sits on the
  draft filter rail + Quick order; SALES_MO_HELP on the Sales/mo header explains
  main-number-vs-base (forecast mean vs flat sell-through average — units ÷ in-stock
  months; identical under 6 useable months, ⚠ at >30% divergence); LineDrawer
  MiniStats carry help tooltips. **Drawer availability graph**: GET
  /products/{id}/stock-history?days=90|180|365 (catalog/router — covered
  StockSnapshotDay with no product rows = genuine ZERO point, emitted not skipped;
  last point = live StockLevel, source sync|reconstructed|live; first_covered =
  global min; untracked/manual → empty, not error). ProductDrawer renders it under
  On hand: time-scaled single-series line (--chart-1), zero baseline = OOS, line
  BREAKS on >21-day capture gaps, per-point hover tooltip w/ bucket split,
  "View as table" relief, range chips 3mo/6mo/1yr, window-scoped coverage footnote;
  staging2 card appears only when nonzero. **Tags compacted** to one wrap of toggle
  chips + inline expires date (admin), read-only tag badges row for everyone else.
  **Silly mode** (`frontend/src/silly.ts`): localStorage `ilops_silly` +
  useSyncExternalStore; EXACT-match dictionary SILLY_LABELS renames nav labels,
  AppShell/PageHeader titles and Sign out (Purchasing→"Get the goods", Users→
  "Peeps", Reports/Sales→🤑🤑🤑, Restock→"The re-up", Vendors→"The plugs", Order
  history→"Receipts 🧾"…) — dynamic strings pass through untouched; toggle lives in
  Settings→Appearance; silly.test.ts FAILS if any role's nav label lacks an entry,
  so new nav items need a silly name too. Round 2 widened it to the sanctioned
  quirk zones: EmptyState maps title+string-hint THROUGH the component (all empty
  states app-wide — "Crickets 🦗", "Local scene's quiet"…), PageHeader maps string
  subtitles, plus brand ("Shop Ops"→"Da Shop"), HEALTHY-only health-chip labels
  ("Vibin' with Odoo" — "Sync stale"/"Odoo auth failing!" have NO entries, keep it
  that way), Inbox ("The goss 📬"), 7 search placeholders (aria-labels stay
  canonical), and Settings/Purchasing section headings ("The drip", "Speed run",
  "Local hauls"). Failure text, confirm buttons, and table data never get entries.
  **Reasonability → "Order Notes"** (Noah 2026-08-02): UI-ONLY rename of the
  advisory assessment's one visible label (OrderDetailPage heading; e2e text
  assertion updated) — backend module/API fields/testids keep `reasonability`,
  and it's a REAL rename, not a silly-mode entry. Browser-pane note: computer-tool coords =
  screenshot px (= rendered image ÷ 2 at 1280×1400); ref-clicks are unreliable on
  small controls — probe with a JS click listener when clicks seem to miss.
- Notes round (2026-08-03, same build-on rule): **Dept-orderable toggle left the
  product drawer** (API/patch field stays; the flag is now managed via API or bulk
  edit only). **sales_daily.returned_units** (migration `b4e7d19c3f82`, nullable —
  NULL = pre-capture row, unknown ≠ zero): the sales sync splits POS refund lines
  (qty<0) into the new column while `units` stays NET (restock semantics
  untouched); the daily window rebuilds each sync so recent rows self-fill.
  **Time machine day sales**: `timemachine/_day_sales` joins SalesDaily for the
  REQUESTED date (past+today; retention-window honest, "so far today" partial
  note; totals count products missing from the stock table too); items gain
  sold_qty/returned_qty; view gains day_sales; the table went SLIM (identity =
  name + productCode, Category column dropped — search covers it, Staging folded
  into Total's title) + a Sold/Returned summary strip. **Barcode everywhere**
  (floor identity; inspect panel + purchasing keep real SKUs/India refs):
  `productCode(barcode, sku)` now renders on transfer create/detail lines,
  restock rows + its transfer prefill, incoming, adjustments, catalog-editor
  lines, center-order detail lines + place-order rows/search; barcode added to
  the six Out models that lacked it (transfers LineOut + AdjustmentOut, restock
  Floor/BackItemOut, orders LineOut, center_orders LineOut + CatalogItemOut).
  **Case size in ordering flows**: PickedLine carries case_size (toPicked),
  picker results + transfer lines show "case of N" (center-order rows already
  did). **Multi-select + context menus**: design/ContextMenu.tsx =
  `useRowSelection` (plain=single/toggle-off, cmd=toggle, shift=range over the
  CALLER'S visible order; forContext keeps multi on right-click) + ContextMenu +
  isInteractiveTarget; DataTable grew (row,e) clicks, onRowContextMenu,
  rowClassName. Wired: transfer create (set-qty…/remove), place-order rows
  (add/set-qty/remove), transfer detail ("New transfer with N items" → /new
  prefill, floor+admin), PO draft review (set sea/air qty…, remove = zero both;
  plain click still inspects — modifier clicks select), All SKUs (bar +
  "Edit N together…"). **BulkProductDrawer** (ProductDrawer.tsx) = Premiere-style
  bulk edit: only TOUCHED fields apply — tag chips stage add/remove-to-all
  (expires add blocked, per-item dates; air/sea exclusivity enforced), case size
  blank=leave, TriPick for restock-exclude + blacklist; per-item PATCH/PUT,
  failures counted honestly. SetQtyDialog lives in OpsBits. **Enter-to-add flow**
  (transfer create + place order): Enter in search adds the TOP result (case-size
  default qty on center orders) and focuses its qty input (value pre-selected;
  new-mount callback ref on transfers, qtyEls Map + rAF on place-order), Enter in
  qty returns focus to search with text selected — type/enter/qty/enter loops.
  QtyInput gained inputRef/onEnter + select-on-focus; ProductPicker gained
  inputRef + Enter-pick (onPick's optional viaEnter arg). e2e phase2/3 selectors
  (aria-labels, testids) deliberately untouched. Migration `b4e7d19c3f82`.
- Mobile/PWA round (2026-08-04, same build-on rule): **Page-state persistence**
  — `src/persist.ts` `usePersistedState(key, initial)` = useState mirrored to
  sessionStorage (per-tab/app-session on purpose: fresh launch starts clean;
  key CHANGE re-seeds via render-time derived-state, setter identity stable)
  + `clearPersisted(...keys)` for submit flows (imperative — a setState write
  effect can miss when navigation unmounts). Wired: catalog search/category/
  tag/sort, place-order CART per center (`order.cart.{centerId}`) + notes +
  search/category (duplicate-prefill overrides stored; placing clears), new-
  transfer draft lines+notes (`transfer.new.*`, prefill replaces, submit
  clears), PO review filters per order (`po.{id}.*`), TM category/search, OOS
  scope/search/includeNever, restock tab, transfers filter, coming-soon
  search, reports period/scope/dim, purchasing tab + per-kind status, users/
  centers filters. Selections/menus stay ephemeral; list pages reset to page 1
  by design. **PWA (add-to-home-screen)**: public/manifest.webmanifest
  (standalone, portrait, icons 192/512 + 512-maskable generated from
  il-mark.png via `uv run --with pillow`, bg #fbfafd) + apple-touch-icon-180
  + apple-mobile-web-app metas + theme-color light/dark (#fbfafd/#131523) in
  index.html; viewport gained `viewport-fit=cover` and the mobile top bar
  wears `pt-[max(0.75rem,env(safe-area-inset-top))]` (BottomNav already had
  the bottom inset). **iOS focus-zoom fix**: `@media (pointer: coarse)` in
  tokens.css puts 16px on `.m3-control, input, select, textarea` — Safari
  zooms any focused control under 16px (the A2HS test round caught it); no
  maximum-scale hack, pinch-zoom stays. **Transfer tap flow**: tapping a
  picker result now opens SetQtyDialog ("How many — {name}?", case-size
  default, number pad, Enter applies, "Add to request") then returns focus
  to search — the phone-in-the-aisle loop; the Enter keyboard flow is
  unchanged (viaEnter branch). SetQtyDialog gained title/help(null hides)/
  applyLabel overrides. No service worker yet (offline semantics vs auth +
  polling deliberately punted).
- SHIP-folds-into-bwhse fix (2026-08-04, same build-on rule): warehouse quantities
  were understated because `III/Stock/SHIP` (the online-fulfillment stock area,
  live id 1234 — **772 products / ~80k units**, incl. both sesame oils at ~2.1k
  and 2.4k with ZERO under BWHSE) was outside the four synced roots. Noah's call:
  SHIP counts as warehouse. `ODOO_FOLDED_LOCATION_NAMES` (models/snapshots.py) =
  complete_name → key it folds into; the stock sync fetches/classifies folded
  subtrees like roots but they NEVER become the key's canonical OdooLocation row
  (writer._resolve_location feeds transfer drafts — bwhse must stay the real
  BWHSE; the fold test locks this). Missing folded locations don't fail the sync;
  they land in sync_state.extra `missing_folded_locations` (a SHIP rename =
  ~80k units silently gone, so it must surface) and found ones in
  `folded_locations`. The time-machine backfill folds them too (resolves by name
  live — no OdooLocation row — and ACCUMULATES per (product, key), one extra
  qty_available read per day). Downstream (ordering on-hand, OOS scopes, restock
  back-stock, TM today, drawer graph) inherits via StockLevel/snapshots — no
  other code changed. Fixtures: SHIP location + 2 quants (one folds onto existing
  bwhse stock, one SHIP-only like live sesame oil); generate.py seeds SHIP quants
  from its OWN rng stream so the seeded demo data elsewhere is untouched. Stock
  snapshot rows before 2026-08-04 predate the fold — bwhse history jumps that
  day, honestly. Still deliberately UNTRACKED: 190 stray units sitting directly
  at the `III/Stock` root (put-away hygiene, not app scope), and Castor/Neem-Oil
  6L SHIP counts (31k/20k) look physically implausible — Odoo-side data to audit,
  not app bugs.
- Security remediation (2026-08-05, same build-on rule — full rationale in
  DECISIONS.md): **config FAILS CLOSED.** `Settings._refuse_insecure_production`
  raises `InsecureConfig` when `ENV` is outside `DEV_ENVS` (dev/test/local) and
  auth isn't supabase, or `APP_JWT_SECRET` is empty/published/<32 chars, or
  `CORS_ORIGINS` has `*`. `app_jwt_secret` has NO default (the old
  `dev-only-change-me` was a public key); dev fills a blank/published one with a
  random per-process value, so dev sessions end on restart — that's intended.
  `test_config_security.py` is the CONTROL for this whole class: the finding
  reached a committed blueprint because nothing failed when the config was
  wrong. **`settings.dev_auth` (dev ENV *and* dev mode) — never `auth_mode`
  alone — gates anything that leaks a login code.** Auth responses are UNIFORM
  for known/unknown/inactive identifiers (the 404 was an enumeration oracle);
  `tests/util.login()` still works because dev mode still returns the code for a
  real user. **Google OAuth is the production sign-in** (`SUPABASE_OAUTH_PROVIDERS`
  =google, `SUPABASE_OTP_ENABLED`=false); `/auth/config` advertises
  `oauth_providers`+`otp_enabled`, LoginPage renders a button per provider and
  finishes the redirect via `getSession()` → the UNCHANGED `/auth/exchange`.
  **`match_supabase_claims_to_user` links auth_uid ONLY on a provider-VERIFIED
  identifier** — checks `email_verified`/`phone_verified` across top-level →
  `app_metadata` → `user_metadata` (first hit wins; the first two are
  Supabase-controlled, `user_metadata` is client-writable), missing/unparseable =
  unverified, and an unverified identifier that WOULD have matched raises 403
  instead of linking. Don't loosen that: it's the account-takeover path, and it
  lives in the mode that fixing dev-auth moves you to. **Sessions revoke** via
  `users.token_epoch` (migration `c1f7a4d90b52`) in the token + compared in
  `get_current_user`; bumped by `POST /auth/logout-everywhere`, role change, and
  deactivation. **Output encoding is the other half**: `ordering/export.py`
  `_safe_cell` neutralizes formula-leading TEXT cells only (numbers keep native
  types — these files are emailed to Coimbatore, and openpyxl turns a leading `=`
  into a real `<f>` cell), and `app/downloads.py` is the ONE door for every file
  response (CR/LF stripped, RFC 6266 `filename*`, content-type allowlist — an
  inbound email attachment filename carries RFC 2231 escapes and needs no app
  account). `app/ratelimit.py` is deliberately in-process (one uvicorn process):
  authed limits key on user id, unauthed on IP *and* identifier, and the
  entrypoint sets `--proxy-headers --forwarded-allow-ips` (never `*`) or the IP
  key is just the tunnel. `RATE_LIMIT_ENABLED=false` in conftest — the suite
  calls some endpoints in loops; `test_auth_hardening.py` turns it on
  deliberately. Also: `counted_qty` bounded + `allow_inf_nan=False` and the
  writer's qty guards are `math.isfinite` (NaN passed `qty <= 0`); list/limit
  ceilings (`?limit=-1` emitted `LIMIT -1`); CSP with the pre-paint palette
  script moved to `public/palette.js` (inline would need a per-edit hash — keep
  it in lockstep with tokens.css); `/api/docs` + detailed `/health` gated behind
  `is_dev_env`/auth (anonymous callers could read `writes_enabled`); the
  coordinator roster workbook left git AND the image for `./private/` (treat as
  already disclosed — rotate the Stripe terminal registrations).
- Floor-flow feedback round (2026-08-11, same build-on rule): **the transfer
  draft is now a SHARED store** — `frontend/src/transferDraft.ts` (external
  store + `useDraftLines`, same `transfer.new.lines` sessionStorage key, so
  drafts in flight survived the change). NewTransferRequestPage reads/writes it
  instead of owning private page state; `clearDraft()` replaces the
  clearPersisted pair on submit. Two consumers depend on it: the restock floor
  list's **"Request more"** (swipe RIGHT, or right-click on a desk — swipes are
  touch-only by design) appends the row at its bring-out qty via `addToDraft`,
  which MERGES quantities on a product already in the draft (the API rejects
  duplicate products on one request) and deliberately leaves the row on today's
  list; and `shell/TransferDraftBubble.tsx`, the floating pill that follows you
  between pages (hidden on /transfer-requests/new, dismissable for the session
  without touching the draft, phone position clears the snackbar row). Swipe
  LEFT is unchanged (snooze, with its exit animation) — only the committing
  direction gained a branch. `restock` FloorItemOut gained `bwhse_qty` (the row
  still SHOWS floor-only; the number is there so the draft quotes an honest
  warehouse figure — test_restock asserts it now, where it used to assert its
  absence). **Transfer board right-click**: DataTable `onRowContextMenu` →
  Duplicate (fetches the detail via `useFetchTransferRequest`, then the usual
  /new prefill — which REPLACES the open draft, same as every other prefill)
  and Cancel request (confirm dialog, offered only for requested/working_on_it
  — past "sent" the stock has moved). Copy: /coming-soon subtitle is Noah's
  wording; the /out-of-stock subtitle is COMMENTED OUT in place at his request
  (2026-08-11) — leave it parked rather than deleting it. **Bubble round 2**
  (same day, Noah's follow-up): the pill is DRAGGABLE (position clamped to the
  viewport, saved per device in localStorage `ilops_transfer_bubble_pos`; a
  gesture under 5px is a tap = open, anything more is a move) and it NEVER
  hides — the dismiss × is gone, because "x it out once and it's gone forever"
  was the complaint; the way to move it aside is to drag it, the way to end it
  is to place the request. Entrance/exit are the bouncy `--animate-bubble-in` /
  `--animate-bubble-out` keyframes in tokens.css, and the entrance class is
  DROPPED on animationend (fill-mode `both` would outrank the hover scale
  forever). Placement REFUSES a zero viewport (`viewport()` + rAF retry): a
  hidden tab reports innerWidth 0, and clamping against it parks the pill in
  the top-left corner permanently — caught live in the browser pane, which
  really does report 0×0 while backgrounded.
- **Time machine PAGE removed (2026-08-11, Noah's call) — endpoints kept.**
  `/time-machine`, `/time-machine/bounds` and `/admin/time-machine/backfill`
  are untouched and verified live after the removal (bounds + today view,
  1,301 items). What went is the UI: TimeMachinePage.tsx, its route + both nav
  entries (admin, warehouse), the `useTimeMachine*`/`useStartHistoryBackfill`
  hooks, the TimeMachine* frontend types, the silly-mode "The rewind" entry,
  the era font imports in main.tsx, and the phase5 e2e test. **The warp
  shockwave (shell/warpFx.tsx + warpWorker.ts + warpWave.ts, the `.tm-*` CSS,
  warpWave.test.ts) is INTACT but no longer mounted** — it existed for that one
  destination; leave it parked for the next 4th-wall moment rather than
  re-deriving it (SVG displacement and main-thread rAF WebGL both failed on the
  way to it — see the phase-5.x notes above). @fontsource/special-elite and
  orbitron stay in package.json (unused now; node_modules is a container
  volume, so dropping them is a rebuild, not an edit). The page is recoverable
  from git history if it ever comes back.
- Transfers rework + swipe-to-add everywhere (2026-08-12, same build-on rule):
  **`design/SwipeRow.tsx` is now the ONE swipe gesture** — `useSwipeRow`
  ({onLeft, onRight, leftExits}) + `SwipeBackdrop` + `leavingStyle` +
  `useAddedBounce`, extracted verbatim from the restock row (dxRef-not-state
  commit test, axis lock, click swallow, exit-then-mutate — the comments
  explain why; don't re-derive them). TOUCH ONLY by design; a mouse gets the
  row's context menu instead. Wired: restock floor rows (left = snooze w/ exit,
  right = request more), **All SKUs phone list** (new `md:hidden` card list
  mirroring restock — the DataTable is now `hidden md:block` desk furniture,
  with "Add to transfer" in its right-click menu, multi-select aware), and the
  **floor OOS board** (swipe right / right-click → add to transfer). Catalog
  adds default to case size and refuse untracked/manual products (the API
  would too). **Transfers page** = `TransfersPage.tsx`, pill tabs like restock:
  New transfer (default) | Past transfers, URL-driven
  (/transfer-requests/new · /past; the bare path opens New) because the bubble
  keys its burst off the path. NewTransferRequestPage exports
  `NewTransferPanel`, TransferRequestsPage exports `PastTransfersPanel` —
  both lost their PageHeader/Fab to the shell. Roles that can't create
  (warehouse, floor_rotating) get the board with NO tabs; the warehouse's
  own /transfers route renders the same page. Nav + Protected titles are
  "Transfers" now (one silly entry, "Big moves"). **Bubble round 3**: momentum
  — pointer velocity is smoothed per move, released as a friction coast
  (`Math.pow(0.9, dt/16)`, dt clamped to 32ms so a backgrounded tab can't
  teleport it), edges kill that axis rather than bouncing, position saves at
  rest. Motion vocabulary in tokens.css: `bubble-in` (liquid squash/stretch),
  `bubble-bump` (an item joined — fires 150ms AFTER the row's own
  `added-bounce`, so the eye follows the item over), `bubble-burst` + 8
  `burst-spark` particles when you reach the New-transfer tab, and the panel
  answers with `animate-rise-in` (`burstedRecently()`). Two live-caught traps:
  measure the pill with **offsetWidth/Height, never getBoundingClientRect** (a
  running scale animation makes the rect a third of the real size and parks it
  off-screen), and clear the entrance class on a **timer, not animationend** (a
  backgrounded tab never delivers the event, and the class then outranks every
  later motion). setPointerCapture/release are wrapped in try/catch — a throw
  there swallows the tap.
- Slingshot round + morph refinement (2026-08-12, same build-on rule):
  **`shell/flyToBubble.ts` is the add-to-transfer feedback — the toasts are
  GONE** (restock, catalog, OOS). Two halves. (1) THE MORPH, in
  `design/SwipeRow.tsx` (`morphOnRight`): past `MORPH_START` (0.8 of the row's
  OWN width, measured on pointerdown) the row itself collapses toward a
  `MORPH_BALL_PX` (30) ball, driven continuously by the swipe — scale from
  `transform-origin: 0% 50%` so the ball forms under the finger, travel capped
  at `w - 38` so it can't slide out of the list, `border-radius` in PERCENT
  (a px radius on a non-uniformly scaled box is a squircle, not a circle), a
  `<MorphBall>` overlay fading the row orange, and `SwipeBackdrop` fading OUT
  by the same fraction. Release still commits at the old 96px threshold; a
  morphed release hands the flight its exact ball box and suppresses the
  spring-back (`snap`), so the hand-off is invisible. (2) THE FLIGHT:
  `buildFlightFrames` is PURE and unit-tested (flyToBubble.test.ts — starts at
  the action box, winds up AWAY from the target, arcs above the straight line,
  lands dead on the anchor, no NaN at zero distance). Keyframes are sampled
  with the easing baked into the SAMPLE SPACING and the animation runs
  `linear`; that's what makes the release read as a slingshot. The bump fires
  at `hitOffset` — the frame where the ball is within 10px of the pill — NOT
  at the end of the timeline, because a decelerating tail covers two pixels in
  its last 60ms and waiting for it read as lag. Data first, always:
  `addToDraft` runs on release; the flight only decides WHEN the "+N"
  (`count-pop`) and the bump show. The bubble publishes its centre via
  `setBubbleAnchor` on every place/drag/coast, and `withAnchor` waits on a
  TIMER (never rAF — a hidden tab pauses frames and the "+N" would never
  land; `document.hidden` skips the flight outright and just announces).
  Row-bounce/`useAddedBounce` are deleted — the ball leaving the row IS the
  acknowledgement — and bubble-in/bump were cut from six wobbling keyframes to
  one clean overshoot (the old ones read as jitter).
  **Verifying animation in the browser pane**: it reports `document.hidden`
  true and freezes the document timeline, so nothing animates and React
  batches state so mid-gesture DOM reads look empty. Spoof `document.hidden`,
  read AFTER a timeout, and scrub `animation.currentTime` by hand, cloning the
  node at each mark, to see the path. Stale HMR modules duplicate the anchor
  store — hard-reload before trusting a "the ball never spawned" result.
  **Catalog**: phones show the search alone with a "Filter by… (n)" disclosure
  over the rest (`catalog.filtersOpen`; the badge counts non-default filters),
  and a new **"Hide OOS"** checkbox → `in_stock_only` on GET /products (sum of
  StockLevel across ALL locations > 0; no rows at all counts as OUT, since
  Odoo vacuums zero quants — test_catalog locks that).
