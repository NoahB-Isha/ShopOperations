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
- Roles: six user types, renamed (2026-08-13, Noah's call): **the stored role
  keys are UNCHANGED** — `admin`, `warehouse`, `shoppe_floor`,
  `floor_rotating`, `zone_coordinator`, `center_orderer` — only what people
  SEE moved, in the frontend's `ROLE_LABELS` (UsersPage): Admin · Warehouse
  Team · Inventory Flow Manager · Floor Team · Order Reviewer · Order
  Requester. Renaming the keys would rewrite every `require_roles`, test and
  seeded row for no user-visible gain; don't. **`dept_liaison` and
  `dept_orderer` are GONE** — a departments reviewer is an Order Reviewer
  whose review zone is III Departments, and a departments requester an Order
  Requester whose center is a department. That works because every
  departments-specific behaviour already keys off `zone.kind == departments`
  (center_orders/catalog.py + service.py), never off the role. Migration
  `a7c3e91d64b8` rewrites the role strings in place (dropping a duplicate row
  first, so uq_role_scope can't trip); its downgrade is a deliberate no-op —
  nothing records which reviewer used to be a liaison. The frontend gets the
  signal from **`RoleOut.zone_kind`** (new field) → `useAuth().isDepartments`,
  which drives the "department" vs "center" wording on MyCentersPage,
  PendingOrdersPage, the /my-centers nav label (`navForRoles(roles, {
  departments })`) and the top-bar title. **"Zone" is "Review zone" in all
  UI copy** — zone NAMES are untouched ("Zone 1 (Lili)", "III Departments"),
  as are the tables, columns, API fields and `zone_coordinator` itself.
- Floor Team asks + Suggested items (2026-08-13, same build-on rule): the
  Floor Team can't raise transfers, so they raise **asks** —
  `app/floor_requests/` (`FloorRequest`: product, qty, note, status
  open|picked_up|dismissed, who asked / who resolved; migration
  `b5f18c26d3a7`). POST is floor_rotating+shoppe_floor, resolving is
  shoppe_floor only, and **every ask is its own row with its own name** —
  two people flagging the same shelf are two entries (who noticed and how
  much each wanted is the information), resolvable one at a time; the
  Suggested items row says "N other people have asked for this" so adding
  both doesn't silently double the pull. Nothing here touches Odoo.
  **/request-items** (Floor Team) reuses the SHARED DRAFT and the floating
  bubble — same store, same gestures; the bubble just reads "Item request"
  and lands on /request-items instead of the transfer page (`canTransfer` vs
  `canAsk` in TransferDraftBubble). Below the picker they see their own asks
  and what became of them. **/suggested-items** (Inventory Flow Manager) is
  the other end: **"Floor Team Requests"** (chip: *asked for by the floor
  team*) ABOVE **"Database Suggestions"** (chip: *found by the app*) — people
  first, deliberately, since someone standing at an empty shelf knows what
  the numbers don't. Both sections feed the same draft, so a mixed pull is
  one transfer; taking an ask marks it picked_up so the asker sees it landed.
  The Suggested items page is deliberately BARE — headings + provenance chip,
  no blurbs and no counts (Noah 2026-08-13) — and the nav carries a red dot
  while any ask is open (`useFloorRequests({enabled})` in AppShell, dotted
  paths through NavList/BottomNav). **Swipe LEFT removes**, and the two
  sections differ on purpose: an ask is dismissed FOR GOOD (a person judged
  it), while a computed suggestion only goes quiet for a week —
  `suggestion_snoozes` + `POST /restock/back/{id}/snooze` (migration
  `c8e4a1b93f26`), excluded by `back_list` while `snoozed_until > today`,
  because the numbers will say the same thing tomorrow morning. The bulk
  button is **"Add all"**: it pours the suggestions into whatever draft is
  already open (no navigation, no replacing a half-picked draft) — with
  nothing in progress the draft simply starts there.
  The restock page's **"From warehouse" tab is GONE** — that computed
  back-stock list is now the Database Suggestions section (the `back` half of
  GET /restock is unchanged and still feeds it); /restock is one list again.
  Swipe-to-add on restock / catalog / OOS now includes floor_rotating: the
  draft is role-neutral, only its destination differs.
- Bubble bin + icons (2026-08-13): **drag the pill to the bottom of the screen
  to clear the draft.** A `DropZone` band (`DROP_ZONE_H` 104px) appears only
  while dragging and turns red under the finger; releasing in it clears and
  raises an UNDO snackbar (Toast gained an optional `ToastAction` — one M3
  action, 7s instead of 3.8s) that restores the exact lines. No confirm
  dialog: a half-built pull list is real work, but the gesture is deliberate
  and the undo is one tap. Two traps handled: the drop test reads
  `overBinRef`, NEVER the `overBin` state (a fast flick puts the last
  pointermove and the pointerup in one frame, and the state would still be
  false — the same lesson as the swipe rows), and the pill's resting inset
  (150px desktop / 200px phone) is deliberately deeper than the band, so a
  nudge where the pill already lives can't bin anything. Icons: the pill wears
  `Icons.truck` for a transfer and `Icons.box` for a Floor Team ask (was a
  shopping cart). NOTE for browser-pane testing: HMR duplicates
  transferDraft.ts, so an undo can write to a stale store instance and the
  pill won't reappear — hard-reload before believing that failure.
- Scanner + close-out fix (2026-08-14, same build-on rule): **the transfer
  close-out poll was starving itself.** `poll_received_in_odoo` and
  `poll_count_validation` shared the `count_checked_at` throttle stamp AND each
  took it before its own Odoo read, so whichever ran first stamped it and the
  second bailed on that fresh stamp — every time, forever. The count closer was
  therefore dead from the day `write_prepare_count_transfer` went live: on the
  hosted stack III/INT/04691 was validated 2026-08-12 and request 42 sat in
  `counting` regardless. Both are now private (`_close_from_count_picking`,
  `_close_from_floor_receipt`) behind ONE public `poll_close_out` that takes the
  stamp once and runs both — count picking first (matched by id, the deliberate
  count), floor receipt second. Never take the stamp inside a closer again. The
  whole suite ran with `ODOO_COUNT_POLL_SECONDS=0`, which is exactly the setting
  that hides a stolen stamp, so `test_count_validation_survives_a_real_throttle`
  keeps a REAL 600s throttle and is the control for the class.
  `find_received_pickings` now matches origin in (TR ref, CNT ref) and excludes
  both app pickings — the floor sometimes duplicates the count transfer rather
  than the placement.
- **Barcode scanning** (`frontend/src/scan/`, same round): top-bar icon
  (`Icons.scan`, both app bars) → full-screen camera sheet → exact lookup →
  the EXISTING ProductDrawer (reuse, not a new result view; closing it returns
  to the camera, so the aisle loop is scan-look-scan). `decode.ts` picks the
  engine: native `BarcodeDetector` reads the <video> with zero pixel copy
  (Chrome/Android), else **zxing-wasm** — the only decoder iOS Safari has, which
  is why "native only" was never an option. Both are format-limited to retail +
  shelf-label symbologies (the biggest speed lever), and the wasm module +
  its ~1MB binary are dynamically imported so the native path never fetches
  them. `useScanner.ts`: `enabled` owns the camera stream, `paused` only stops
  the decode loop (a result freeze keeps the camera warm — "scan again" is
  instant, not a second warm-up); loop runs on `requestVideoFrameCallback` so a
  frame is never decoded twice; the wasm path decodes a center-band ROI capped
  at 720px, not the full frame; check-digit formats (EAN/UPC) accept on one
  read, Code39/128/ITF wait for two identical ones; continuous autofocus +
  torch toggle where supported. `GET /products/by-barcode/{code}` (declared
  BEFORE `/{product_id}`) is EXACT-match only — a scan is an identity claim, so
  ambiguity is a miss, never a guess — with `barcode_candidates()` covering the
  UPC-A/EAN-13 leading-zero pad (verified live: scanning `0021908129419` finds
  stored `021908129419`), falling back to SKU/internal-ref for Code 128 shelf
  labels, and returning blacklisted/inactive items (someone holding the item
  wants to know what it is). Manual-entry field doubles as the USB/bluetooth
  wedge-scanner path and the fallback when the camera is refused.
  **Deploy gotcha, caught before shipping: `Permissions-Policy: camera=()`
  disabled the camera site-wide** — it's `camera=(self)` now, and `script-src`
  gained `'wasm-unsafe-eval'` (Chrome refuses to instantiate WebAssembly
  without it) in all three policy copies: render.yaml, frontend/vercel.json,
  infra/Caddyfile. Toast durations are now per-viewport (`LINGER` in Toast.tsx):
  phones 2.2s/4s (the snackbar sits over the list you're working in) vs desktop
  3.8s/6.5s, but an undo offer keeps its full 7s everywhere — reaching the
  button is the point of it.
- Prices + cost removal (2026-08-14, same build-on rule): **the product sync
  read the wrong price field.** On a variant, Odoo's `list_price` is the
  TEMPLATE's sales price; this catalog prices sized goods through attribute
  extras, so CM233 (Mens-Mangalgiri-Dhoti) carried list_price **-9.00** with a
  +35.00 `price_extra` per size while the register charged `lst_price` **26.00**.
  `app/sync/products.py` now reads **`lst_price`** (= list_price + price_extra,
  what the POS rings up), falling back to `list_price` when an instance or
  fixture set doesn't expose it. Live scale: **856 of 4,037 active variants had
  a wrong price**, incl. 100 NEGATIVE and ~690 showing $0.00 (Herbal-Toothpaste
  read 0 where the shelf says 2/4/8); after the fix, zero negatives. The
  fixture Kurta (208) now carries the real shape — list_price -9, lst_price 28
  — so `test_product_sync_stores_the_price_the_register_charges` fails with
  `-9.0 == 28.0` without the fix. Don't "simplify" the sync back to list_price.
- **Cost is gone from the app** (Noah 2026-08-14). `ProductOut` no longer
  carries `cost`, the catalog lost its `cost` sort (an ordering key is a read
  of it by another name), the product drawer lost its Cost row, and the PO line
  drawer lost its "Economics" MiniStat — margin is retail minus cost and retail
  is on screen, so publishing margin published cost. `ordering/router.
  public_suggestion()` is the single door: it strips `COST_KEYS` (unit_cost,
  cost, margin, profit_lost_by_air, sea/air_shipping_cost) from every
  suggestion sent to a browser, because the frozen `suggestion_json` otherwise
  rides to the client whole — "not rendered" is not "not sent". The stored JSON
  and `products.cost` are UNTOUCHED: the engine's margin rule needs cost, and
  **the India export still writes UNIT COST (COGS) / MARGIN / PROFIT LOST BY
  AIR** — that spreadsheet goes to Coimbatore and is the one place cost is
  still meant to appear. Cut those columns only on an explicit ask.
- Restock live-sync + line ageing (2026-08-15, same build-on rule): three
  separate things were making the list wrong.
  (1) **The fold could burn a day.** `fold_floor_restock` walked to *yesterday*
  regardless of whether the sales sync had loaded those days, and folding is a
  one-way door. On the hosted stack (worker off) the sales sync last succeeded
  08-13 while `folded_through` had already reached 08-14, so a whole day of
  shop sales sat in `sales_daily` unable to flag anything, permanently. The
  fold now stops at `min(yesterday, sales_covered_through(db))`, where a
  successful sales run at time T proves every day BEFORE T's date complete (it
  re-pulls the whole current month). No sales SyncState at all = fixture/demo
  data, which folds as before. `folded_through` is never rewound — re-folding
  a day would double-count it — so days already burnt stay burnt.
  (2) **Open lines never expired**, which is what read as "the list repeats
  each day": 20 of 51 open lines on live were 15-19 days old and had been on
  every morning's list since July. `expire_stale_lines` stamps
  `restock_lines.expired_at` (migration `d3b7f21a5c40`, additive) after
  `restock_line_max_age_days` (7; 0 disables). The row is KEPT — it is the
  record of what the floor was asked for and never did — but leaves the list,
  and `_flag`'s open-line lookup skips expired rows so the next crossing
  starts a fresh line with an honest quantity instead of growing a
  three-week-old one. Rows already showed "Added N days ago"; now nothing on
  the list is older than a week.
  (3) **Nothing synced at all** on the hosted stack (no worker on Render's free
  tier — data only moved when someone clicked "Sync now"). The restock GET is
  now its own refresher: `claim_stale_refresh` (sync/runner.py) stamps
  `last_attempt_at` BEFORE any work and commits, so ten phones opening the page
  fire one sync, not ten — the same claim trick as the transfer pollers — and
  the work runs in a FastAPI BackgroundTask AFTER the response
  (`refresh_domains_in_background`), because a stock sync takes seconds and
  nobody should wait for it to see a list they already have. Budgets:
  `restock_refresh_stock_seconds` 300 (the aisle reads those numbers) and
  `restock_refresh_sales_seconds` 1800 (the heavy pull; it only changes the
  list at a day boundary). **Fixture mode refreshes nothing** — there is no
  Odoo to be behind and a simulator sync would overwrite seeded demo/test data
  (that check is why test_restock passes; without it the background task runs
  synchronously under TestClient and wipes the hand-seeded stock). The page
  polls at `BOARD_POLL_MS` like the transfer board and shows "Shelf counts
  updated N min ago" from the new `meta.stock_synced_at`. NOTE: TanStack pauses
  `refetchInterval` while the tab is unfocused, which is correct for a phone in
  a pocket — and means the browser pane reports ZERO polls until you spoof
  `document.hidden`/`hasFocus` (3 GETs in 13s once you do).
  **Still worker-shaped:** every OTHER page on that deployment is as stale as
  ever. Restock refreshes itself; transfers/OOS/coming-soon/purchasing do not.
- Centers map (2026-08-15, admin + desktop only): `/centers` opens on a real
  map of North America with the list preserved underneath (phones get the list
  alone — `hidden lg:block`). **The geography is committed, never fetched**:
  `backend/scripts/build_map_geo.py` projects Natural Earth boundaries (public
  domain; the 2.5MB inputs are NOT in the repo, URLs in the script) through an
  Albers equal-area conic, simplifies them in PIXEL space (the projection's own
  units are ~1.0 for the whole continent, so a tolerance expressed in them is
  meaningless), and writes `frontend/src/pages/admin/mapGeo.ts` — 60 shapes,
  ~70KB, plus a `project()` that is the SAME maths so a center's lat/lon lands
  exactly where its state does. The deployed CSP allows no external host, so
  tiles were never an option. **Watch the y sign**: the conic's y grows
  northward and SVG's grows down, and an upside-down continent looks plausible
  enough at a glance to ship (it did, for one screenshot).
  Positions come from `app/centers/geo.py` — a gazetteer keyed on the roster's
  center NAME with a state-centroid fallback, since the roster has cities, not
  coordinates. All 62 were checked against their own state polygon by a
  point-in-polygon pass; New York and St. Louis were nudged a mile inland
  because both sit ON a simplified border and rendered in the harbour/in
  Illinois. **Colour**: a dot map is an all-pairs form, and the dataviz
  validator says exactly two 4-hue sets clear all-pairs in BOTH modes and no
  5-set does — so hue carries the four FIELD zones and nothing else (`--zone-1
  ..4` in tokens.css, yellow/magenta/green/violet). Canada needs no hue (it is
  the only thing above the border), III Departments is ONE campus glyph (five
  departments at one address are not five places), and unzoned centers are
  grey — an absence, not an identity. Zone territories are convex hulls
  stroked round and fat (a 1-2 center zone inflates to a dot or capsule, which
  is honest), each with a direct title placed ABOVE the hull — at the centroid
  it sat on the very cities it named, and Boston and New York lost their
  labels to "ZONE 1 (LILI)". City labels are greedy collision-avoided (active
  first, then alphabetical so the choice is stable), and MORE appear as you
  zoom because the boxes shrink in map units. Wheel-zoom needs a NON-passive
  listener (React's onWheel is passive, so preventDefault is ignored and the
  page scrolls out from under you); setPointerCapture is try/caught like the
  bubble's. `GET /centers/{id}/detail` feeds the click panel: Order Reviewer
  (the zone's coordinators), Order Requesters (center-scoped orderers), roster
  contacts who aren't app logins, and a **live Odoo quant read** of the
  center's own III/CityCenter location — deliberately not synced (the stock
  sync covers four locations; adding 54 for an occasional panel is a bad
  trade), and honest when there's nothing to read: `stock_status` is
  ok/unmapped/unavailable, and 10 of 62 centers have no mapped location.
  Browser-pane note: `document.querySelector("svg")` grabs a NAV ICON, not the
  map — target `svg[role="img"][aria-label^="Map of"]` or you'll conclude the
  interactions are broken when they aren't.
  **Feedback round the same day** — four fixes, one of them a real bug:
  (1) **Clicks did nothing.** The frame took `setPointerCapture` on
  pointerdown so it could pan, and capture retargets the following `click` to
  the capturing element — so the dot's own onClick never fired. Capture is now
  taken LAZILY, on the first pointermove past `DRAG_SLOP` (4px): a press that
  doesn't move stays a click. Verified with a real mouse click, not a
  synthetic `dispatchEvent` — dispatching a click straight at the circle
  bypasses capture entirely and passes against the broken build.
  (2) **Zoom is gentler and eased.** A notch multiplied the scale by 1.15 and
  painted it, so one trackpad flick (a dozen events) crossed half the range.
  Now each event nudges a TARGET by `exp(-deltaY * 0.0016)` — sensitivity
  follows the device rather than the event count — `deltaMode` is normalised
  (a Firefox line-delta would otherwise zoom ~16× harder), and a rAF loop eases
  the drawn view toward it. The wheel also commits 35% of the move
  SYNCHRONOUSLY: rAF is throttled in background tabs and some webviews, and an
  ease-only zoom does nothing at all there. Measured: 1.023 per trackpad notch
  (was 1.15), 1.70 after a ten-notch flick.
  (3) **Dot AREA is last month's units** (`radiusFor`, sqrt scaling — doubling
  the radius would quadruple the ink and read as 4× the sales). The legend
  carries two reference circles. A center that sold nothing still draws at
  `R_QUIET`, because "exists and did nothing" is information.
  (4) **Trend glyph**: ▲/▼ above the dot, month over month — one pop-up's
  setup against the previous one, which is the only comparison that means
  anything for a shop that opens once a month. Both months are COMPLETE
  (`app/centers/sales.py`, `comparison_months`): including the in-progress
  month would show every center collapsing on the 3rd and recovered on the
  30th. Under 5% is "flat", a first month with no baseline is "first" (never a
  percentage — nothing to divide by), and a center the rollup has never seen
  reads null, not zero. The arrow is the encoding and colour only seconds it,
  so it survives CVD and greyscale. The panel spells the numbers out.
  Dormant centers are tinted RINGS: a solid surface fill turned a big dormant
  center (Richmond and Houston are both marked inactive and still selling)
  into what looked like a hole punched in the map.
  Pure signal maths lives in `pages/admin/centerSignals.ts` with unit tests —
  exporting `trendOf` from the component module broke Fast Refresh, and the
  map's handlers then silently stopped updating mid-session ("Could not Fast
  Refresh (export is incompatible)" in the console is the tell; hard-reload
  before believing a handler is dead).
  **Centers page rework (2026-08-16, same round):** the map click bug plus the
  page around it. (1) **Clicking a dot no longer scrolls the page** — selecting
  used to `scrollIntoView` the list, which read as the page lurching away from
  the map you just clicked. The panel opens on the map; nothing moves.
  (2) Map frame is `62vh` (max 46rem) and sits `-mt-4` under a header with NO
  subtitle — it used to run flush to the bottom of the window and read as cut
  off. The "62 centers across 6 zones" summary line is gone.
  (3) **The list says who is involved**: `GET /centers` now carries `reviewers`
  and `requesters` (display names only — contact details stay on the detail
  endpoint), built by `_people_index` in ONE query, because asking per row is
  60 round trips to render a table. The People cell shows Reviewer / Requester
  / Roster with icons and a `+N`; the zone cell wears the SAME hue as the dot
  on the map (`zoneColors`/`zoneSwatch` moved to centerSignals.ts so both call
  one function); city+state sits under the name with a pin.
  (4) **The roster is now edited in the app.** `PATCH /centers/{id}` (admin)
  takes the center fields and, when `contacts` is present, REPLACES the whole
  roster — the editor shows all of it, so a partial send would silently drop
  people. `clear_zone` exists because a null `zone_id` can't say "unassign" on
  its own, and blank contact rows aren't people. CenterEditDialog also invites
  an Order Requester scoped to that center (the existing `POST /admin/users`),
  which is the "add users right here" ask. NOTE: a Toggle must NOT be wrapped
  in `Field` — the floating label is drawn for a text input and lands on top of
  the switch.
  (5) **Import moved to the bottom and takes a FILE**: "Import from .xlsx or
  .csv" → `POST /admin/import/coordinators/upload` (multipart; 8MB cap; temp
  file deleted in a `finally`, because a roster carries every coordinator's
  phone number). The importer reads either format now — `read_sheets` splits
  xlsx/csv and the parser works on `(name, headers, rows)`. A CSV is ONE sheet,
  so the Zone column carries the zone split the workbook did with tabs; note
  `_col` fuzzy-matches headers, so "Zone Coordinator" resolves to "Zone" and
  only the zone-CODE path ever runs (pre-existing, harmless for the real file,
  but it will confuse the next person writing a fixture). The old no-file
  endpoint stays for the seeded/local path.
