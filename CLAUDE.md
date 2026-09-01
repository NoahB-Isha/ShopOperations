# Shop Ops — working guide

This file is the **topical** reference for working in this repo: what the app is,
the safety rules, and the invariants and traps each module carries. It replaces the
old chronological log, which is preserved verbatim in **`docs/HISTORY.md`** (the
founding brief + every build round, oldest first — read it when you need the story
behind a rule). **`DECISIONS.md`** holds the rationale for every major call, newest
first. When editing here, update the matching section in place — see
"Maintaining this file" at the bottom.

## What this app is

A production internal web app for **Isha Life USA** (retail arm of a nonprofit at the
Isha Institute of Inner Sciences, Tennessee). It manages North American retail ops:
inventory visibility, warehouse→floor transfers, city-center ordering, quarterly India
import ordering, domestic vendor ordering, inventory counting, and reporting.

- **Odoo 19 is the system of record.** The app reads snapshots from it and writes
  scoped, audited records to it. It never replaces Odoo. There is **no staging
  Odoo** — production is the only instance.
- Physical reality: **Blue Warehouse** (`III/Stock/BWHSE`, hundreds of bin
  sub-locations) receives India shipments and fulfills transfers; the **Isha Life
  Shoppe** on campus (floor + back stock) receives pallets via a staging location;
  **~60 city centers** across US/Canada run monthly pop-up shops, each in a zone
  with a reviewer; **III departments** on campus order from the shop (modeled as a
  special zone whose "centers" are departments — every departments-specific behavior
  keys off `zone.kind == "departments"`, never off a role).
- **India ordering**: most stock ships from Isha Life Coimbatore. Sea lead ≈ 6
  months, air ≈ 4. Bhoomi/Gold/Silver fly only; toothpaste/camphor are bulk ~yearly;
  Bloom is expiry-sensitive. All Coimbatore communication is email + spreadsheets.