- Settings, mobile nav and DARK AS A SETTING (2026-08-16, Noah's list):
  **Dark mode is now a choice, not the device's.** It was a bare
  `@media (prefers-color-scheme: dark)`, so a phone in dark mode forced dark
  and locked the user out of every light palette. `public/palette.js` now
  RESOLVES the stored preference (system | light | dark, key `ilops_theme`,
  default system) before first paint and stamps `data-theme` on `<html>`;
  tokens.css lost all three media queries for one plain
  `:root[data-theme="dark"], :root[data-theme="dark"][data-palette]` block per
  group (scheme, chart, zone). Resolving in JS is what keeps that to ONE block
  instead of a media query plus an override of it. `theme.ts` owns the mode
  (`currentThemeMode` / `resolvedTheme` / `setThemeMode` / `watchSystemTheme`,
  the last wired in main.tsx so a phone that flips at sunset flips the app, and
  it repaints the `theme-color` metas too). Verified on a dark device: Light →
  #fbfafd, Dark → #131523, Match my device → follows, and Neem parchment now
  works on a dark phone.
  **Settings is the admin surface**: account FIRST, then appearance (theme +
  palette), then an "Admin pages" card linking Users and **Dev Tools** (the old
  Status page, renamed in the route title, the page header and silly mode),
  plus the styleguide and palette lab. Both left the main nav, so
  `homeForRoles(admin)` is `/reports` now — nav.test.ts asserts it. The gear is
  a real cog (eight teeth on a ring) rather than the old lumpy blob.
  **Mobile nav is a bottom bar for EVERY role.** Five slots; a role with more
  destinations keeps the first three, then **Scan** (pinned — it was asked for
  as a menu item, so overflow must never eat it), then **More**, which opens
  the rest in a sheet. The hamburger drawer is gone: it hid the whole menu from
  exactly the roles with the most to reach. `NavItem.short` is the bar's label
  (a slot is ~70px; "Search Inventory" truncated to "Search Inv…"). The brand
  lock-up is gone from the mobile top bar and NOTHING replaced it — the page's
  own headline is an inch below, and printing it twice was worse than the brand.
  **Inbox card**: on phones it is `fixed inset-x-3` under the top bar, not a
  320px popover anchored `right-0` to a bell that sits mid-bar — that hung the
  left edge off a 375px screen. md+ keeps the anchored popover. (Measure it
  with offsetWidth: the pane pauses `animate-pop-in` mid-scale and
  getBoundingClientRect then reports 0.6× the real box.)
  **All SKUs → "Search Inventory"** with a magnifier, and the search box takes
  `autoFocus` — the page's whole job is the query. It also seeds from
  `?search=`, which is what the scanner's "Search the catalog" now passes (it
  was navigating to `/products`, an API path with no route — dead button).
  **Scanner manual entry takes letters**: `inputMode="text"` +
  `autoCapitalize="characters"`, because plenty of these codes are CM233-L, not
  digits, and a number pad can't type them.
- Cleanup pass (2026-08-17, Noah asked for redundancy removal; logic and
  layouts unchanged): **the warp shockwave is deleted** — warpFx.tsx (572),
  warpWorker.ts (183), warpWave.ts (66), warpWave.test.ts (46) and its 66-line
  `.tm-*` CSS block, plus the `@fontsource/special-elite` + `@fontsource/
  orbitron` packages that only it used. Nothing had imported it since the
  time-machine page went on 08-11; its last reference was a comment explaining
  why it was still there. It is in git history if a 4th-wall moment ever comes
  back — the earlier notes above describe what it did and which two approaches
  (SVG displacement, main-thread rAF WebGL) failed on the way to it.
  **One throttle helper**: `models/base.is_due(stamp, seconds, now)` +
  `elapsed_since` replace four hand-rolled copies (transfers ×2, center_orders,
  sync/runner) of the same naive-vs-aware datetime dance. SQLite hands those
  columns back NAIVE and Postgres aware, so comparing them raises TypeError in
  a background poll rather than in a test — four copies were four chances to
  forget. A missing stamp reads as `inf`, i.e. "never looked" is always due.
  **Three dead hooks removed** (`useImportCoordinators`, superseded by the file
  upload; `useReplaceTransferLines`; `useAdjustCenterOrderLines`). The last two
  matter: `PUT /transfer-requests/{id}/lines` and `PUT /center-orders/{id}/lines`
  are LIVE, TESTED endpoints that no UI calls — editing a transfer's lines and
  a reviewer adjusting an order before approving are both unbuilt front ends,
  not dead code. The endpoints stay. The transfer detail page no longer claims
  lines are "editable from the request form" (they never were — there is no
  such button); it now points at cancel-and-re-raise, which is the real path.
  **Two stale tests fixed**: `test_center_orders` pinned an incoming ETA to a
  frozen fixture date while the endpoint labels against the real today, so it
  started failing on its own in August — the fixture is relative now. And
  `e2e/smoke.spec.ts` asserted nav items that had left the menu (Styleguide on
  07-25, Users on 08-16); it asserts the current shape and a new test covers
  Settings holding the moved pages. `ruff check backend worker` — the CI
  command — passes clean for the first time in a while (three import-order
  findings that predated this work).