- **Clothing** is allowed in catalogs and center-order menus (hand-curated menus
  decide) but excluded from purchasing — candidates, analogy pools, vendor roster
  picker. `test_clothing_is_curatable_but_never_purchasable` locks both sides.
  (This supersedes the founding brief's blanket exclusion — Noah, 2026-07-26.)
- **Data quality is imperfect**, especially at low counts. Odoo numbers are
  authoritative but the UI displays confidence honestly (staleness flags, "verify
  physically", estimated shares reported, never silently).

**Stack**: FastAPI (Python 3.12) + SQLAlchemy/Alembic on Supabase Postgres; a worker
process for sync cadence; React + TS + Vite + TanStack Query + Tailwind, thin
internal design system. REST under `/api/v1`, one API for web + skubot + future
integrations. LLM features call Anthropic server-side; **every LLM output that would
change data is a proposal a human confirms** — never auto-applied.

**Deployment today**: frontend on Vercel (`VITE_API_BASE` → remote API;
`frontend/vercel.json`), backend on Render (`render.yaml`), DB + auth on Supabase.
**Render's free tier runs no worker** — restock self-refreshes (see Restock), other
domains sync on demand from Dev Tools; every other page is as stale as its last
sync. Local dev is Docker Compose (`make dev`): backend/worker are BUILT images
(`docker compose build backend worker && up -d` to land backend changes); the
frontend container mounts source (HMR). With no `ODOO_*` credentials the app runs
against the fixture simulator end to end.

**Roles** — six stored keys, UNCHANGED in code (renaming would rewrite every
`require_roles`, test and seeded row for no user-visible gain — don't): `admin`,
`warehouse`, `shoppe_floor`, `floor_rotating`, `zone_coordinator`, `center_orderer`.
UI labels (frontend `ROLE_LABELS`): Admin · Warehouse Team · Inventory Flow Manager ·
Floor Team · Order Reviewer · Order Requester. `floor_rotating` = shoppe_floor minus
create/edit on transfer requests. Two **add-on roles** (`ADD_ON_ROLES`), held
alongside a real role and grantable in the UsersPage picker: `inventory_wrangler`
(count review queue) and `dept_order_approver` (approve departments-zone orders; it
carries no zone on its assignment — it means every departments-kind zone, enforced
in three places that must agree: `PARTICIPANTS`, `visible_center_ids`,
`_is_coordinator_of`; `notify._recipients` pings add-on holders on departments
orders). The old dept_liaison/dept_orderer roles are gone (migration `a7c3e91d64b8`;
downgrade is a deliberate no-op) — a departments reviewer is an Order Reviewer whose
zone is III Departments. Frontend gets the departments signal from `RoleOut.zone_kind`
→ `useAuth().isDepartments` (drives "department" vs "center" wording). UI copy says
"Review zone"; zone names, tables, columns and API fields are untouched.

## Working conventions

- Business logic lives in pure, tested modules; every module has unit tests. The
  India ordering engine keeps its 281-row workbook parity test green — it is the
  spec of record.
- **Build on the foundation modules, never around them** (list below). New
  transitions go in TRANSITIONS tables, new write ops in the writer, new sync
  domains in SYNCERS — never inline.
- Migrations are additive; a NOT NULL column landing on a deployed table carries a
  `server_default`. The deployed stack runs migrations but no seed — a flag row a
  feature needs must be INSERTED by its migration (`PUT /admin/flags/{key}` 404s on
  a missing row).
- Feature flags for anything that writes to Odoo or sends email. Conventional
  commits. CI = tests + typecheck + lint (`ruff check backend worker` passes
  clean — keep it that way).
- Keep modules small and boundaries clean — this app is iterated on by non-experts
  with AI assistance; legibility beats cleverness everywhere.
- `make test` = backend pytest + frontend typecheck/vitest. `PHASE_REVIEW_CHECKLIST.md`
  is the human acceptance gate.
- Timestamp comparisons: SQLite hands columns back NAIVE, Postgres aware — a bare
  compare raises TypeError in a background poll rather than in a test. Use
  `models/base.is_due(stamp, seconds)` / `elapsed_since` — the ONE copy of that
  dance (a missing stamp reads as "always due"); never hand-roll another.

## Odoo integration policy (safety-critical)

1. **Reads** go through the JSON client feeding a local snapshot cache; the app
   reads its own snapshot, never live-per-request (deliberate exceptions, each
   falling back to the snapshot with an honest `source` when Odoo is quiet: the
   counting page, per-product bin locations, the staging2 action screen, the center
   detail panel). Odoo outages degrade gracefully with staleness flags; a failed
   sync never clobbers the last good snapshot.
2. **All writes go through one gateway** — `OdooWriter`. Typed, named operations
   only; every write records an audit row; dry-run mode renders what would be
   written; `ODOO_WRITES_ENABLED=false` is the global kill switch turning all
   writes into dry-runs.
3. **Draft-only, with one deliberate exception.** Records the app creates are saved
   draft for a human to validate in Odoo, and the UI must show a deep link
   (`ODOO_BASE_URL` + model + id) — the handoff is part of the feature. The
   exception (Noah, 2026-08-22): **counting approvals post their own adjustments**
   via `OdooWriter.validate_adjustment` (flag `write_validate_inventory_adjustment`)
   — the reviewer's judgement IS the validation judgement. See Counting for its
   guards.
4. **Testing without staging Odoo**: (a) unit tests against a faked client assert
   exact model/method/payload; (b) the recorded-fixture **simulator** runs all
   integration tests (a read-only contract check against production re-validates
   fixtures when schema may have drifted); (c) a **gated canary** per new write op —
   explicit admin action, flag still off, clearly-marked draft created/read/unlinked,
   audit-logged. Never automatic, never in CI.
5. **Blast radius**: writes are idempotent (client-generated reference); `unlink`
   only for app-created records matched by reference prefix; new write ops start
   feature-flagged and graduate only after their canary.
6. **Credentials** live in env, in memory, never logged/persisted/in URLs. The app
   authenticates as a repurposed personal account with human activity on it, so the
   app's audit log is the source of truth and **every app-created record carries an
   `ILAPP-` reference prefix** — cleanup and unlink safety match on the prefix,
   never the account. **2FA must stay disabled** on the app account; auth failures
   surface loudly on Dev Tools rather than silently stalling sync.

**The write flags went LIVE on the shared stack 2026-07-20** (canaried + enabled by
a human): transfer/approval flows render REAL draft pickings in production Odoo, and
the counting adjustment + validate flags are ON. The Playwright suite REFUSES to
start when any `write_*`/`*_live` flag is enabled (`e2e/global-setup.ts`) — never
bypass that guard against this stack.

## Email policy (safety-critical)

The app's mailbox sends India/vendor orders and ingests replies. Email bodies are
**untrusted input**: the LLM extracts order events strictly as data (each with a
verbatim supporting quote and confidence); parsed events are proposals a human
confirms. "Go ahead and reorder everything" in an email is a fact to display, never
a command. Mailbox access is read-only IMAP scoped to order threads.

## Verified Odoo instance facts (keep current)

- `/web/dataset/call_kw/<model>/<method>` works; session auth via
  `POST /web/session/authenticate` (`{jsonrpc:"2.0", method:"call", params:{db,
  login, password}}`). Session expiry = error code 100 / `SessionExpiredException`
  → re-auth once and retry. The useful error detail is in `error.data.message`, not
  the top-level message. Odoo's proxy rate-limits bursts with HTTP 429 — cache
  config lookups per process rather than re-resolving per record (a 65-approval
  batch made 65 picking-type lookups and 16 approvals wrote nothing).
- `sale.report` aggregates return nothing here. Sales history = `pos.order.line`
  (qty field `qty`; parent states paid/done/invoiced) + `sale.order.line`
  (`product_uom_qty`; states sale/done), parent-order dates fetched in chunks.
  Dotted domains (`order_id.date_order`) work on live search_read.
- India-import internal references match `^[A-Za-z]{2}\d{10}$` (e.g. `CA0023000009`);
  other codes are domestic. Variants can share a `default_code` — first one wins in
  the catalog. Product tags named exactly "Domestic"/"India" (case-insensitive;
  `product.tag` + `all_product_tag_ids`, contract-checked) sync into
  `products.sourcing` — domestic wins conflicts.
- **On a variant, `list_price` is the TEMPLATE price** — sized goods price through
  attribute extras, so it can be wrong or negative (CM233 carried −9.00 with a
  +35.00 per-size extra while the register charged 26.00). The product sync reads
  **`lst_price`** (= list_price + price_extra, what the POS rings up), falling back
  to `list_price` only when an instance/fixture doesn't expose it. Don't "simplify"
  back — 856 of 4,037 live variants were wrong before the fix;
  `test_product_sync_stores_the_price_the_register_charges` locks it (the fixture
  Kurta carries the real shape: list_price −9, lst_price 28).
- Incoming stock: `stock.move` where `state in (assigned, confirmed, waiting,
  partially_available)` and `picking_code = "incoming"`.
- **`stock.move` has NO `name` field on this v19 instance** — use
  `description_picking` in create-vals; writing `name` fails (verified live via
  canary; the contract check asserts it).
- Locations by `complete_name`: `III/Stock/BWHSE`, `III/Stock/III-FLOOR`, floor
  staging = `III/Stock/III-FLORR-STAGING` (the FLORR typo is production's, live id
  2360; renamed ~2026-07-17). `ODOO_LOCATION_NAMES` maps ALL historical spellings —
  when the stock sync fails with "locations not found", **check for another rename
  FIRST**. BWHSE quants MUST be matched by subtree (`child_of` + path-prefix
  classification in `app/sync/stock.py`), never exact id. Floor has a `Vending
  Machine` child (counts as floor). `III/Staging2` (live id 2030) is a TOP-LEVEL
  sibling of III/Stock (`staging2` LocationKey, in `OPTIONAL_LOCATION_KEYS` so old
  fixture sets don't break the sync). `III/CityCenter/<City>` per-center locations
  exist (60+). `III/Stock/SHIP` (live id 1234) is the online-fulfillment area and
  **folds into bwhse** (below). Production location ids are NOT fixture ids (floor
  is 1232 live, 14 in fixtures) — hardcoding a fixture id makes every live quant
  read return 0 and nearly produced a confidently wrong report once.
- **SHIP folds into bwhse**: `ODOO_FOLDED_LOCATION_NAMES` (models/snapshots.py) maps
  complete_name → the key it folds into; folded subtrees are fetched/classified like
  roots but NEVER become the key's canonical OdooLocation row (transfer drafts must
  keep sourcing from the real BWHSE — the fold test locks this). Missing folded
  locations don't fail the sync; they surface in sync_state.extra
  `missing_folded_locations` (a SHIP rename = ~80k units silently gone).
- Historical stock reconstruction works: `product.product qty_available` under a
  `to_date` + `location` context reads Odoo's own move ledger (verified live).
- `stock.location.usage` names a movement counterpart's KIND (`inventory` =
  adjustment, `customer` = sale, `supplier` = receipt, `internal` = transfer) —
  classify on usage, never on picking-type names (those are instance-dependent).
- 53 pos.configs: 'III Floor' + one per city center + campus one-offs; POS registers
  put HOUSE partners on walk-in orders. ~96% of POS orders and 100% of online carry
  a partner.
- Live data quirks, deliberately untracked: ~190 stray units directly at the
  `III/Stock` root (put-away hygiene, not app scope); Castor/Neem-Oil 6L SHIP counts
  (31k/20k) look physically implausible — Odoo-side data to audit, not app bugs.

## Foundation modules (build on, never around)

- `app/odoo/writer.py` — all writes: add new operations to `OPERATION_FLAGS` + a
  typed method. Qty guards use `math.isfinite` (NaN passed `qty <= 0`).
  `button_validate` is on the WRITE_METHODS allow-list for `validate_adjustment`.
- `app/odoo/simulator.py` — extend RELATIONS / ONE2MANY registries for new query
  shapes. It computes `qty_available` from its quant table (any as-of date serves
  current state — documented), returns False-y for absent date fields, and writes
  `stock.move.line` rows on validate so ledger classification is exercised for real.
- `app/sync/runner.py` — new sync domains register in `SYNCERS`;
  `claim_stale_refresh` stamps `last_attempt_at` BEFORE any work and commits, so N
  clients opening a page fire one sync, not N.
- `app/centers/importer.py` — roster re-import is idempotent.
- `app/transfers/flow.py` + `app/transfers/delivery.py`, `app/center_orders/flow.py`,
  `app/ordering/timeline.py`, `app/counting/flow.py` — state moves ONLY through
  their transition/apply functions.
- `app/models/base.py` — `is_due` / `elapsed_since` (the naive-vs-aware dance).
- `app/downloads.py` — the ONE door for every file response (CR/LF stripped,
  RFC 6266 `filename*`, content-type allowlist).
- `app_settings` table — the generic admin-editable JSON store (`merged()`
  validates by ignoring unknown keys).

## Domain guide

### Sync & snapshots

- Cadence: polite client. Stock a few times daily or on demand; sales = one
  24-month backfill at setup then small hourly incrementals touching the current
  month (previous month once daily). Everything the app shows comes from its own
  snapshot (exceptions listed in policy §1).
- **Sales channel split at sync** (`app/sync/sales.py`): every pos.order classified
  by pos.config name into `shoppe` / `city_center` / `campus_other`; online stays
  `online`; center matching is normalized-name vs the centers table with the
  `sales_channel_aliases` AppSetting as escape hatch; unmatched configs land in
  campus_other honestly (per-config map in sync_state.extra). Rows written
  pre-split keep channel `pos` (displayed "Shoppe-legacy"). Line revenue is tax-in
  (`price_subtotal_incl` / `price_total`); NULL-amount rows are estimated at units ×
  current retail with `estimated_share` reported. `POST /admin/sync/sales/rebuild`
  (declared BEFORE /sync/{domain}) is the one deliberate heavy re-pull. The
  parent-order fetch also captures `partner_id` + `amount_total` into
  `sales_orders_monthly` + `customer_first_seen` (partner ids only, never contact
  details; append-only min — the memory that keeps new-vs-returning stable).
  **House partners** are detected three ways (channel dominance, per-config
  dominance, monthly volume — pure, threshold-injectable detectors), remembered in
  sync_state.extra, scrubbed from customer metrics while their ORDERS still count.
  "New" = first order per (partner, channel) within the 24-month window — don't
  "fix" cross-channel debuts without a partner-level identity decision.
- `sales_daily` (UTC days, retention-pruned) also carries `returned_units`
  (nullable — NULL = pre-capture row, unknown ≠ zero); `units` stays NET. The daily
  window rebuilds each sync so recent rows self-fill.
- **Stock history**: every stock sync appends day totals to `stock_snapshots` +
  `stock_snapshot_days` coverage markers — absent product on a covered day =
  genuinely zero (zero rows aren't stored); same-day re-runs replace; retention
  `stock_snapshot_retention_days` (730). Rows before 2026-08-04 predate the SHIP
  fold — bwhse history jumps that day, honestly. Weekly `source='reconstructed'`
  rows exist on live from the retired time-machine backfill (removed 2026-08-24
  with the rest of the time machine) — they're real data, keep honoring the
  source label.
- **Transfers discovery sync** (`app/sync/transfers.py`, domain `transfers`, runs
  LAST in run_all — it needs the staging location the stock sync maps): finds
  pickings destined for EITHER staging location in (draft, waiting, confirmed,
  assigned), snapshots lines into `staging_inbound_moves` (replace-on-sync;
  validated/cancelled drop out), skips app-placed pickings (by
  TransferRequest.odoo_picking_id) AND ILAPP-origin pickings (app pallets would
  double-count the requests riding them).

### Products, catalog & search

- `app/catalog/` — `/products` list + drawer PATCH. "Catalogs" (UI name; backend
  keeps `order_lists` — don't rename tables/API paths; likewise UI "All SKUs" is
  now "Search Inventory") are product LISTS with no quantities: admin grants
  lists→zones, reviewers grant zone lists→centers; the order form reads a center's
  granted lists. `matching.py` is the ANY-spreadsheet→products matcher: sku →
  barcode (8–14 digits) → exact name → unique containment → unique token-subset;
  short numbers never match; ambiguity = unmatched, never guess.
- **Search is tokenized in three twins — change one, change all three**:
  `app/catalog/search.py` (`product_search_clause` SQL + `matches_search`
  in-memory for availability/bot) and `frontend/src/search.ts`. Match = any ONE
  field contains ALL alphanumeric tokens; tokens never mix across fields (a "00"
  token must not match every barcode).
- `products.blacklisted` + `not_blacklisted()` filters every user-facing
  list/report/flow (header metrics have no product dim and can't exclude).
  **Blacklist sweep** (`POST /products/blacklist/sweep`, declared BEFORE
  /{product_id}; preview → confirm) = never-stocked AND never-SOLD active odoo
  items (IL-Service excepted, manual source exempt) ∪ "USA"-name/-sku duplicates.
  The never-SOLD half is LOAD-BEARING: snapshots are shallow, so fast movers trade
  without stock history — the stock-only first sweep blacklisted 1,308 selling
  items restored by hand. Don't loosen it. (~960 blacklisted, ~2,950 active
  visible on live.)
- `GET /products/by-barcode/{code}` (declared BEFORE `/{product_id}`) is
  EXACT-match only — a scan is an identity claim, ambiguity is a miss, never a
  guess — with `barcode_candidates()` covering the UPC-A/EAN-13 leading-zero pad,
  SKU/internal-ref fallback for Code 128 shelf labels, and blacklisted/inactive
  items returned (someone holding the item wants to know what it is).
- `GET /products/{id}/stock-history` — covered day with no rows = genuine ZERO
  point (emitted, not skipped); last point = live StockLevel; the drawer graph
  breaks its line on >21-day capture gaps; untracked/manual → empty, not error.
  `GET /products/{id}/locations` (warehouse+floor) reads quants LIVE for one
  product, rolls bins up by longest-path-first prefix into synced areas, and
  **filters by location `usage` (internal+transit only)** — Partner
  Locations/Customers is stock already sold, not a place; done as a second read,
  not a dotted domain, so the simulator exercises it.
- `GET /products` `in_stock_only`: sum of StockLevel across ALL locations > 0; no
  rows at all counts as OUT (Odoo vacuums zero quants — test_catalog locks it).
- Cost is GONE from app surfaces (`ProductOut`, drawer, PO drawer, catalog sort —
  an ordering key is a read by another name) but `products.cost` and the frozen
  `suggestion_json` are UNTOUCHED: the engine's margin rule needs cost, and the
  India export still writes UNIT COST / MARGIN / PROFIT LOST BY AIR (that
  spreadsheet goes to Coimbatore; cut those columns only on an explicit ask).
  `ordering/router.public_suggestion()` is the single door that strips `COST_KEYS`
  from every suggestion sent to a browser — "not rendered" is not "not sent".

### Transfers (floor requests → warehouse → delivery → count)

The warehouse works in Odoo and always will — the app follows them (full rationale
in DECISIONS.md 2026-08-17).

- **Flow** (`flow.py` TRANSITIONS; status KEYS unchanged, labels are "Seen by
  warehouse" / "Staged" / "Received"; the stepper drops `counting` unless that's
  where you are): requested → working_on_it → sent → counting → done, + cancelled.
  The placement draft is rendered AT request creation (the picking name becomes
  the order's identity) and targets **Staging2** (`service.placement_dest_key`,
  falls back to floor staging where staging2 isn't mapped).
  `service.warehouse_has_acted` = state != draft OR write_date > create_date by
  >3s (absent date fields read False — "no information" must never read as
  "somebody edited it").
- `poll_outbound_status` (throttled via `picking_checked_at`): warehouse actions in
  Odoo drive the workflow — confirmed/assigned → working_on_it, cancel → cancelled.
  A validated picking's DESTINATION decides the rest: floor staging (direct path,
  `service.landed_at_floor_staging`) → sent + per-request count prepared
  (`mark_sent` prepares a count ONLY on this path — two staging→floor mechanisms
  would move the same units twice); staging2 → SENT only ("waiting for the
  pallet"), count deferred to the delivery. Sent quantities sum across the whole
  validated picking family sharing the request's ILAPP-TR- reference
  (`service.outbound_family` — done pickings only, floor-bound receipts excluded,
  or a split double-counts).
- **Delivery form** (`delivery.py` — build on it, never around): the warehouse
  declares what one staging2→floor-staging pallet carries. `candidate_pickings`
  (recent pallets; `?search=` matches a name ANYWHERE in Odoo — the "Don't see
  it?" path; contents via `picking_contents_bulk`, ONE stock.move read for the
  whole list — per-picking reads were ~3s of nothing), `suggest_requests` (EVERY
  linkable request; `suggested=False` ones hide behind "Add another transfer…";
  auto_select = staged AND its items are on the pallet), `discrepancy_review` (per
  PRODUCT summed across selected requests, |sent − asked| >
  `transfer_discrepancy_threshold` (3); products nobody asked for return as
  `extras` — information, never a question), `allocate_sent` (pure FIFO,
  unit-tested: oldest request filled first, surplus to the last asker), `declare`
  (links, freezes contents, writes qty_sent back, saves reasons, lands it if the
  picking is already done). Reasons = `DiscrepancyReason` (no_stock, full_case,
  another_transfer, other — `other` needs a note), ≥1 per flagged row enforced
  server-side in `validate_reasons`, and the server RE-COMPUTES the review on
  submit so a stale dialog can't sneak a gap through. **An UNDECLARED pallet
  closes nobody's request** — `poll_manual_pallets` records it with its contents
  ("needs details"); guessing would close requests still waiting.
- The delivery's count = `prepare_count_transfer(allow_foreign_source=True)` — the
  pallet is usually a picking the app didn't create; `copy` writes nothing to the
  source and the copy keeps the ILAPP-CNT- reference. Pallet-vs-count differences
  are LOGGED (`_log_count_differences`) — the adjustments queue is gone
  (2026-08-24, table dropped in `b2d94f7c8e13`); the validated count picking in
  Odoo and the request's DISCREPANCY event are the records. One count per
  delivery.
- **Close-out**: ONE public `poll_close_out` takes the throttle stamp once and
  runs both closers (count picking matched by id first — the deliberate count —
  floor receipt second). **Never take the stamp inside a closer**: two pollers
  sharing a stamp and each taking it starved whichever ran second, forever (the
  count closer was dead for days on the hosted stack).
  `test_count_validation_survives_a_real_throttle` keeps a REAL 600s throttle and
  is the control for the class — the suite's `ODOO_COUNT_POLL_SECONDS=0` is
  exactly the setting that hides a stolen stamp. `find_received_pickings` matches
  origin in (TR ref, CNT ref) and excludes both app pickings — the floor sometimes
  duplicates the count transfer rather than the placement.
- The list/detail GETs host the pollers (≤8 polls per refresh); the UI polls ~4s,
  food-POS style. `GET /transfer-requests/coming-soon` and `/staging2` are
  declared BEFORE `/{request_id}` — route order matters.
- Pallet actions (`pallet.py`): `staging2_snapshot` (LIVE quant read, snapshot
  fallback), `create_pallet` (ONE draft via create_internal_transfer,
  staging2→staging, ILAPP-PLT-, lines frozen on `pallet_transfers`), pallet
  validation → every SENT request with count none/failed gets its count prepared.
  `/staging2`: warehouse nav (floor may view; only warehouse sees Send-all).
  Staging2 counts in org OOS scope + ordering on-hand, NOT in bwhse scope (it's
  committed to the floor).
- Board affordances: right-click → Duplicate (detail fetch → the usual /new
  prefill, which REPLACES the open draft like every prefill) and Cancel request
  (confirm; offered only for requested/working_on_it — past "sent" the stock has
  moved). TransfersPage tabs are URL-driven (/new · /past; bare path opens New)
  because the bubble keys its burst off the path; roles that can't create
  (warehouse, floor_rotating) get the board with no tabs.
- Admin repair actions (both preview-first, both REFUSE when Odoo doesn't answer —
  rewinding a real count, or deleting rows while telling a human to cancel drafts
  the app could have removed, is the worse error): `delivery.release_stale_counts`
  (pre-form leftovers → sent, count cleared; a count Odoo reports done is LEFT
  ALONE; `test_release_refuses_to_guess_when_odoo_is_unreachable` is the control)
  and `transfers/reset.py` reset-flow (typed-CLEAR confirm; deletes requests older
  than keep_hours + ALL pallet rows, unlinks only app-created pickings still in
  DRAFT, reports everything else with deep links, and stamps the **`discover_from`
  watermark** into `manual_pallet_poll_state` — without it discovery re-adopts the
  same pickings next poll;
  `test_flow_reset_clears_the_rubble_and_discovery_starts_after_it` is the
  control). Three honest outcomes per picking, kept apart on purpose: Odoo not
  answering = REFUSAL (`ResetError` → 422, nothing deleted); an id a successful
  read doesn't return = `already_gone` (never a goose chase); present and not
  draft = `leftover` with its real Odoo state.
- The app cancels nothing in Odoo beyond its own drafts — that would be a new
  write op needing flag + canary.
- `PUT /transfer-requests/{id}/lines` and `PUT /center-orders/{id}/lines` are
  LIVE, TESTED endpoints with no UI yet — unbuilt front ends, not dead code. The
  detail page points at cancel-and-re-raise as the real path.

### Restock, floor asks & suggested items

- `app/restock/engine.py`: ILscripts accumulator; thresholds in Settings
  (`restock_*`); `products.restock_exclude` keeps non-retail POS items (Meals,
  CX900-FLOOR) off every list, admin-togglable. Sales fold is lazy on read, each
  calendar day exactly once (`restock_fold_state`); **the fold stops at
  `min(yesterday, sales_covered_through(db))`** — folding a day the sales sync
  hasn't loaded burns it permanently (`folded_through` is never rewound;
  re-folding would double-count; days already burnt stay burnt). No sales
  SyncState at all = fixture/demo data, folds as before. Restock counts
  `SHOPPE_CHANNELS` = (shoppe, pos-legacy) only — center/campus POS sales must not
  inflate floor restock.
- `reset_floor` (`POST /restock/floor/reset`, floor/warehouse) = "floor fully
  stocked": clears floor lines, zeroes accumulators, `folded_through=today` (today
  gets amnesty; who/when shown on the page). `expire_stale_lines` stamps
  `restock_lines.expired_at` after `restock_line_max_age_days` (7; 0 disables) —
  the row is KEPT (the record of what was asked and never done) but leaves the
  list, and `_flag` skips expired rows so the next crossing starts a fresh line
  with an honest quantity.
- **The restock GET is its own refresher** (no worker on the hosted stack):
  `claim_stale_refresh` + FastAPI BackgroundTask AFTER the response
  (`refresh_domains_in_background`); budgets `restock_refresh_stock_seconds` (300)
  / `restock_refresh_sales_seconds` (1800). **Fixture mode refreshes nothing** — a
  simulator sync would overwrite seeded demo/test data (that check is why
  test_restock passes; under TestClient the background task runs synchronously).
  The page polls at `BOARD_POLL_MS` and shows "Shelf counts updated N min ago"
  from `meta.stock_synced_at`.
- **Grouping + rank** (`grouping.py`): barcode PREFIX → aisle group (IN →
  Incense), because the Odoo category is too coarse to walk a shop by. **CA never
  names a group** (`NEVER_GROUP`; the PUT 422s on it) — two letters + ten digits
  is an India import reference, it says where a thing shipped from, not what it
  is. EX/CX/WC/ME deliberately unmapped (verified spread across unrelated
  categories); unmapped falls back to Odoo category, then "Other". Defaults in
  `PREFIX_GROUPS`, overridable via the `restock_groups` AppSetting (blank label =
  stop grouping). `popularity()` sums 90d `sales_daily` on SHOPPE_CHANNELS only (a
  city-center hit must not reorder the shop's shelves); the FLOOR list is sorted
  server-side (group total desc → item units desc → name — stable with no sales);
  **the BACK list keeps worst-cover-first** — /suggested-items and the transfer
  strip depend on that order, never re-rank it.
- FloorItemOut carries `bwhse_qty` (the row shows floor-only; the number is there
  so the transfer draft quotes an honest warehouse figure — test_restock asserts
  it).
- **Check-offs are also the gamification ledger** (2026-09-01): BOTH lists
  write `restock_checkoffs` (floor checks mirror into `list_type="floor"`
  rows — floor LINES are wiped by the reset, so they can't be the ledger).
  The unique (day, list, product) key means toggle-spam can't inflate a
  tally; unchecking removes only TODAY's credit
  (`test_check_offs_feed_a_durable_personal_tally`). Both check endpoints
  return `my_restocked_total`; milestone thresholds are a UI opinion
  (`restockCheer.MILESTONES`). The celebration layer is optimistic and purely
  decorative — data first, always: `restockCheer.ts` (pure, tested: aisle
  finished / list finished / milestone), `src/sound.ts` (Web Audio synth, no
  asset files; localStorage `ilops_sounds`, default ON, toggle in
  Settings→Appearance; `flyToBubble` plays the whoosh so ONE call site covers
  every add gesture; every entry point try/caught, silent on hidden tabs) and
  `src/celebrate.ts` (canvas confetti, no-op on reduced-motion/hidden).
  **Never gamify counting or purchasing** — rewarding speed or volume where
  numbers move real stock is how counts go wrong; restock check-offs write
  nothing to Odoo, which is why they're the safe playground.
- **Floor asks** (`app/floor_requests/`): FloorRequest = product, qty, note,
  status open|picked_up|dismissed. POST = floor_rotating + shoppe_floor; resolving
  = shoppe_floor. **Every ask is its own row** — two people flagging one shelf are
  two entries (who noticed and how much each wanted is the information); the
  suggested row says "N other people have asked" so adding both doesn't silently
  double the pull. Nothing here touches Odoo. Dismissing an ask is FOR GOOD (a
  person judged it); a computed suggestion only snoozes a week
  (`suggestion_snoozes`, `POST /restock/back/{id}/snooze`) — the numbers will say
  the same thing tomorrow.
- `/request-items` (Floor Team) and `/suggested-items` (Inventory Flow Manager)
  both feed the SHARED transfer draft (same store, same gestures; the bubble reads
  "Item request" for asks and lands on /request-items — `canTransfer` vs `canAsk`).
  The suggested page and the transfer form's `SuggestedStrip` show **people
  first** ("Floor Team Requests" above "Database Suggestions"); taking an ask
  resolves it to picked_up so the asker sees it landed; "Add all" pours into
  whatever draft is open (no navigation, no replacing a half-picked draft). The
  suggested page is deliberately BARE — headings + provenance chips, no blurbs, no
  counts. The strip renders NOTHING at zero total, is expanded when the draft is
  empty, a disclosure once you've added something; five slots then "See all N →" —
  slot maths in `suggestedRows.ts` (pure + tested, own module — a non-component
  export from a component file breaks Fast Refresh). No flyToBubble on that route
  (the bubble is hidden there; the item appearing in the list IS the
  acknowledgement); tap-only — swipe-left would mean snooze, too big a commitment
  mid-request. Opening New transfer freshens shelf counts (the strip calls
  GET /restock, which is its own claimed refresher). The nav carries a red dot
  while any ask is open (`useFloorRequests({enabled})`; `overflowDotted` follows
  it into the More sheet).

### Inventory counting

- **The ONE door for counted numbers** (Noah, 2026-08-24): the product drawer's
  floor-count and the OOS board's mark/"back in stock" flows are REMOVED —
  a counted quantity enters the app only through this page, so every count gets
  the warnings, history and guards below. `oos/adjust.py` survives as
  counting's apply core (the reduction/addition writer ops serve it alone:
  "USA-III: Inventory Adj Reduction" / "…Adj  Adding Qty" — the double space is
  the live name; `ODOO_REDUCTION_PICKING_TYPE` / `ODOO_ADDITION_PICKING_TYPE`).
- ONE nav destination: `/inventory-count` + `/count-review` share a Count·Review
  tab bar (`countingTabsFor(roles)` — warehouse sees Count only, an
  inventory_wrangler Review only, shoppe_floor/admin both; a lone tab renders
  no bar).
- Schema shaped by the hardest rule — **never overwrite a previous count**:
  `inventory_counts` (a submission: one location, one moment) →
  `inventory_count_items` (one product, the unit of review, UNIQUE per submission
  so "add it again" means "change the quantity") → `inventory_count_entries` (ONE
  act of counting; attempt 1 original, 2+ recounts, append-only). `odoo_qty` lives
  on the ENTRY (a recount days later compares against Odoo THEN; the reviewer
  needs both numbers as they were); `odoo_qty_source` records live-vs-snapshot.
  `inventory_count_events` is the review trail. Note the unique constraint is per
  SUBMISSION — near-simultaneous submissions can still both contain a product;
  that's what the warning + apply-time guard below exist for.
- Counting roles: shoppe_floor, floor_rotating, warehouse, admin; reviewing:
  shoppe_floor, inventory_wrangler, admin (`locations.COUNTER_ROLES` /
  `REVIEWER_ROLES`). Locations = the four synced roots + **SHIP** (deliberately no
  OdooLocation row — resolved by complete_name; the Warehouse Team's default spot,
  floor for everyone else). The Odoo qty is a LIVE subtree quant read (BWHSE is
  hundreds of bins; StockLevel has no `ship` key), snapshot fallback with honest
  `source`. **The server re-reads at submit** and freezes it on the entry — a
  browser is not a trustworthy source of the evidence a reviewer judges against.
- Status is never set directly: `flow.roll_up` derives it from items (mixed
  outcomes tell the truth; an outstanding recount wins the label);
  `flow.queue_rank` puts recounts first; bulk actions skip anything already
  decided — an individual decision outranks a group one; reasons mandatory on
  reject/recount (`flow.check_reason`), server-side.
- **Approve = re-read Odoo first** (`service.read_baseline`, BEFORE the decision
  is recorded — `flow.can_review` allows review only on open items, so refusing
  first is what keeps the Request-recount button available). Outcomes: live ==
  counted → **settled**, approved with nothing to adjust ("Odoo already shows N");
  live == captured → apply the original difference; live moved → **`ledger.py`
  asks WHY** (stock.move.line since the count, classified by
  `stock.location.usage`): drift explained entirely by sales/transfers/receipts →
  APPLY (real movement leaves the counter's finding untouched; reason recorded on
  the item's history); ANY inventory-adjustment correction in the window → REFUSE
  422, item stays open (that discrepancy was fixed once already — applying it
  again subtracts it twice; this is the 2026-08-22 double-apply bug, three
  products landed on numbers nobody counted); unexplained residual or Odoo silent
  → refuse (not knowing ≠ knowing it's fine). The correction test is "did any
  happen", NOT "do they net to zero" (+2 then −2 nets to nothing while meaning
  corrected twice). A snapshot fallback is NEVER drift (`drifted` requires
  `source == "live"` — blocking on a stale sync figure would stop approvals every
  time Odoo hiccuped). Bulk approval SKIPS a stale row rather than 422-ing the
  batch, and says so in the submission event.
  `test_two_counts_of_one_shelf_cannot_both_be_applied` reproduces the bug.
- Applying runs the shared `oos/adjust.reconcile_floor_count` core (one copy of
  the delta/ceiling/writer dance, `location_odoo_id`-aware — a count can be taken
  at SHIP or in the warehouse). `apply_to_odoo` catches WriterValidationError and
  records `picking_status=failed` instead of 422-ing — an approval is a DECISION
  and must not be lost to an unmapped location or an off flag.
- **Posting** (`validate_adjustment` — the one exception to draft-only): two
  guards, both load-bearing — the picking's origin must be app-prefixed AND its
  picking TYPE must be one of the two inventory-adjustment types, because
  ILAPP-CNT- is ALSO the prefix on staging→floor count transfers (a
  reference-only sweep would post pallets nobody counted; live: 58 CNT pickings =
  49 adjustments + 9 transfers;
  `test_validate_adjustment_refuses_anything_that_is_not_an_adjustment` is the
  control). Backorder wizards are REFUSED, not confirmed — a short quantity is a
  question for a person. A failed post keeps the approval AND the draft (worst
  case is the old behaviour). Config lookups are cached per process
  (`clear_adjustment_caches()` in the `db` fixture) — re-resolving per record 429s
  Odoo's proxy. `backend/scripts/post_count_adjustments.py` (dry-run default) is
  the catch-up for pre-flag rows.
- **"Just counted" warning** (`recent.py`): who else counted this here lately.
  Two weights — **unapplied** counts WARN regardless of age (still going to move
  stock against the old number; "applied" means ODOO HEARD IT — `picking_status`
  validated/none — NOT reviewer-approved: with posting off an approval is still a
  draft; `test_a_settled_count_is_context_not_a_warning` locks that);
  **applied** counts are context and fade after `RECENT_DAYS` (7). Rejected
  counts are neither. Surfaces: `POST /counts/stock-at` returns `recent` (shown on
  the counting row the moment a product is added, before a number is written) and
  `ItemOut.also_counted` (red, above Approve). `recent.for_items` = one query per
  location, excluding each item's OWN submission. Advisory on both sides — a
  genuine recount IS a second submission.

### Coming soon & availability

- **The out-of-stock PAGE and the `/oos` endpoint are GONE (2026-09-01, Noah:
  redundant)** — the restock list already computes what the floor is missing,
  and counting owns corrections. What remains: `app/oos/adjust.py` (counting's
  apply core — the package survives for it) and the read-only
  `app/availability/` lists (`/availability/oos?scope=`, `/coming-soon`,
  `/meta`), which feed the warehouse /incoming page and the bot API.
- **Availability OOS lists hide never-stocked items by default**
  (`oos_items(include_never_stocked=)`, scope-aware: no snapshot ever showed stock
  in scope). On live that's 1,271 of 1,652 org rows and 1,240 of them SELL — they
  are HIDDEN, not blacklisted (the sweep's never-sold guard stands;
  `test_blacklisted_products_leave_availability` keeps the blacklist filter).
  The bot API stays curated and read-only. `last_in_stock_on` comes from
  snapshot history.
- `/coming-soon` is its own nav destination again ("Coming soon", admin + floor
  roles): pending IncomingMove ∪ discovered staging-bound Odoo pickings ∪ active
  transfer requests (dashed chips, "Odoo · state" badge), soonest-first. The
  subtitle is Noah's wording. `useOnTheWay()` + `OnTheWayChip` reuse this
  aggregation on the transfer form (chips on search results and draft lines + a
  summary above Send) — ADVISORY, never blocks and the API still allows a second
  open request for the same product: a shelf that cleared at lunchtime is real
  and the floor knows it before the numbers do.
- `pages/shared/SectionTabs.tsx` (the URL-driven tab-bar merge pattern) now
  serves only the counting destination (`countingTabs.ts`).

### Center orders & notifications

- `app/center_orders/` — flow: pending → approved → shipped / rejected / cancelled
  (SHIPPED is service-only, polled via `picking_checked_at`; list/detail GETs are
  the listener, UI polls ~4s). `catalog.py`: a center's menu = granted order lists
  ∪ `dept_orderable` products for departments-zone centers; availability/OOS
  labels from StockLevel + IncomingMove ("expected back mid-August").
  `reasonability.py` (UI label "Order Notes" — a real UI-only rename; backend
  module/API fields/testids keep `reasonability`): pure rules vs the center's own
  approved-order history + optional Anthropic polish that can only escalate —
  advisory, never blocking.
- Approval → `create_internal_transfer` with `dest_odoo_location_id =
  center.odoo_location_id or 0` — 0 not None, so unmapped FIELD centers get the
  actionable writer error; all-untracked or locationless dept orders take the
  honest `picking_status="none"` path (the department water flow, not a failure).
  Centers map to `III/CityCenter/<City>` by leaf-name match, REBUILT every stock
  sync.
- **Department QR ordering**: `GET /centers/{id}/order-qr.png` (admin; segno, via
  downloads.py) encodes `{app_public_url}/place-order?center={id}` and NOTHING
  else — a bookmark, not a credential (everyone at III has an ishausa Google
  account; without that this would need a kiosk-token credential class that
  deliberately doesn't exist). The route is `/place-order` (`/order` is a detail
  page). Signed-out landing works via `auth/returnTo.ts` — destination parked in
  sessionStorage (router state can't survive the OAuth hop), consumed once by
  LoginPage; `safeReturnPath` refuses anything but a single-slash same-origin path
  (an open redirect straight after login is how a scanned QR becomes a phishing
  page). `?center=` is an OPENING pick only. The poster lives in CenterEditDialog
  (link, copy, PNG via `apiBlobUrl`).
- `app/notify/`: outbox enqueued in-transaction, inline best-effort delivery
  post-commit, worker sweep with 2^n-minute backoff. Gate ladder: NOTIFY_ENABLED →
  channel flag (`notify_whatsapp_live`/`notify_email_live`) → configured; gated
  sends recorded SIMULATED and logged on the order timeline. **WhatsApp is ON
  HOLD** (code intact; Dev Tools says so; email carries notifications).
- The demo seed gives the coordinator a role in AUSTIN's zone and floor@ the
  dept-approver add-on — coordinator pings and e2e approvals depend on both;
  don't "simplify" either away.

### Purchasing (India + domestic)

- `app/ordering/engine.py` + `forecasting.py` are PURE and parity-locked:
  `tests/test_workbook_parity.py` reproduces all 281 numeric SEA rows of
  `docs/reference/USA INV CHK.xlsx` (committed — the one tracked xlsx). **Never
  "fix"** the `max(0, oh − demand + incoming)` recurrence or the `ceil_to_case`
  epsilon. Forecast = per-month MOH multipliers on the same projection; a flat
  forecast ≡ workbook EXACTLY; every parity fixture passes `forecast=None`
  (`test_a_flat_forecast_is_still_exactly_the_workbook`).
- Forecast refinements: `Forecast.forward_level` (deseasonalised expected rate)
  prices months of cover as `cover_rate` in the sea/air conversions — with the
  trailing average, a rising forecast depleted faster than the order bought
  (measured 23% short on a +30% forecast). Safety stock: `safety_z` (default 0 =
  OFF, the workbook's behaviour — what keeps parity meaningful) + `safety_max_moh`
  add `z × sd/avg × √(sea_lead + target)` months. `_seasonal_indices` shrink
  toward 1.0 by `k_obs/(k_obs + seasonal_shrink_k)` — at 24 months an index is the
  mean of TWO observations and over-confidence compounds across a year-long order.
  `baseline_*` columns stay on the workbook's own terms (flat, trailing, no
  safety) — their job is to be COMPARED on the review screen. Flags include
  `new_product` and `FLAG_NO_DEMAND` (sat in stock, sold none). Review findings 05
  and 07–10 are open by choice.
- Rules = `rules.py` defaults merged with the `ordering_rules` AppSetting.
  **Months of cover is one control** (`rules.coverage_overrides`,
  `GET/PUT /ordering/coverage`, field atop the purchasing settings dialog). The
  trap it exists for: `target_moh_for` reads `category_target_moh` BEFORE
  `default_target_moh` and every category has an entry, so setting the default
  alone silently under-orders — the helper writes the default AND every category;
  `coverage_of()` returns None when targets differ so the UI says "mixed" instead
  of lying with one number. Deliberately NOT folded in: `expiry_max_target_moh`
  (6 — a year of Bloom expires first; the cap applies after the target),
  `air_only_min_moh` (6 — a cash call), `bulk_cycle_target_moh` (12; the engine
  takes the max anyway), lead times/horizon (they say WHEN a container lands, not
  how much to buy). Defaults in code are UNCHANGED so parity stays green; the real
  year lives in the AppSetting.
- Import candidates = active + stock-tracked + odoo, not clothing, not
  `ordering_exclude`, not blacklisted; sourcing tag domestic = hard exclude
  (outranks the uploaded list, like the domestic-vendor rule), india tag =
  candidate regardless of reference shape, untagged = the `^[A-Za-z]{2}\d{10}$`
  pattern / vendor rules. On-hand sums bwhse+floor+staging+staging2. Sales from
  `sales_monthly` SPARSE — only selling months (velocity per in-stock month,
  workbook semantics), current month excluded. When the `india_product_list`
  AppSetting is present (original file b64 inside for byte-identical download;
  PUT/GET/DELETE /ordering/product-list), `import_candidates(restrict_skus=…)`
  treats it as authoritative and skips the pattern check.
- The review table IS the draft order: `suggestion_json` frozen per line,
  overrides move `final_*` and log qty_change events. Post-place state moves ONLY
  via `timeline.apply_event` (new kinds go in OrderEventKind + apply_event, never
  inline). Placement stores CSV+XLSX OrderAttachments, creates sea/air legs,
  emails via gate ladder NOTIFY_ENABLED → `ordering_email_live` (ships OFF) →
  SMTP; recipients live in the `ordering_email` AppSetting, NOT env.
- `parser.py`: LLM extraction with verbatim-quote enforcement, heuristic fallback
  (the dev/test path — e2e and the acceptance email depend on it); proposals
  human-confirmed; `product_hint` is matching scaffolding stripped before apply.
  Mailbox ingest (`mailbox.py`, worker `_poll_mailbox`) is READ-ONLY IMAP,
  last-UID in the `ordering_mailbox_state` AppSetting, matches In-Reply-To then
  ILAPP-PO- token, ignores everything else; blank IMAP_HOST = no-op (paste replies
  via `POST /ordering/orders/{id}/ingest-email`).
- Domestic: products get `vendor_id` + `moq` (one vendor per product, 409
  otherwise; roster via `/ordering/vendors/{id}/products`); same engine
  (`is_domestic` → MOQ-when-below-4 rule); Quick order per vendor, `send:true`
  creates AND places in one step (plain "reply with an invoice" email — no sea/air
  language anywhere domestic). `US-` fixture codes are the domestic demo pool.
- ForecastAnalogy: one per product; LLM/heuristic SUGGESTS, human confirms;
  graduates at ≥6 real months, checked at draft generation.
- Frontend: /purchasing is TABBED India | Domestic (+ /vendors, /:id); draft
  review sorts the FULL filtered set BEFORE pagination (never the page slice —
  QuickOrder sorts too); sell-through chips + help tooltips explain
  forecast-vs-flat divergence; downloads go through `apiDownload` (bearer in
  header, never URLs); e2e phase4 needs `ordering_email_live` OFF.

### Centers & the map

- `/centers` (admin): map on desktop (`hidden lg:block`), list alone on phones.
  **The geography is committed, never fetched** (the deployed CSP allows no
  external host — tiles were never an option): `backend/scripts/build_map_geo.py`
  projects Natural Earth (inputs NOT in repo, URLs in the script) through an
  Albers conic, simplifies in PIXEL space (the projection's units are ~1.0 for the
  continent — a tolerance in them is meaningless), writes
  `frontend/src/pages/admin/mapGeo.ts` (~70KB) with a `project()` that is the SAME
  maths so a lat/lon lands exactly where its state does. **Watch the y sign** —
  the conic's y grows north, SVG's grows down, and an upside-down continent looks
  plausible enough to ship (it did, for one screenshot). Positions from
  `app/centers/geo.py` (name-keyed gazetteer, state-centroid fallback); all points
  pass point-in-polygon vs their own state; NY and St. Louis are nudged inland off
  simplified borders.
- Hue carries the four FIELD zones and nothing else (`--zone-1..4` — the dataviz
  validator says exactly two 4-hue sets clear all-pairs in both modes and no 5-set
  does); Canada needs no hue, III Departments is ONE campus glyph, unzoned is
  grey (an absence, not an identity). Dot AREA = last month's units (sqrt scaling;
  a zero-sales center still draws at `R_QUIET` — "exists and did nothing" is
  information). Trend ▲/▼ compares the last two COMPLETE months
  (`app/centers/sales.py comparison_months` — the in-progress month would show
  every center collapsing on the 3rd); <5% = flat, no baseline = "first" (never a
  percentage), unseen = null not zero; the arrow is the encoding, colour only
  seconds it (CVD/greyscale-safe). Dormant centers are tinted RINGS (a solid fill
  looked like a hole in the map). Pure signal maths in
  `pages/admin/centerSignals.ts` (own module — Fast Refresh; `zoneColors`/
  `zoneSwatch` live there so list + map call one function).
- Interaction traps, all learned live: pointer capture is taken LAZILY on the
  first move past `DRAG_SLOP` (capture on pointerdown retargets the click and dots
  stop being clickable — verify with a real mouse click; a synthetic
  dispatchEvent bypasses capture and passes against the broken build); wheel zoom
  needs a NON-passive listener (React's onWheel is passive), nudges an exponential
  TARGET (`exp(-deltaY * 0.0016)`, deltaMode normalised) eased by rAF, and commits
  35% synchronously (rAF is throttled in background tabs); selecting a dot must
  not scroll the page; setPointerCapture is try/caught.
- `GET /centers` carries `reviewers`/`requesters` display names built by
  `_people_index` in ONE query (per-row asking is 60 round trips).
  `GET /centers/{id}/detail` adds roster contacts and a **live** quant read of the
  center's own location (deliberately not synced — 4 synced locations vs 54 for an
  occasional panel is a bad trade); `stock_status` is ok/unmapped/unavailable,
  honest (10 of 62 centers have no mapped location). `PATCH /centers/{id}` with
  `contacts` REPLACES the whole roster (the editor shows all of it; a partial send
  would silently drop people); `clear_zone` exists because null can't say
  "unassign"; blank contact rows aren't people. CenterEditDialog can invite an
  Order Requester scoped to the center (existing `POST /admin/users`). Roster
  import takes a FILE (`POST /admin/import/coordinators/upload`, multipart, 8MB
  cap, temp file deleted in `finally` — it carries every coordinator's phone
  number); `read_sheets` handles xlsx/csv; a CSV is ONE sheet so its Zone column
  carries what the workbook did with tabs (note: `_col` fuzzy-matches headers, so
  "Zone Coordinator" resolves to "Zone" and only the zone-CODE path runs —
  harmless for the real file, confusing for fixture authors). The old no-file
  endpoint stays for the seeded/local path.

### Reports & bot API

- `app/reporting/`: monthly aggregates only (`queries.resolve_period`; packed
  (year,month) BETWEEN); breakdown dims category|product|channel|center; scope
  tabs `SCOPE_CHANNELS` (all | in_person = shoppe+pos-legacy+campus_other | online
  | city_center). `orders_summary`: AOV/orders/new-customers period-exact via
  first_seen; returning share uses the latest COMPLETE month, never a half-month;
  distinct-customer counts are per-month only — the rollup has no partner dim to
  dedupe across months, don't pretend otherwise. Narrative + Q&A follow the
  inline-anthropic pattern (json-schema output, heuristic fallback without a key,
  source labeled model-id-or-'heuristic', cache per (period, facts-hash) in the
  `reports_narrative_cache` AppSetting). Chart series colors are the
  `--chart-1..4` tokens, fixed identity order shoppe/online/city_center/
  campus_other; new-vs-returning is emphasis form (returning wears the hue, new
  wears gray); the chart's Table toggle is the contrast-relief channel — keep it.
  Order size is a display NUMBER (period AOV + prior), not a chart.
- **Time machine is fully REMOVED (2026-08-24)** — module, endpoints
  (`/time-machine`, `/bounds`, the admin backfill), worker hook, settings and
  tests (the page had gone 08-11; nothing consumed the endpoints). The DATA
  stays: `stock_snapshots`/`stock_snapshot_days` (incl. `source='reconstructed'`
  rows the backfill wrote) still feed the drawer graph, OOS history and
  never-stocked logic — don't touch those tables when cleaning further.
- Bot API (`/api/v1/bot/*`): X-API-Key == SKUBOT_API_KEY (blank = 503,
  compare_digest), read-only oos/coming-soon/health — keep it read-only.
- `app/ingestion/sources.py` = Amazon/Canada STUBS (interfaces + registry,
  surfaced on Dev Tools) — don't build them; extend `ExternalSalesSource` when the
  day comes.
- Notices inbox (`app/notices/`, bell in both top bars): admin posts, per-user
  read rows, read-all on open — deliberately NOT the notify outbox.

### Auth, security & rate limiting

Full rationale in DECISIONS.md (2026-08-05 remediation). The posture:

- **Config FAILS CLOSED**: `Settings._refuse_insecure_production` raises
  `InsecureConfig` when ENV is outside DEV_ENVS (dev/test/local) and auth isn't
  supabase, or `APP_JWT_SECRET` is empty/published/<32 chars, or CORS has `*`.
  `app_jwt_secret` has NO default; dev fills a blank/published one with a random
  per-process value (dev sessions end on restart — intended).
  `test_config_security.py` is the CONTROL for the class: the original finding
  reached a committed blueprint because nothing failed when the config was wrong.
- **`settings.dev_auth` (dev ENV *and* dev mode) — never `auth_mode` alone — gates
  anything that leaks a login code.** Auth responses are UNIFORM for
  known/unknown/inactive identifiers (a 404 was an enumeration oracle);
  `tests/util.login()` works because dev mode still returns the code for a real
  user. Dev mode also skips the 60s resend throttle (e2e re-logs demo users in
  seconds); real delivery modes keep it.
- **Google OAuth is the production sign-in** (Supabase;
  `SUPABASE_OAUTH_PROVIDERS=google`, OTP off). `/auth/config` advertises
  providers; LoginPage finishes the redirect via `getSession()` → the unchanged
  `/auth/exchange`. **`match_supabase_claims_to_user` links auth_uid ONLY on a
  provider-VERIFIED identifier** (checks `email_verified`/`phone_verified`
  top-level → `app_metadata` → `user_metadata`, first hit wins; the first two are
  Supabase-controlled, `user_metadata` is client-writable; missing/unparseable =
  unverified; an unverified identifier that WOULD have matched raises 403 instead
  of linking). **Don't loosen it — it's the account-takeover path.**
- Sessions revoke via `users.token_epoch` (in the token, compared in
  `get_current_user`; bumped by `POST /auth/logout-everywhere`, role change,
  deactivation).
- Output encoding: `ordering/export._safe_cell` neutralizes formula-leading TEXT
  cells only (numbers keep native types — the files are emailed to Coimbatore, and
  openpyxl turns a leading `=` into a real formula cell); `app/downloads.py` is
  the one file-response door.
- `app/ratelimit.py` is deliberately in-process (one uvicorn process): authed
  limits key on user id, unauthed on IP AND identifier; the entrypoint sets
  `--proxy-headers --forwarded-allow-ips` (never `*`) or the IP key is just the
  tunnel. `RATE_LIMIT_ENABLED=false` in conftest (the suite loops endpoints);
  `test_auth_hardening.py` turns it on deliberately.
- Input bounds: `counted_qty` bounded + `allow_inf_nan=False`; list `limit`
  ceilings (`?limit=-1` emitted `LIMIT -1`).
- CSP: the pre-paint palette/theme script lives in `public/palette.js` (inline
  would need a per-edit hash — keep it in lockstep with tokens.css).
  `Permissions-Policy` must be `camera=(self)` (an empty allowlist disabled the
  scanner site-wide) and `script-src` needs `'wasm-unsafe-eval'` (Chrome refuses
  to instantiate WebAssembly without it) — **in all three policy copies:
  render.yaml, frontend/vercel.json, infra/Caddyfile.**
- `/api/docs` + detailed `/health` are gated behind `is_dev_env`/auth (anonymous
  callers could read `writes_enabled`).
- The coordinator roster workbook left git history — treat as already disclosed;
  rotate the Stripe terminal registrations. It lives in `./private/` (gitignored,
  mounted read-only); `docs/reference/*.xlsx` is ignored except the parity
  workbook.

## Frontend

### Design language & theming

Material Design 3, vibrant and editorial — fun, colorful, quirky at times, always
functional (Noah's call, superseding the early Linear-quiet direction; see
DECISIONS.md). Brand orange `#f36f21` primary everywhere, deep-umber on-primary.
Light mode has **three palettes** — Charcoal Pop (DEFAULT; its values live in the
`@theme` block so `data-palette="pop"` needs no override), Neem Tree, Turmeric Root
(sunflower-gold secondary wears DARK text — gold is a light hue) — as
`data-palette` on `<html>` + localStorage, applied pre-paint by `public/palette.js`,
which validates stored ids and falls back to pop (retired ids can't strand anyone).
**Dark is a SETTING, not the device's** (`ilops_theme` system|light|dark, resolved
pre-paint to `data-theme`; `theme.ts` owns the mode — `currentThemeMode` /
`resolvedTheme` / `setThemeMode` / `watchSystemTheme` wired in main.tsx — and
repaints the `theme-color` metas; ONE global slate-indigo dark scheme, one
`:root[data-theme="dark"]` block per token group — resolving in JS is what keeps it
to one block instead of a media query plus an override). All color roles live in
`frontend/src/styles/tokens.css`; **palette-lab.css + PaletteLabPage mirror
tokens.css — change one, change both.** Each palette themes inverse-surface
(snackbars). Text fields sit on the derived `--color-field`.

M3 anatomy throughout: pill buttons with state layers, tonal containers,
floating-label filled fields, chips, switches, snackbars on inverse surface, an
extended FAB for a page's one big action. Type: Fraunces (WONK on) at
Display/Headline scales for titles (`.display-xl/.display-l/.headline`), Inter for
everything operational. Motion is springy (`--ease-spring`, `.stagger-children`) —
always honoring `prefers-reduced-motion`, never animating inside dense data tables.
**Quirk belongs in safe places** (brand flower, empty states, container colors) —
never in data tables or safety-critical UI. Dense tables where operators live;
consumer-grade flows where volunteers live (the order form is a checkout,
phone-first).

**Silly mode** (`frontend/src/silly.ts`, toggle in Settings→Appearance):
EXACT-match dictionary renames nav labels, titles, brand ("Da Shop"), HEALTHY-only
health chips, empty states (mapped THROUGH EmptyState/PageHeader), search
placeholders (aria-labels stay canonical), section headings. Failure text, confirm
buttons, and table data NEVER get entries ("Sync stale"/"Odoo auth failing!" stay
literal). silly.test.ts FAILS if any role's nav label lacks an entry — new nav
items need a silly name too. Dynamic strings pass through untouched.

### Shell, nav & mobile

- Desktop: navigation drawer with pill indicators. **Phones: a bottom bar for
  every role** — the role's first destinations, then **Scan** (pinned — overflow
  must never eat it), then **More** (sheet). `NavItem.short` is the bar label
  (~70px slots). One round `bg-primary` FAB docked left, breaking above the bar,
  RESERVES a slot's width with a spacer (a floating circle over a tappable label
  is a mis-tap machine): Search for most roles, **Scan for the Warehouse Team** —
  role-chosen, never two FABs; roles with no inventory search (Order
  Reviewer/Requester) get none. The FAB wears `on-primary` (the token with real
  contrast on this orange); active state is a ring, not a fill change.
- `homeForRoles`: admin → /reports, warehouse → /staging2, shoppe_floor →
  /restock (nav.test asserts). Warehouse menu is THREE items (Send to floor,
  Inventory counting, Search Inventory) — they live in Odoo. shoppe_floor nav
  order is Restock · Transfers · Suggested items · Inventory counting · Coming
  soon · Search (the phone bar keeps the first TWO before Scan/More).
  "Inventory counting" is a tab-bar merge (2026-08-24, `SectionTabs`): it
  carries /count-review — a new silly-mode entry is needed if a merged label
  changes. **The search FAB primes the iOS keyboard** (`primeKeyboard` in
  AppShell, 2026-09-01): focus() only opens the keyboard inside the tap
  gesture, and the catalog's autoFocus runs after navigation — so the FAB
  focuses a throwaway 16px input synchronously (16px or iOS zooms instead)
  and the real box takes over on mount; already-on-catalog taps focus the box
  directly.
- Settings is the admin surface (account → appearance → Admin pages card linking
  Users + **Dev Tools** — the old Status page — + styleguide/palette-lab); those
  left the main nav. The audit log is a Dev Tools button. On phones the inbox
  card is `fixed inset-x-3` under the top bar (an anchored 320px popover hung off
  a 375px screen); md+ keeps the popover. The mobile top bar has no brand lock-up
  — the page headline is an inch below.
- "Search Inventory" (`/products` route page): search box takes `autoFocus` and
  seeds from `?search=` (what the scanner's "Search the catalog" passes). Phone
  catalog shows search alone with a "Filter by… (n)" disclosure and a "Hide OOS"
  checkbox (`in_stock_only`); the phone card list is the `md:hidden` twin of the
  DataTable (`hidden md:block`).
- PWA: manifest + apple metas + safe-area insets (`viewport-fit=cover`;
  `pt-[max(0.75rem,env(safe-area-inset-top))]`). `@media (pointer: coarse)` puts
  16px on all controls (iOS Safari zooms any focused control under 16px — no
  maximum-scale hack, pinch stays). No service worker yet — offline vs auth +
  polling deliberately punted.
- `usePersistedState` (`src/persist.ts`) mirrors page state to sessionStorage
  (per-tab/app-session on purpose — fresh launch starts clean; key change
  re-seeds via render-time derived state; `clearPersisted` for submit flows —
  imperative because a setState write effect can miss when navigation unmounts).
  Selections/menus stay ephemeral; list pages reset to page 1 by design.

### Shared interaction patterns

- `api()` (client.ts) **JSON-stringifies the body itself** — passing
  `JSON.stringify(x)` double-encodes and FastAPI answers "Input should be a valid
  dictionary" (caught in the browser, not by typecheck). It returns undefined for
  204/empty bodies (DELETEs) — don't "simplify" that away. `apiDownload` /
  `apiBlobUrl` carry the bearer in a header, never a URL (revoke object URLs on
  unmount). `useDeliveryPreview` is a MUTATION, not a query — it reads Odoo live
  and the answer changes as boxes are ticked.
- `design/SwipeRow.tsx` is **the ONE swipe gesture** (`useSwipeRow` {onLeft,
  onRight, leftExits} + `SwipeBackdrop` + `leavingStyle` — dxRef-not-state commit
  test, axis lock, click swallow, exit-then-mutate; the comments explain why,
  don't re-derive). TOUCH ONLY by design — a mouse gets the row's context menu.
  Wired: restock rows (left = snooze with exit, right = request more), catalog
  phone list (the floor OOS board wore it too, until the page was removed
  2026-09-01). Catalog adds default to case size and refuse untracked/manual
  products. The morph (`morphOnRight`): past `MORPH_START` (0.8 of the row's own
  width, measured on pointerdown) the row collapses toward a `MORPH_BALL_PX` ball
  under the finger — scale from `transform-origin: 0% 50%`, travel capped at
  `w − 38`, `border-radius` in PERCENT (a px radius on a non-uniform scale is a
  squircle), backdrop fades out by the same fraction. Release commits at the 96px
  threshold; a morphed release hands the flight its exact ball box and suppresses
  the spring-back.
- `shell/flyToBubble.ts` is the add-to-draft feedback (the toasts are GONE):
  `buildFlightFrames` is PURE and unit-tested (winds up away, arcs above, lands
  dead on the anchor, no NaN at zero distance); easing is baked into the SAMPLE
  SPACING and the animation runs `linear` — that's what reads as a slingshot; the
  bump fires at `hitOffset` (within 10px of the pill), NOT timeline end (a
  decelerating tail reads as lag). **Data first, always**: `addToDraft` runs on
  release; the flight only decides WHEN the "+N" shows. The anchor waits on a
  TIMER, never rAF (hidden tabs pause frames); `document.hidden` skips the flight
  outright.
- `transferDraft.ts` is the SHARED draft store (external store + `useDraftLines`,
  sessionStorage `transfer.new.lines`). `addToDraft` MERGES quantities on a
  repeat product (the API rejects duplicates within one request);
  `withNewestFirst` (pure, tested) puts the newest/merged row on TOP — appended
  rows land off-screen on phones, and a merge you can't see reads as a tap that
  did nothing (InventoryCountPage prepends the same way). Prefills REPLACE the
  open draft; `clearDraft()` on submit. Consumers: transfer form, restock
  "Request more", suggested items, floor asks, catalog, OOS.
- `TransferDraftBubble` follows you between pages: draggable (position clamped +
  saved in localStorage `ilops_transfer_bubble_pos`; <5px gesture = tap = open),
  NEVER hides (the way to move it is dragging, the way to end it is placing the
  request), momentum coast on release (friction `0.9^(dt/16)`, dt clamped 32ms so
  a backgrounded tab can't teleport it; edges kill that axis), publishes its
  centre via `setBubbleAnchor` on every place/drag/coast; bursts
  (`bubble-burst` + sparks) when you reach the transfer page, which answers with
  `animate-rise-in`. Wears `Icons.truck` for a transfer, `Icons.box` for a Floor
  Team ask. **Drag to the bottom band to clear the draft** (`DropZone`, red under
  the finger; release = clear + UNDO snackbar restoring the exact lines — no
  confirm dialog, the gesture is deliberate and undo is one tap). Traps, all
  learned live: read `overBinRef`, NEVER the `overBin` state (a fast flick puts
  the last pointermove and pointerup in one frame); on phones the bin band
  floats ABOVE the bottom bar + docked FAB (`DROP_ZONE_LIFT_PHONE` + the CSS
  `bottom-[calc(6rem+…)]` move together — it used to hide behind the search
  FAB, Noah 2026-09-01) and the resting inset (150 desktop / 260 phone) is
  deliberately deeper than the band's top so a nudge can't bin anything; the
  burst plays `playPop()` when it fires; measure the pill with **offsetWidth/Height, never getBoundingClientRect** (a running scale
  animation shrinks the rect and parks it off-screen); clear the entrance class
  on a TIMER, not animationend (a backgrounded tab never delivers the event and
  fill-mode `both` then outranks every later motion); placement REFUSES a zero
  viewport (a hidden tab reports innerWidth 0 and clamping parks the pill
  top-left forever); wrap setPointerCapture/release in try/catch (a throw
  swallows the tap).
- `design/ScrollingText.tsx` — long-press a truncated name for an iPod-style
  ticker (restock, OOS, suggested/request items, coming soon, transfer draft,
  catalog phone list, place-order rows, staging2): IDLE is one `truncate` span;
  RUNNING swaps to clipping-outer + `inline-block w-max` inner with TWO copies +
  `TICKER_GAP`, translating exactly `firstCopy.offsetWidth + gap` (that's what
  makes the wrap invisible), linear, two loops. The press target is the whole
  CARD (`data-name-press`, found via `closest()`, NATIVE listeners so the gesture
  coexists with the React pointer handlers those rows already carry — spreading a
  second set would clobber the first; missing attribute falls back to the text).
  iOS: `.scrolling-name` + `[data-name-press]` get `user-select: none` +
  `-webkit-touch-callout: none` scoped to `@media (pointer: coarse)` (Safari
  otherwise starts a selection and pointercancel kills the hold timer; a desk
  mouse can still select to copy), and `swallowNextClick()` eats the click after
  a press (capture phase, 700ms disarm) so the press doesn't toggle the row
  underneath. Reduced-motion gets only the tooltip. Probe note: a RUNNING card no
  longer matches a `scrollWidth − clientWidth` overflow probe (the inner is
  `w-max`) — check for the two-copy layout instead.
- `design/ContextMenu.tsx` — `useRowSelection` (plain=single/toggle-off,
  cmd=toggle, shift=range over the CALLER'S visible order; forContext keeps multi
  on right-click) + ContextMenu + isInteractiveTarget; DataTable has (row,e)
  clicks, onRowContextMenu, rowClassName. Wired: transfer create, place-order
  rows, transfer detail ("New transfer with N items"), PO draft review (plain
  click still inspects — modifier clicks select), Search Inventory ("Edit N
  together…" → `BulkProductDrawer`, Premiere-style: only TOUCHED fields apply —
  tag chips stage add/remove-to-all, expires add blocked, air/sea exclusivity
  enforced, blank = leave, TriPick for restock-exclude/blacklist; per-item PATCH,
  failures counted honestly).
- `ProductPicker`: results stay open after a pick (multi-add; picked rows
  disable — don't reinstate clear-on-pick); Enter adds the TOP result and focuses
  its qty input (value pre-selected), Enter in qty returns focus to search with
  text selected — type/enter/qty/enter loops; a tap opens SetQtyDialog (case-size
  default, number pad, "Add to request") then returns focus to search — the
  phone-in-the-aisle loop. Generic `annotate?: (productId) => ReactNode` slot
  (how OnTheWayChip rides it — the picker never learns about transfers; catalogs
  and vendor rosters pass nothing). The meta row WRAPS and the container is
  `overflow-x-hidden` (a nowrap flex made every picker scroll sideways on
  phones). Results show "case of N"; PickedLine carries case_size.
- Toast durations are per-viewport (`LINGER` in Toast.tsx): phones 2.2s/4s,
  desktop 3.8s/6.5s — but an undo offer keeps its full 7s everywhere (reaching
  the button is the point). `ToastAction` = one M3 action.
- A Toggle must NOT be wrapped in `Field` — the floating label is drawn for a
  text input and lands on top of the switch.
- Product identity on floor-facing lists is `productCode(barcode, sku)` — barcode
  everywhere the floor works; the inspect panel + purchasing keep real SKUs/India
  refs.

### Scanner

`frontend/src/scan/` — top-bar icon (both app bars) → full-screen camera sheet →
exact lookup → the EXISTING ProductDrawer (reuse, not a new result view; closing
returns to the camera — the aisle loop is scan-look-scan). `decode.ts` picks
native `BarcodeDetector` (reads the <video> with zero pixel copy) else
**zxing-wasm** (the only decoder iOS Safari has — "native only" was never an
option); both format-limited to retail + shelf-label symbologies (the biggest
speed lever); the wasm module + ~1MB binary are dynamically imported so the native
path never fetches them. `useScanner.ts`: `enabled` owns the camera stream,
`paused` only stops the decode loop through lookups and misses ("next scan"
stays instant there), but a FOUND item powers the camera OFF — `enabled`
follows `phase.kind !== "found"` (battery + privacy while the drawer is read;
"Scan again" restarts the stream at its ~1s cost — Noah, 2026-09-01); loop on
`requestVideoFrameCallback` (a frame is never
decoded twice); the wasm path decodes a center-band ROI capped at 720px;
check-digit formats (EAN/UPC) accept on one read, Code39/128/ITF wait for two
identical; torch + continuous autofocus where supported. Manual entry doubles as
the USB-wedge path and the camera-refused fallback — `inputMode="text"` +
`autoCapitalize="characters"` (codes like CM233-L aren't digits).

## Testing & verification notes

- Backend: pytest, no Docker needed (`make test-backend`). Frontend: tsc + vitest.
  E2E: Playwright, `workers: 1`, REQUIRES all write/live flags OFF —
  `e2e/global-setup.ts` refuses otherwise (the shared stack's flags are ON; never
  bypass). E2e specs own their data; phase5 is fully read-only; phase2/3
  selectors (aria-labels, testids) are deliberately stable. The counting approve
  path is covered against the simulator only — approving on the shared stack
  writes real adjustments.
- Simulator fixtures ship 2 native pickings (WH/INT/NATIVE1 assigned + a done
  twin) — writer/canary tests count pickings RELATIVE to that baseline, never
  == 0. Fixtures also carry: SHIP location + quants (one folds onto bwhse stock,
  one SHIP-only like live sesame oil), III/Staging2 + quants, a
  Partner-Locations quant (the usage filter's reason — the locations test targets
  odoo product 201 BECAUSE "the first product with an odoo id" made the assertion
  vacuous), and the Kurta price shape (list_price −9 / lst_price 28).
- `conftest` sets `RATE_LIMIT_ENABLED=false` and calls `clear_adjustment_caches()`
  in the `db` fixture. The suite's `ODOO_COUNT_POLL_SECONDS=0` hides
  stamp-stealing bugs — `test_count_validation_survives_a_real_throttle` keeps a
  real throttle on purpose. Don't pin fixture dates against "today"-relative
  endpoint labels (a frozen ETA fixture started failing on its own one August).
- Browser-pane (in-app browser) lessons: computer-tool coords = screenshot px
  (rendered ÷ 2 at 1280×1400); ref-clicks are unreliable on small controls —
  probe with a JS click listener. The pane reports `document.hidden` true and
  freezes the document timeline: spoof `document.hidden`/`hasFocus` before
  concluding animations or polling are broken (TanStack pauses `refetchInterval`
  unfocused — correct for a pocketed phone, zero polls in the pane until
  spoofed). HMR duplicates module-level stores (transferDraft, bubble anchor) —
  hard-reload before believing "the store didn't update"; "Could not Fast
  Refresh" in the console means handlers silently stopped — hard-reload before
  believing a handler is dead. `document.querySelector("svg")` grabs a nav icon,
  not the map — target `svg[role="img"]`. Measure animated elements AFTER a
  timeout and scrub `animation.currentTime` by hand to see paths; measure with
  offsetWidth (a paused pop-in scale makes getBoundingClientRect report 0.6× the
  box).

## Live-state notes (as of 2026-08-24)

- Write flags LIVE since 07-20; counting adjustment + validate flags ON (counting
  posts real adjustments). `ordering_email_live` OFF. WhatsApp on hold.
- Hosted stack: Render backend (no worker — only restock self-refreshes), Vercel
  frontend, Supabase DB/auth, Google OAuth sign-in. Demo-flow rows wiped 07-25
  (`app.seeds.clear_demo`); transfer-flow testing rubble reset 08-18
  (watermarked). One-time catch-ups already run: sales rebuild, count-adjustment
  posting script, stale-count release.
- Known open items, deliberate: ordering review findings 05 and 07–10;
  Amazon/Canada ingestion stubs; `PUT …/lines` endpoints have no UI.
- Feature-merge round (2026-08-24): counting is the one door for counted
  numbers; counting+review is a single tab-bar destination; adjustments queue
  and time machine removed (tables `adjustments` + `floor_oos_marks` dropped by
  `b2d94f7c8e13` — runs on the hosted stack at next deploy). Flags
  `write_create_inventory_reduction` / `write_create_inventory_addition` remain
  in use BY COUNTING's apply core. 2026-09-01: gamified restocking round 1
  (sounds/celebrations/milestones), and the out-of-stock page + `/oos` endpoint
  removed outright — Coming soon stands alone.

## Maintaining this file

The old CLAUDE.md grew by appending a dense narrative paragraph per work round
until it was ~27k tokens of chronology every session had to wade through, with
superseded rules still standing upstream of their corrections. Don't restart that
pattern:

- **Durable rules, invariants and traps** → edit the matching topical section
  here, in place, REPLACING anything they supersede. Name the control test when
  one locks the rule.
- **Why a call was made** (rationale, alternatives, measurements, incidents) → a
  dated entry in `DECISIONS.md` (existing habit — keep it).
- **Round-by-round narrative**, if worth keeping at all → append to
  `docs/HISTORY.md`. Commit messages already carry most of it.
- When a feature is removed, DELETE its section here — history keeps the story.
- Keep this file roughly under 1,200 lines. The test for a line is "does a
  fresh session need this to avoid a mistake?", not "did this happen?".