- Search FAB in the phone bar (2026-08-17): search LEFT the bottom bar's row
  for a round `bg-primary` FAB docked at the left and breaking out above the
  bar (`SearchFab` in AppShell; Noah's sketch). Finding a product is the most
  common phone task and the bar's slots are all one size, so it could never
  read as the primary action from inside one. It RESERVES a slot's width with
  a spacer rather than floating over a neighbour — a 56px circle sitting on a
  tappable label is a mis-tap waiting to happen — so the row keeps four cells
  (`BOTTOM_SLOTS - 1` when a FAB is present). Roles with no inventory search
  (Order Reviewer, Order Requester) get NO FAB and no spacer: nothing for it
  to open. It wears `on-primary` (deep umber / dark on the peach dark-mode
  primary), not the white of the sketch — that is the token with real contrast
  on this orange, and it keeps the button reading as the same brand action as
  every other primary control. Active state is a ring, not a fill change: the
  FAB stays brand orange wherever you are.
- **Delivery form: the warehouse declares what they sent (2026-08-17, Noah's
  rework — full rationale in DECISIONS.md). The warehouse works in Odoo and
  always will, so the app follows them.** Flow now: floor requests → app
  drafts BWHSE→**Staging2** (`service.placement_dest_key`, falls back to
  floor staging where staging2 isn't mapped — one less manual retarget) →
  ANY write to that picking reads as "seen by warehouse"
  (`service.warehouse_has_acted`: state != draft OR write_date > create_date
  by >3s; the simulator returns False for absent date fields and "no
  information" must never read as "somebody edited it") → they pull it,
  splitting it however they like → validated into Staging2 = **staged**
  (`sent`), NO count prepared → they make ONE staging2 → floor-staging
  transfer IN ODOO → they fill the **delivery form** → validation closes
  every request on it as **received** (`done`) and prepares ONE count
  transfer for the whole pallet. `app/transfers/delivery.py` is the module
  (build on it, never around it): `candidate_pickings` (Q1: recent
  staging2→staging pickings, `?search=` = the "Don't see it?" path matching
  a name ANYWHERE in Odoo), `suggest_requests` (Q2: EVERY linkable request
  comes back — `suggested=False` ones hide behind "Add another transfer…",
  which is the "button to add more" without a second endpoint; auto_select
  = staged AND its items are on the pallet), `discrepancy_review` (Q3: per
  PRODUCT summed across the selected requests, |sent − asked| >
  `transfer_discrepancy_threshold` (3); products nobody asked for come back
  as `extras` — information, never a question), `allocate_sent` (pure,
  unit-tested FIFO: oldest request filled first, surplus to the last asker
  — the floor WILL ask why their request shows 6), `declare` (links,
  freezes contents, writes qty_sent back, saves reasons, lands it if the
  picking is already done) and `land`/`prepare_delivery_count`/
  `poll_delivery_counts`. Reason codes = `DiscrepancyReason` (no_stock,
  full_case, another_transfer, other; ≥1 required per flagged row and
  `other` needs a note — enforced server-side in `validate_reasons`, and
  the server RE-COMPUTES the review on submit so a stale dialog can't sneak
  a gap through). **An UNDECLARED pallet closes nobody's request** —
  `poll_manual_pallets` records it (with its contents) and the page shows
  "needs details"; guessing would close requests still waiting. Sent
  quantities sum across the whole validated picking family sharing the
  request's ILAPP-TR- reference (`service.outbound_family` — done pickings
  only, floor-bound receipts excluded, or a split double-counts). The
  DELIVERY's count is `prepare_count_transfer(allow_foreign_source=True)`
  (the pallet is usually a picking the app didn't create; `copy` writes
  nothing to the source and the copy keeps our ILAPP-CNT- reference), and
  its differences file as adjustments with `pallet_id` set and request_id
  NULL. `mark_sent` only prepares a per-request count on the DIRECT path
  (`service.landed_at_floor_staging`) — two staging→floor mechanisms would
  move the same units twice. Status KEYS unchanged; labels are "Seen by
  warehouse" / "Staged" / "Received" and the stepper drops `counting`
  unless that's where you are. Migration `e4a7c2b91d63` (additive:
  pallet_transfers gains declared_*/count_*/note, + `pallet_requests`,
  `pallet_discrepancies`, `adjustments.pallet_id`). `useDeliveryPreview` is
  a MUTATION, not a query — it reads Odoo live and the answer changes as
  boxes are ticked. **`api()` JSON-stringifies the body itself** — passing
  `body: JSON.stringify(x)` double-encodes and FastAPI answers "Input
  should be a valid dictionary" (caught in the browser pane, not by
  typecheck). Candidate contents come from `picking_contents_bulk` — ONE
  stock.move read for the whole list; per-picking reads were ~3s of
  nothing every time the form opened.
- **Warehouse menu is two items (2026-08-17, Noah): Send to floor + Search
  Inventory.** They live in Odoo; the scanner, inbox and settings are
  top-bar furniture (Scan is pinned in the phone bottom bar). Incoming,
  Transfers, Coming soon, Out of stock and Adjustments left the MENU only —
  routes and role access are untouched, so a link still opens them and
  `homeForRoles(warehouse)` is `/staging2` now (nav.test asserts both the
  two-item list and the home). Consequence to watch: the adjustments queue
  has no entry point for the role that owns it — a count difference on a
  delivery is still filed, just not surfaced in their nav.
- One-time release of the pre-form leftovers (2026-08-18): requests that were
  mid-flight when the delivery form landed sit in `counting` with their OWN
  floor-staging→floor count picking — a status the form can't link, while
  their stock really rides the warehouse's next pallet (III/INT/04709 carries
  several). `delivery.release_stale_counts` (+ `POST /admin/transfers/
  release-stale-counts`, `apply:false` PREVIEW default, "Stranded transfer
  requests" card on Dev Tools) rewinds each to `sent` (staged → the form
  offers it, auto-ticked once its items are on the pallet) and clears the
  count_* fields so the pallet's single count is the only one. Three
  deliberate limits: a count Odoo reports `done` is LEFT ALONE (the floor
  really counted it; `poll_close_out` closes it) — so the Odoo read must
  SUCCEED or the whole thing 422s and changes nothing (`test_release_refuses_
  to_guess_when_odoo_is_unreachable` is the control: rewinding a real count is
  the worse error); an unrecognised Odoo state is reported, never acted on;
  and **the app cancels nothing in Odoo** — retiring a leftover count picking
  would be a new write op needing its own flag+canary, so the report names
  each one with a deep link for a human, which matters physically (a leftover
  count AND the pallet count both scanned = the same units moved twice). An
  endpoint rather than a script because the hosted stack has no shell; not a
  data migration because the decision needs a live Odoo read.
- Flow reset back to a known point (2026-08-18, Noah): two weeks of testing
  left a full transfer board and **15 pallets "landed with no details"**.
  `app/transfers/reset.py` (`POST /admin/transfers/reset-flow`, `apply:false`
  PREVIEW default + a typed-CLEAR confirm on the Dev Tools card "Reset the
  transfer flow") deletes every TransferRequest older than `keep_hours` (24)
  with its lines/events/adjustments/pallet links, ALL PalletTransfer rows, and
  stamps the discovery watermark. **The watermark is the load-bearing part**:
  `poll_manual_pallets` de-dupes undeclared pallets against the pallet rows it
  already HAS, so deleting the 15 rows alone would rediscover the same 15
  pickings on the next poll — the reset writes `discover_from` (Odoo datetime
  format) into the `manual_pallet_poll_state` AppSetting and the discovery
  domain gained `["date_done", ">", …]`, which is also what "start from the
  next pallet" means. `test_flow_reset_clears_the_rubble_and_discovery_starts_
  after_it` is the control (fails with "the reset watermark should hold" when
  the honoring is removed — verified by removing it). Odoo side: the reset
  unlinks ONLY app-created pickings that are still `draft` (the
  `cancel_placement_draft` rule — a draft moved no stock), and everything else
  (validated, or a human's own picking) is REPORTED with a deep link and left
  alone; no new write op, so no flag/canary needed. Requests inside the keep
  window survive whole, Odoo draft included. Cutoff uses `elapsed_since` — a
  bare `created_at < cutoff` raises TypeError on SQLite (naive) vs Postgres
  (aware), which is why that one copy of the dance exists. Three honest
  outcomes per picking, and keeping them apart is the point: Odoo NOT ANSWERING
  is a REFUSAL (`ResetError` → 422, nothing deleted — otherwise every picking
  reads as unknown and the app would delete its own rows while telling a human
  to cancel drafts it could have removed itself, which it then never can); an
  id the successful read doesn't return is `already_gone` — deleted in Odoo
  during testing, "nothing to do", never a goose chase; anything present and
  not a draft is a `leftover` with its real Odoo state. Caught by running the
  preview on live: III/INT/04636 came back "unknown", and Odoo was up — the
  picking was simply gone.
- "Already on the way" warning on the transfer form (2026-08-18): the app
  hid this where nobody building a request would look — /coming-soon was a
  separate PAGE, and `back_list` silently dropped items on an open request,
  so the only surface that never mentioned pending stock was the one where
  you commit to asking for it (the API only rejects a duplicate product
  WITHIN one request, never across two open ones). `useOnTheWay()` +
  `OnTheWayChip` (OpsBits) reuse the EXISTING /coming-soon aggregation — no
  new endpoint, and it therefore covers app requests AND transfers the
  warehouse made straight in Odoo. Three surfaces on NewTransferPanel: a
  chip on each search result, a chip on each draft line, and a summary
  above Send ("All 3 items / 2 of these 7 items are already on the way …
  Send it anyway if the floor needs more" + a link to /coming-soon). The
  shared `ProductPicker` gained a generic `annotate?: (productId) =>
  ReactNode` slot rather than learning about transfers — catalogs and
  vendor rosters pass nothing. ADVISORY BY DESIGN: it never blocks Send and
  the API still allows the second request, because a shelf that cleared at
  lunchtime is real and the floor knows it before the numbers do.
- Suggested items ON the transfer form + phone-nav reshuffle (2026-08-18,
  Noah): `pages/transfers/SuggestedStrip.tsx` sits between the draft and the
  Send card — the same two voices as /suggested-items, in the same order
  (**people first**: open FloorRequests, then `restock.back` which the engine
  already sorts by urgency — never re-rank it), reusing that page's add
  semantics exactly (suggested qty, `addToDraft` merge, and taking an ask
  `resolve`s it to picked-up so the asker sees it landed). Items already in
  the draft drop out, and a zero total renders NOTHING — no empty box above
  Send. Expanded when the draft is empty (then it's the most useful thing on
  screen), a `Suggested items (N) ▾` disclosure once you've added something,
  so a long strip can't push Send off a phone. Five slots then "See all N →";
  the slot maths is `suggestedRows.ts` (pure + tested) in its OWN module —
  exporting a non-component from the component file breaks Fast Refresh (the
  centerSignals lesson). **No flyToBubble here**: the bubble is hidden on this
  route, so the item appearing in the list above IS the acknowledgement.
  Tap-only, deliberately — swipe-left would mean snooze, too big a commitment
  mid-request. Consequence: this panel now calls GET /restock, which is its
  own claimed sync refresher, so opening New transfer freshens shelf counts.
  Nav: `shoppe_floor` order is now Restock · **Transfers** · Suggested items ·
  … — the phone bar keeps only the first TWO destinations before Scan/More, so
  Transfers is one tap from anywhere and Suggested items moved into the More
  sheet (its red ask-dot follows it there via `overflowDotted`). Restock stays
  first, so `homeForRoles(shoppe_floor)` is unchanged; the desktop sidebar
  reorders to match. floor_rotating is untouched (Request items is their job).
- Pre-launch round (2026-08-18, Noah's list of six):
  **1. Restock groups by TYPE** — `app/restock/grouping.py` maps a BARCODE
  prefix to an aisle (IN → Incense; verified live: 61 items, every one an
  Incense-Stick-*), because the Odoo category is too coarse to walk a shop by
  ("Home" holds incense, candle holders and bath towels). **CA never names a
  group** (Noah) — a two-letter prefix plus ten digits is an India import
  reference (`CA0023000009`), so it says where a thing shipped from, not what
  it is; `NEVER_GROUP` enforces it and the PUT endpoint 422s on it. EX/CX/WC/ME
  are deliberately unmapped for the same reason (verified spread across
  unrelated categories); unmapped prefixes fall back to the Odoo category, then
  "Other". Defaults live in `PREFIX_GROUPS`, overridable via the
  `restock_groups` AppSetting (GET/PUT /restock/groups, admin; blank label =
  stop grouping that prefix) because the shop coins a prefix long before anyone
  ships a release. **2. Best sellers first** — `popularity()` sums
  `sales_daily` units over 90 days on SHOPPE_CHANNELS only (a city-center hit
  must not reorder the shop's shelves); `sort_key` = group total desc, then
  item units desc, then name, so a fresh install with no sales is still
  stable. Rows carry `group`/`popularity`/`group_popularity`; the FLOOR list is
  sorted server-side and the page only draws a heading where the group changes
  — the BACK list keeps its worst-cover-first order untouched, because
  /suggested-items and the transfer strip depend on it. **3. Every location an
  item sits in** — `GET /products/{id}/locations` (warehouse+floor) reads
  quants LIVE for one product: the stock sync rightly collapses hundreds of
  BWHSE bins into one number, which is the wrong answer for whoever has to go
  FIND the thing. Rolls each bin up into its synced area by longest-path-first
  prefix match, biggest pile first, honest `source` when Odoo is silent (falls
  back to the synced buckets, never "nowhere"). **Filters by location
  `usage`**: live had 3,255 units of one incense at Partner
  Locations/Customers — stock already sold, not a place — so only
  internal+transit count; done as a second read, not a dotted domain, so the
  simulator exercises it. Test fixtures gained location 32 + quant 13 for
  exactly that, and the test targets odoo product 201 BECAUSE "the first
  product with an odoo id" made the assertion vacuous (verified by removing the
  filter). **4. Long-press a truncated name** — `design/ScrollingText.tsx`
  (restock, OOS, suggested items, coming soon, transfer draft, request items).
  IDLE is one `truncate` span (that's what draws the "…"); RUNNING swaps to a
  clipping outer + `inline-block w-max` inner, because clipping the overflow on
  the element you translate slides the ellipsis along and reveals nothing (the
  first version's bug). 130px/s, 6s ceiling — 70px/s made the worst real name
  a ten-second round trip. Reduced-motion gets nothing but the tooltip.
  **iOS follow-up (same day): it didn't work on an iPhone at all** — Safari
  answers a long press on text by starting a SELECTION (magnifier, handles,
  "Copy" callout) and takes the gesture, which fires `pointercancel` and kills
  the hold timer before it can fire. Fix is the `.scrolling-name` rule in
  tokens.css: `user-select: none` + `-webkit-touch-callout: none`, scoped to
  `@media (pointer: coarse)` so a desk user can still select a product name to
  copy it (a mouse press-and-hold still scrolls). Second half of the fix:
  these names sit INSIDE tappable rows (the restock row is a
  `role="checkbox"` button), so the click after a long press ticked the item
  off — `swallowNextClick()` eats one click in the capture phase and disarms
  on a 700ms timer in case no click arrives, the same discipline as
  SwipeRow.swallowClick. **The press target is the whole CARD, not the text**
  (Noah's follow-up): the card marks itself `data-name-press` and
  ScrollingText finds it with `closest()`, attaching NATIVE listeners —
  which is what lets the gesture coexist with the React pointer handlers those
  rows already carry (the restock row is a `role="checkbox"` button wearing
  useSwipeRow's handlers; spreading a second set would clobber the first).
  Missing attribute = falls back to the text itself, so an un-opted row still
  works. The no-select rule covers `[data-name-press]` too, or a press on the
  card's OTHER text would start an iOS selection. Verified by pressing the
  big "bring out" number: marquee runs, checkbox doesn't toggle.
  **5. Warehouse scan FAB** — the bottom bar's big FAB is role-chosen: the
  Warehouse Team gets Scan (their most common phone task), everyone else keeps
  Search, and Search returns to a normal slot for them. Never two FABs.
  **6. Floor count edit** — `POST /products/{id}/floor-count` (shoppe_floor):
  counted qty in, DRAFT adjustment out, link shown; equal counts write nothing.
  The delta/ceiling/writer dance moved to `app/oos/adjust.py` and is now shared
  with the OOS board's "back in stock" — one copy, two callers. Both sit in the
  product drawer behind disclosures ("Where it is", "Floor count"), so neither
  costs an Odoo round-trip until asked for.
