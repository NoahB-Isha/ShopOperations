# Decisions Log

A running record of every decision made during the build that isn't already in `CLAUDE.md`.
Add an entry whenever you and Claude settle something mid-session — a schema choice, a UX call,
a workaround for an Odoo quirk, a scope cut. If a decision *changes* something in `CLAUDE.md`,
update `CLAUDE.md` too and note that here.

Format: newest entries at the top. Keep entries short — one paragraph max. The point is that six
months from now, anyone (human or AI) can answer "why is it like this?" without archaeology.

---

## Template

**YYYY-MM-DD — Short title** *(Phase N)*
What was decided, and in one sentence, why. Alternatives rejected, if notable.

---

## Founding decisions (pre-build, from the planning conversation)

**2026-07-09 — Fresh repo; prior projects are reference only**
`ops`, `skubot`, and `ILscripts` inform the design (especially the ordering math and Odoo
session-auth approach) but are not extended. The `USA INV CHK` workbook parity test must be
reproduced — it is the spec of record for India ordering.

**2026-07-09 — Odoo: full access, zero validation**
The app may read and write freely but never validates anything it creates. All created records
stay in draft with an `ILAPP-` reference prefix and a deep link shown in the UI. All writes go
through the single `OdooWriter` gateway with audit logging, dry-run mode, and a kill switch.

**2026-07-09 — No staging Odoo; test via simulator + canaries**
Unit tests against a faked client, integration tests against a recorded-fixture simulator in CI,
and gated `APP-TEST-` canary writes against production (create draft → verify → unlink), triggered
manually only.

**2026-07-09 — Shared Odoo account**
The app authenticates as a repurposed personal account with human activity on it. The app's audit
log is therefore the source of truth; record identification is by `ILAPP-` prefix, never by
account. Auth failures surface loudly on the admin status page.

**2026-07-09 — Stack & hosting**
FastAPI + worker in Docker Compose on an on-campus box, exposed via Cloudflare Tunnel. Postgres
and auth on Supabase (OTP codes to email or phone, fresh code per login). Frontend: React + TS +
Vite + Tailwind, thin internal design system.

**2026-07-09 — WhatsApp first, on the unofficial bridge for now**
Notifications and (later) the bot ride skubot's existing unofficial bridge behind a
`WhatsAppTransport` interface; official Business API is a planned drop-in swap. Bridge
disconnection is an expected condition: auto email fallback + admin health status. Keep outbound
messages template-simple to survive the official API's rules later.

**2026-07-09 — Sync cadence**
One full 24-month sales-history backfill at setup, then small hourly incrementals (current month;
previous month once daily). Stock levels a few times daily or on demand. The app always serves
from its own snapshot.

**2026-07-09 — Clothing is out of scope**
Excluded from all flows. Don't design around it; just avoid architecture that would make adding
it painful.

---

## Build decisions

**2026-08-24 — Feature merge: one door for counted numbers, and four fewer surfaces**
Noah asked what could be merged, then chose the aggressive cuts. The organizing principle:
a counted quantity now enters the app in exactly ONE place — the counting page — because
that is where all the safety machinery lives (never-overwrite history, the "just counted"
warning, the baseline re-read, the ledger classification). The product drawer's
floor-count and the whole OOS marking flow (mark → draft reduction, "back in stock" →
counted reconciliation) bypassed every one of those guards: two people could floor-count
the same shelf from the drawer and land both drafts, which is the 2026-08-22 double-apply
bug on the draft-only path. Rather than teaching three write paths the same guards, two
of the paths were deleted. The OOS board went further than proposed — Noah: "This page
should only show a searchable list of oos items. No marking items" — so the board is
read-only and `floor_oos_marks` is gone entirely; fixing a phantom floor number means
counting the product. `oos/adjust.py` and the reduction/addition writer operations
survive because counting's apply core runs on them.

Also in the round, all Noah's calls: **counting + count review** are one nav destination
(URL-driven tab bar, the TransfersPage pattern, extracted as `SectionTabs`; tabs follow
roles so warehouse sees Count only and an inventory_wrangler Review only — the Inventory
Flow Manager keeps both, explicitly confirmed); **Out of stock + Coming soon** merged the
same way into "Stock status" (they were two answers to one floor question, already
sharing the incoming aggregation; the floor nav drops from 8 destinations to 6);
the **adjustments queue** is removed outright — it had no nav entry since 08-17 and no
reviewer, so rows accumulated unseen; the DISCREPANCY event on the request and the
validated count picking in Odoo already carried the same facts, and the delivery closer
now logs pallet-vs-count differences instead of queueing them. The `adjustments` table
and its historical rows were dropped (Noah's pick over keeping the dead table — the Odoo
pickings are the durable record). And the **time machine** is fully removed: the page had
been gone since 08-11 and a code audit found zero consumers of the endpoints; the
reconstructed `stock_snapshots` rows it wrote stay, still feeding the drawer graph and
OOS history. Routes and testids for the merged pages are untouched — only the menu and
the write surface shrank. Migration `b2d94f7c8e13` (drops `adjustments` +
`floor_oos_marks`; downgrade recreates schemas empty).

**2026-08-18 — Reset to a known point, with a discovery watermark** *(Phase 2.z)*
Noah wanted the two weeks of testing gone — the full board and the 15 undeclared pallets — with
anything asked for in the last 24h kept, and the real process starting from the next pallet. The
non-obvious half is that deleting the pallet rows is NOT enough: `poll_manual_pallets` de-dupes
against the rows it already has, so the same 15 Odoo pickings would be rediscovered on the next
poll and the "needs details" pile would rebuild itself. So the reset also stamps a
`discover_from` watermark that the discovery domain filters on, which is the literal
implementation of "start from the next pallet" — and incidentally stops the app ever adopting
years-old staging2→staging traffic as a pallet needing details. Odoo is treated as not ours to
rewrite: only app-created pickings still in `draft` are unlinked (a draft moved no stock, same
rule as `cancel_placement_draft`, so no new write operation and no canary), while validated
pickings and anything a human made are reported with deep links and left alone. Deleting rather
than cancelling because Noah asked for a clean starting point, not a wall of cancelled rows; the
preview plus a typed CLEAR is the confirmation, since the action is irreversible.

**2026-08-18 — The pre-form leftovers are released, not migrated** *(Phase 2.z)*
Requests already waiting on their own count transfer when the delivery form shipped were
invisible to it (`counting` isn't linkable) while their stock sat in Staging2 waiting for the
next pallet — Noah spotted it on III/INT/04709. Fixed as an admin action with a preview rather
than an Alembic data migration, because deciding each case needs a LIVE Odoo read (a count the
floor really validated must be left to close itself, and rewinding a real count is worse than
leaving a request stuck) and a migration must never depend on Odoo answering; and as an
endpoint rather than a script because the affected rows are on the hosted stack, which has no
shell. The app deliberately cancels nothing in Odoo — that would be a new write operation
needing its own flag and canary for a one-off — so it names each leftover count picking with a
deep link and says why it matters: scanned alongside the pallet's count, the same units move
twice.

**2026-08-17 — The warehouse works in Odoo; a form is how the app learns what they sent**
*(Phase 2.z)*
The transfer flow assumed one app request = one Odoo picking, and closed a request off a
per-request STAGING→FLOOR count. The warehouse doesn't work that way and won't: they pull a
request however suits them (split it, part-ship it, build their own pickings), pile it into
III/Staging2, and send ONE pallet to floor staging in Odoo. Nothing the app can poll knows
which requests that pallet carries, so a human answers three questions instead — which
transfer, which requests, and why any quantity is off by more than 3 units (four reason
chips: not enough stock / sending a full case / it'll go on another transfer / other, with a
note required for "other"). Noah's three calls in the same conversation: the count is now
**one per delivery**, not one per request (the floor counts a pallet once); the app's
placement draft targets **Staging2** so the warehouse stops retargeting it by hand; and a
request on a delivery **always closes**, with a clear per-item note when it wasn't filled —
tracking the remainder is the Inventory Flow Manager's job, not the app's. Consequences worth
knowing: sent quantities now sum across every validated picking sharing the request's
reference (backorders and copies included, receiving pickings excluded, or a split would
double-count); "seen by warehouse" fires on ANY write to the picking, not just a state change
(editing a quantity leaves it in draft and only moves `write_date`); an **undeclared** pallet
lands as a pallet and closes nobody's request, because guessing would close requests still
waiting for their stock; and `prepare_count_transfer` gained `allow_foreign_source` since the
pallet is usually a picking the app didn't create — safe because Odoo's `copy` writes nothing
to the source and the copy still carries our own ILAPP-CNT- reference. Status KEYS were left
alone (same reasoning as the roles rename); only the labels moved — "Seen by warehouse",
"Staged", "Received". Same day, Noah cut the **warehouse menu to two items** (Send to floor,
Search Inventory — the scanner, inbox and settings are top-bar furniture): Incoming,
Transfers, Coming soon, Out of stock and Adjustments left the menu, keeping their routes and
role access so nothing has to be rebuilt to bring one back.

**2026-07-11 — Four switchable light palettes; one global dark** *(Phase 1.5)*
Noah liked all four palette-lab candidates, so instead of picking one they became themes:
Sunset Studio (default), Indigo Violet, Forest & Clay, Charcoal Pop — all sharing the locked
#f36f21 primary and the semantic error/success/warn roles. Selection is presentation-only:
`data-palette` on `<html>` + localStorage (applied pre-paint in index.html), picker in the top
bar and on the "Themes" page. Dark mode stays ONE global slate-indigo scheme for every palette
(Noah's call — no per-palette dark schemes). Text fields moved to a derived `--color-field`
token, lighter than the container ladder, after feedback that form backgrounds were hard to
read on the sand palette.

**2026-07-10 — Palette v2: dynamic color around brand orange #f36f21** *(Phase 1.5)*
Noah's follow-up to the M3 pivot: primary is now brand orange #f36f21 (deep-umber on-primary
for real contrast), grounded by rich indigo secondary and electric teal tertiary; almond-cream
light surfaces and an automatic deep-slate-indigo dark scheme (prefers-color-scheme override of
the CSS variables — no toggle, no state). Typography scaled up to editorial Display/Headline
sizes (page titles are display-large), and motion went springy: `--ease-spring`
micro-interactions, staggered entrances, drifting login blobs, all gated by
prefers-reduced-motion and kept out of data tables. Visual layer only — zero changes to data
flow, state, or component logic.

**2026-07-10 — Design pivot: Material Design 3, fun/colorful/quirky** *(Phase 1.5)*
Noah's call, superseding the brief's "Linear/Notion-quiet" direction: the app now follows M3 —
color roles seeded from the Isha palette (vivid copper primary, peacock-teal secondary, berry
tertiary, warm cotton surface ladder), pill buttons, tonal containers, floating-label filled
fields, chips, FAB, bottom navigation on phones, Fraunces with WONK=1 for display type. Quirk
stays in safe places (brand mark, empty states, container colors), never in data tables or
safety-critical UI. Component APIs kept stable; legacy color names alias to M3 roles in
`tokens.css`. `CLAUDE.md` design-language section updated.

**2026-07-10 — Stock quants match by location SUBTREE; staging is hyphenated** *(Phase 1.5)*
First live sync revealed production reality: staging's `complete_name` is
`III/Stock/III-FLOOR-STAGING` (hyphen, not the space in early notes), and BWHSE stock lives in
hundreds of bin sub-locations (`III/Stock/BWHSE/A/1/1/1`). The stock sync now queries quants
with `child_of` and classifies by path prefix (longest root first); exact-id matching would have
missed most warehouse stock. `ODOO_LOCATION_NAMES` accepts multiple spellings per key. Live
verified: 2,016 product-location rows, ~296k BWHSE units.

**2026-07-10 — Ambiguous roster rows import as inactive, never guessed** *(Phase 1)*
The sheet has two conflicting "active" columns ('?', 'NA', blanks). Resolution: `Active?` wins,
then `Is Active as of Jan 2026?`; anything unresolvable imports as **inactive** with an
`ambiguous_active` follow-up flag. Safer for operations (no orders routed to dead centers);
the Centers admin page surfaces every flagged row. The `canada new` sheet predates the audit
and defaults to active unless an explicit "No".

**2026-07-10 — Zone naming and legacy sheets** *(Phase 1)*
Zones import as "Zone 1 (Lili)" … "Zone 4 (Vivek)" (number and coordinator name both come from
the sheet, and rows carry either) plus "Canada". Sheets `Old` and `Canada (old)` are skipped as
legacy. Continuation rows (blank city) merge into the previous center; repeated cities merge as
extra contacts; same-name contacts dedupe.

**2026-07-10 — Dev-auth mode alongside Supabase mode** *(Phase 1)*
`AUTH_MODE=dev` has the backend issue OTP codes itself and return them in the API response so
the demo runs with zero external services. `AUTH_MODE=supabase` keeps authorization in the app:
the frontend does Supabase email/SMS OTP, then exchanges the Supabase JWT for an app session
token at `/auth/exchange`. One session shape either way (30-day app JWT).

**2026-07-10 — Snapshot tables are replaced transactionally; batches deferred to Phase 4** *(Phase 1)*
Stock/incoming snapshots are deleted+rewritten inside the sync transaction — a failed pull rolls
back, so the last good snapshot survives without the ops repo's batch machinery. Sales upsert by
(product, year, month, channel) window. Batch freezing returns in Phase 4 when orders must pin
the snapshot they were computed from.

**2026-07-10 — Dry-run gate order** *(Phase 1)*
A write renders a dry-run for the FIRST matching reason: explicit request → global kill switch
(`ODOO_WRITES_ENABLED=false`) → operation feature flag off → fixture mode. The audit row records
which gate fired. The canary bypasses only the feature-flag gate (that's its purpose) and still
honours the kill switch.

**2026-07-10 — One simulator for tests, CI, and the credential-less demo** *(Phase 1)*
`OdooSimulator` serves fixture JSONs and implements create/read/write/unlink with draft
semantics. It supports AND-only domains (raises loudly on '|') and dotted paths through an
explicit relation registry — extend the registry rather than working around it. The demo fixture
set (~1,200 products, 24 months of sales) is generated deterministically (`make fixtures`), not
committed.

**2026-07-10 — Sales channels are `pos` and `online`, sourced from line models** *(Phase 1)*
`sale.report` aggregates return nothing on this instance (confirmed in the ops project), so
sales history reads `pos.order.line` (qty; states paid/done/invoiced) and `sale.order.line`
(product_uom_qty; states sale/done), joined to parent orders for dates. City-center activity is
transfers, not sales, so it doesn't appear here.

**2026-07-10 — uv workspace; one image for backend + worker** *(Phase 1)*
Python is a uv workspace (`backend/`, `worker/`; worker imports the backend package). One Docker
image serves both services with different commands; only the backend runs migrations
(`RUN_MIGRATIONS=0` on the worker).

**2026-07-13 — Approval reuses `create_internal_transfer`; no new write operation** *(Phase 3)*
A center-order approval renders the same draft internal transfer the phase-2 flow does — source
BWHSE (III-FLOOR for departments), destination the center's `III/CityCenter/<City>` location id.
Same `write_create_internal_transfer` flag, same audit trail, no second canary to run. Orders
whose lines are all untracked, or department "centers" with no Odoo location, legitimately create
NOTHING: `picking_status` stays `none` and the timeline says why — that's the designed path for
department water/snacks, not a failure. An unmapped FIELD center, by contrast, fails loudly with
the actionable "no Odoo location mapped" error (an admin must fix the mapping before go-live
anyway; a masking dry-run would hide it).

**2026-07-13 — SHIPPED is polled, never pushed** *(Phase 3)*
An approved order flips to `shipped` when its picking hits state `done` in Odoo — detected by
the same polite listener pattern as the count-transfer validation (throttled per order via
`picking_checked_at`, the list/detail GETs are the listener). The app still validates nothing.

**2026-07-13 — Notification outbox with OdooWriter-style honesty** *(Phase 3)*
Every notification is a DB row enqueued in the same transaction as the change it announces; the
API attempts delivery inline after commit and the worker sweeps retries (2^n-minute backoff,
capped attempts). Gates mirror writes: `NOTIFY_ENABLED` kill switch → per-channel feature flags
(`notify_whatsapp_live`, `notify_email_live`, shipped OFF) → configuration; gated sends are
recorded as SIMULATED, never faked. WhatsApp is primary through a tiny `WhatsAppTransport`
protocol (today: skubot's unofficial bridge over HTTP `POST /send` / `GET /status`; the official
Cloud API becomes a drop-in second implementation). Failure or a missing phone falls back to
email automatically. Bridge health is probed by the worker into `notify_channel_state` and shown
on the admin status page. Message bodies are single plain-text strings — template-message-safe
for the official API migration.

**2026-07-13 — Reasonability = deterministic rules, LLM polish on top, advisory always** *(Phase 3)*
The rules layer is pure and always runs: volume spike vs the center's own approved-order history
(Odoo has no per-center sales — the app's orders ARE the history), stock coverage at the
fulfillment source, low-count honesty, case-size mismatch, recent repeats, first-order/new-item
notes, absolute size caps. The optional Anthropic call (blank key = skipped) only rewrites the
order-level summary and may RAISE the severity, never lower it; any API failure degrades to
rules-only. Assessments compute at placement (stored on the order), recompute rules-only on
coordinator adjustments, and the order form's live preview is rules-only. Nothing ever blocks.

**2026-07-14 — Floor OOS board writes through a third gated operation** *(Phase 3.x)*
`OdooWriter.create_inventory_reduction` renders a DRAFT picking on the "USA-III: Inventory
Adj Reduction" operation type (matched by `name ilike` on the configurable
`ODOO_REDUCTION_PICKING_TYPE`, destination = the type's own default destination location),
removing whatever quantity Odoo still claims is on the floor when the team marks a shelf
empty. Same discipline as every write: feature flag `write_create_inventory_reduction`
(ships OFF — needs its canary before enabling), ILAPP-OOS- reference, idempotent by origin,
audited, draft-only — a human confirms the shelf is really empty in Odoo. Marks with nothing
to remove are bookkeeping (`picking_status="none"`); unmarking deletes the mark and unlinks
the app-created draft while it's still a draft. The /out-of-stock board itself is two honest
sources: Odoo's own floor zeros (computed) + the team's marks.

**2026-07-14 — The addition op mirrors the reduction; back-in-stock reconciles to a count** *(Phase 3.x)*
`create_inventory_addition` is the fourth write operation: draft on "USA-III: Inventory Adj
Adding Qty" (note the double space in the live name — the config default matches via an ilike
`%` wildcard, which the simulator now honors), locations mirrored (the type's default SOURCE →
floor). Both adjustments share one writer core (`_create_adjustment_draft`) — identical gates,
audit, idempotency; flag `write_create_inventory_addition` ships OFF. The OOS board's "Back in
stock" flow asks for the freshly counted shelf quantity and renders whichever draft reconciles
Odoo to the count (higher → add the difference, lower → reduce, equal → nothing), with the
honest caveat that the baseline is the last stock sync — the human validating in Odoo sees the
live numbers either way.

**2026-07-15 — The workbook stays the provable baseline; the forecast generalises it** *(Phase 4)*
The ordering engine (`app/ordering/engine.py` + `forecasting.py`, pure modules) reproduces the
USA INV CHK SEA sheet EXACTLY when demand is flat — `tests/test_workbook_parity.py` drives it
with the workbook's own inputs and all 281 fully-numeric SEA rows match within rounding (the
workbook is committed at `docs/reference/`, so CI runs parity on every push). Seasonal-index
forecasting (24 months of `sales_monthly`; ≥24 useable months → multiplicative indices + OLS
trend, 6–23 → moving average + trend, <6 → flat) rides ON TOP: demand becomes per-month MOH
multipliers in the same projection, so when the forecast equals the flat average the result is
identical to the workbook. The baseline sea/air numbers are always computed alongside and shown
with a divergence flag — the buyer sanity-checks the smart number against the spreadsheet they
trust. Category rules (target MOH per category, BLOOM case 32, gold/silver/air_only tags →
AIR-sheet top-up rule, camphor/toothpaste → yearly bulk target, expiry cap, domestic MOQ
trigger, clothing excluded) are code defaults merged with the admin-editable `ordering_rules`
AppSetting row — overridable without code changes, typos ignored rather than fatal.

**2026-07-15 — A purchase order is an immutable origin + an append-only event log** *(Phase 4)*
Generation freezes everything (`suggestion_json` per line, `rules_json` on the order,
`origin_*` quantities) — the review table IS the draft order, so later catalog/sales changes
never rewrite what the buyer saw (the batch-freezing seam from Phase 1 lands here). Placement
stores the ORDER LIST CSV+XLSX as attachments forever, creates the initial sea/air legs, and
dispatches the order email through the standard gate ladder (NOTIFY_ENABLED →
`ordering_email_live` flag, ships OFF → SMTP configured); a gated send is recorded SIMULATED
with the full rendered body on the thread — dry-run mode IS the email, minus delivery. After
placement, state moves only through confirmed timeline events (qty_change, substitution,
discontinued, method_change, split→new leg, availability), each carrying actor, source quote,
and confidence.

**2026-07-15 — Vendor replies are data; parsers propose, humans confirm** *(Phase 4)*
Replies land verbatim on the order thread (worker IMAP poll — READ-ONLY, tracks last UID in an
AppSetting, touches nothing in the mailbox, matched to orders by In-Reply-To against our sent
Message-IDs or the ILAPP-PO- reference token; unmatched mail is ignored. No IMAP configured →
paste replies through the admin endpoint). Parsing prefers the Anthropic structured call
(quotes must be verbatim substrings or they're dropped as hallucinations) and falls back to a
deterministic heuristic parser — the offline/dev/test path — so "we can only send 200 of the
500 lamps, and dhoop sticks are discontinued" always yields two line-matched proposals with
quotes and confidence. Proposals NEVER touch state: confirm (optionally edited) applies the
event append-only; reject records the decision. Same discipline for new products: the analog
suggester (LLM or name-token heuristic) only ever proposes; a confirmed ForecastAnalogy is
labelled method="analogy" on every suggestion it feeds and auto-graduates once ≥6 real months
accumulate.

**2026-07-16 — Domestic ordering is a weekly email, not a quarterly review** *(Phase 4.x)*
Real cadence: domestic vendors get simple order emails monthly/weekly; only India gets the
engine's review table. So /purchasing split into two tabs — India (engine, product-list-scoped
drafts, sea/air) and Domestic (per-vendor Quick Order: suggested-by-MOQ quantities, one button
that creates AND emails in a single step; wording "Dear {contact}, we kindly request the
following products… reply with an invoice"; no sea/air anywhere). Same PurchaseOrder + timeline
machinery underneath, so replies/tracking work identically. India generation is scoped by an
admin-uploaded product list (the `india_product_list` AppSetting keeps the original file for
download and the matched SKUs as the authoritative candidate set). Catalogs (né order lists —
UI rebrand only, schema names unchanged) can be born from any spreadsheet via the shared
matcher (`app/catalog/matching.py`): SKU/barcode/name in any combination, quantities ignored,
ambiguity surfaces as unmatched rather than a wrong guess.

**2026-07-22 — POS channels are split by pos.config at sync time** *(Phase 5)*
A live read of `pos.config` (53 records) settled it: city centers ring up real POS sales in
Odoo under per-center configs, alongside 'III Floor' and campus one-offs (Snack, Events,
Tent…). The sales sync now classifies every POS order into shoppe / city_center /
campus_other by normalized config↔center name match (`sales_channel_aliases` AppSetting as
the admin override; unmatched → campus_other, never a guess), and captures tax-in line
revenue (`amount`) alongside units. Pre-split rows keep channel `pos` + NULL amount until an
admin triggers the one deliberate re-backfill (`POST /admin/sync/sales/rebuild`); dashboards
estimate NULL amounts at current retail and disclose the estimated share rather than mixing
them silently. Corollary bug-fix: floor restock now counts only Shoppe channels — center POS
sales had been inflating the floor accumulator. Rejected: deriving "city centers" from
app-side transfer (sell-in) records — real sell-through exists in Odoo, so use it.

**2026-07-22 — Stock history is captured going forward, not reconstructed** *(Phase 5)*
The time machine's past view replays `stock_snapshots` rows the stock sync appends daily
(last sync of the day wins; zero rows skipped; `stock_snapshot_days` marks coverage so
"absent on a covered day" honestly means zero). Reconstructing backwards from sales/moves was
rejected — transfers and manual Odoo adjustments make it a guess, and the app never guesses
inventory. Consequence: history begins the day Phase 5 ships; the confidence indicator says
so. The future view runs the ordering engine's own recurrence in units (`_project_moh` is
scale-free) over `snapshots_for_products`, so the time machine and the India review table can
never disagree — a parity test locks it.

**2026-07-22 — Digests ride the notification outbox; the bot gets an API key** *(Phase 5)*
Availability digests are Notification rows via a new generic `enqueue_email` (email-only,
no order attached) — retries, backoff, and the admin's honest send log come free, and the
kind-level `availability_digest_live` flag (ships OFF) pre-marks rows SIMULATED at enqueue so
a flag flip can't unleash a backlog (`last_sent_on` stamps even simulated sends). skubot's
read-only endpoints live under `/api/v1/bot/*` behind an `X-API-Key` == `SKUBOT_API_KEY`
check (blank = 503) — machine auth kept deliberately separate from user sessions; Phase 6
builds on this surface. Chart series colors are app-level `--chart-1..4` tokens validated
with the dataviz six-checks against both surface modes, identical across the four light
palettes so a theme switch never repaints a learned channel color.

**2026-07-24 — Order & customer metrics come from order headers** *(Phase 5.x)*
The dashboard's "sell more to existing customers" questions (order-size trends, buyer
counts, loyalty) needed order-level facts the line-item snapshot can't answer. The sales
sync's existing parent-order fetch now also reads `partner_id` + `amount_total` (verified
live: ~96% of POS orders and 100% of online orders carry a partner — the registers attach
customers), rolled up into `sales_orders_monthly` plus a `customer_first_seen` memory
(partner×channel → first order date) so new-vs-returning stays stable across incremental
windows and full rebuilds converge. Only partner ids are stored — never names or contact
details. Honesty rules: walk-ins count as orders but never customers; distinct-customer
counts are monthly (no partner dimension exists to dedupe a quarter); the returning share
is quoted for the latest complete month so a half-month never reads as churn.

**2026-07-24 — Reconstructed history is allowed; guessed history still isn't** *(Phase 5.x)*
Amends 2026-07-22: the time machine may now backfill the past — because Odoo itself can
compute on-hand as of any date from its move ledger (`qty_available` under a
`to_date`+`location` context, verified live). That's real data, not the sales-walk guess
the original decision rejected, so the rule stands refined: live-captured days are ground
truth and never overwritten; reconstructed days are admin-requested, worker-paced (one
weekly date per loop pass — each is a heavy as-of computation for Odoo), marked
`source='reconstructed'`, capped below "high" confidence, and labeled in the UI.

**2026-07-24 — The warp is a real displacement wave, budgeted to stay smooth** *(Phase 5.x)*
The time machine's 4th-wall moment is an SVG feDisplacementMap shockwave riding a
canvas-built lens map from the user's pointer position (nav entry and every time-jump).
Three perf rules make it shippable: the filter region is clamped to the viewport (filtering
the whole document height per frame was the observed lag), the lens map is pre-built and
pre-warmed at mount so the first wave doesn't stutter, and the wavefront rings carry no
backdrop-filter. Reduced-motion skips it entirely; a failsafe timer un-bends the page if
rAF stalls. Era typography switches the whole panel (typewriter past / Orbitron future) so
non-today stock can never be mistaken for live counts.

**2026-07-24 — The warp moved to WebGL; raw shader, no engine** *(Phase 5.x)*
The SVG feDisplacementMap warp was CPU-bound by construction (the browser re-rasterizes
the filtered DOM layer every frame) and stayed laggy after every SVG-side optimization.
v3 distorts a pre-captured viewport snapshot (html2canvas-pro, taken during idle before
any warp can fire — on nav-link hover and after settled renders) in a raw-WebGL fragment
shader on an overlay canvas: one triangle, one texture, trivially smooth, and the wave
doubles as a mask over the data swap on time-jumps. Three.js/PixiJS were rejected as ~30×
the code we need for one effect; html2canvas-pro over html2canvas because Tailwind v4
emits oklch()/color-mix() the original can't parse. Degradation ladder: stale/no snapshot
→ rings only; no WebGL → rings only; reduced-motion → nothing.

**2026-07-24 — Warp v4: instant compositor feedback + a worker-rendered wave** *(Phase 5.x)*
v3's WebGL wave was smooth in isolation but started late and hitched in practice: it was
driven by main-thread rAF, fired after the route effect, and the time-machine mount (a
1,277-row table) blocked both. v4 splits the effect by thread: a compositor-only pop+rings
(transform/opacity WAAPI) spawns synchronously inside the nav click's capture phase —
visible feedback in the same tick, unstoppable by React — while the refraction wave renders
in a worker on an OffscreenCanvas from an ImageBitmap pre-decoded at capture time, so the
whole fire path is synchronous and the wave plays THROUGH the mount instead of after it.
Slider warps commit on pointerup rather than waiting out the debounce. The rings double as
the graceful floor when no snapshot/WebGL exists; reduced-motion still gets nothing.

**2026-07-24 — The warp's duration is adaptive: it ends when the page is ready** *(Phase 5.x)*
A fixed-length wave could finish while the destination was still mounting, breaking the
illusion. The timeline (shared `warpWave.ts`, unit-tested) now has three phases: expand
(~1.3s, punchier amplitude/fringe plus a magnifier "suction" inside the bubble), HOLD (a
standing shimmer while the destination renders — the time machine signals `settleWarp()`
after its data paints, via double-rAF), and release (+ a settle-pop ring). A ~5s cap plus
main-thread failsafes mean a page that never settles still ends the wave honestly. Probes
in the embedded browser pane are time-dilated by throttling — trust the unit tests and a
hand on a real screen for timing.

**2026-07-24 — The warp is an entrance, not a scrubbing companion** *(Phase 5.x)*
Noah's call after hands-on use: firing the shockwave on every slider/date jump broke the
scrubbing experience — the effect stays reserved for entering the time machine (quirk in
safe places, not between an operator and their data). The slider/date fireWarp +
recapture call sites are commented (not deleted) in TimeMachinePage for easy re-enable;
settleWarp wiring remains because it releases the entry wave. Bonus of disabling the
recapture: no more idle html2canvas work after every jump.

**2026-07-25 — Pre-deploy refinement round: the app sheds its scaffolding** *(Phase 5.y)*
Noah's between-prompts list before the first hosted test deployment. The through-line:
demo-era surfaces out, operator conveniences in. (1) The Availability page duplicated the
OOS and Coming-Soon pages — deleted; its Everywhere/Floor/Warehouse scope filter moved
onto the Out-of-stock page (floor scope = the actionable board, other scopes = read-only
snapshot lists; no category filter by request), and its incoming-shipments list finally
became the real warehouse /incoming page (retiring the last "Phase 2b" stub). (2) The
email digest was removed ENTIRELY — model+table dropped (migration a4e9d27c81b3), worker
loop, endpoints, flag, UI. (3) The Themes top-bar menu and page were replaced by a
/settings page for every role (palette picker for all; blacklist manager + Styleguide /
Palette-lab links for admins — those two left the nav, routes now admin-only). (4) Audit
log left the nav; it's a button on Status. (5) WhatsApp is ON HOLD — code stays, status
page says "on hold" instead of dressing an unconfigured bridge as a fault.

**2026-07-25 — Product blacklist: hidden app-wide, managed in Settings** *(Phase 5.y)*
Odoo's product list is full of non-shop noise (mortgages, solar panels, FBA fee lines,
rental houses) that polluted every list and report. `products.blacklisted` +
`not_blacklisted()` (the query-side twin, same pattern as `not_clothing()`) now filters
EVERY user-facing surface: catalog default, restock engine, OOS board + org lists,
center-order menus, ordering candidates + upload pools, spreadsheet matcher, time
machine, and the reports product-join (`_grouped`). Order-header metrics (AOV/orders)
have no product dimension and can't exclude — documented, not pretended. The one place
blacklisted items still appear is the Settings manager itself (products?blacklisted=true)
so they can be restored. Odoo is never touched.

**2026-07-25 — floor_rotating: a role for rotating volunteers** *(Phase 5.y)*
Identical to shoppe_floor everywhere (restock, OOS board actions, transfer viewing +
counting/closing transitions) EXCEPT creating transfer requests or editing their lines —
those endpoints stay shoppe_floor-only, and the UI entry points already keyed off
shoppe_floor so they hide automatically. Added to SEE_EVERYTHING_ROLES and the transfers
flow's FLOOR_ROLES; the role column is a plain string so no schema change.

**2026-07-25 — Admin notices: a bulletin board, not a notification channel** *(Phase 5.y)*
The "little inbox" is deliberately NOT wired into the notify outbox: admins post,
everyone sees a bell badge, opening marks read (per-user rows). No email, no WhatsApp,
no retries — announcements don't need delivery semantics, and keeping them out of the
outbox means the gate ladder stays a write/send concern only.

**2026-07-25 — Demo data cleared by script, not by nuke** *(Phase 5.y)*
`python -m app.seeds.clear_demo` (dry-run default, `--apply` to execute) removed 15,053
flow rows from the shared stack — transfers, center orders, POs, notifications, restock
state, seed+e2e catalogs, the seed vendor — while keeping users (demo logins are the dev
door), products, real synced history, settings, flags, and (always) the Odoo write audit
log. Draft pickings testing left in Odoo are Odoo's to clean; the audit log lists them.
Deploy guide for the hosted test round: docs/DEPLOY_VERCEL_SUPABASE.md — Vercel serves
only the static frontend (VITE_API_BASE now points the client at a remote API); the
backend+worker need an always-on host; Supabase is plain Postgres.

**2026-07-26 — The blacklist sweep: never-stocked + "-USA" duplicates, previewed** *(Phase 5.y)*
Hand-blacklisting 2,000+ junk entries wasn't going to happen, so Settings gained a
re-runnable admin sweep (POST /products/blacklist/sweep): rule A = active Odoo products
with no stock snapshot ever and nothing on hand now (2,157 on live — never-stocked
sarees, donation/fee lines, rentals), rule B = "USA" in the name or a "-USA" SKU suffix
(114 stale duplicate entries — matched case-sensitively in Python so SQLite tests and
Postgres agree and "Usable" never matches). IL-Service is a hard exception (a real
service item with no stock by nature); manual items are exempt from rule A by source.
Always preview-first in the UI; everything stays restorable. Applied on live 07-26:
2,267 blacklisted, 1,648 active products remain — the actual shop catalog.

**2026-07-26 — Customer metrics count people, not registers** *(Phase 5.y)*
Noah flagged new-vs-returning as "off". Diagnosis on live data: POS registers attribute
walk-in orders to per-register HOUSE partners — 'Isha Life USA - III FLOOR POS' held
~99% of Shoppe orders (1 "distinct customer"/month, always "returning"), the LA
register's account rode ~50–130 campus orders/month, so "96% of orders have a customer"
was a fiction. Fix in the sales sync (three pure detectors, thresholds test-tunable):
channel dominance (≥50 orders AND ≥30% share), per-register dominance (same test per
pos config — low-volume registers never dominate their aggregated channel), and monthly
volume (≥25 orders by one partner in one month is a register, not a person). Detections
are remembered in sync_state.extra['house_partners'] so tiny hourly windows can't
forget, house rows are scrubbed from customer_first_seen (the period-exact "new
customers" count reads that table), and their orders still count as ORDERS — walk-ins
are sales, not customers. Full rebuild re-ran 07-26: in-person channels now honestly
show ~0 identified customers; online (~1,900 real people/month, ~55/45 new/returning)
is the loyalty signal. Known-customer share fell from a bogus 96% to a truthful ~24%.

**2026-07-26 — Reports: order size is a number; pickers stay open for multi-add** *(Phase 5.y)*
The AOV month-line said little that the number doesn't — the Order size card is now the
period average, large ($62.22-style display type), with orders/total/prior context; the
trend chart is gone by request. And every search-to-add surface (ProductPicker: catalogs,
transfer requests, vendor rosters — plus the Settings blacklist search) keeps its results
open after a pick so several items go in from one search; picked rows just flip to
disabled. The old clear-on-pick forced a retype per item.

**2026-07-26 — Clothing is curatable on catalogs; out-of-scope survives only in purchasing** *(Phase 5.y)*
Noah's bug report ("saree"/"kurta" return nothing when building a catalog) revealed the
phase-2 reading of the brief was too broad: the catalog editor's picker, the order-lists
API, and the center-menu builder all hard-excluded clothing. His direction supersedes it —
catalogs are hand-curated menus, so if an admin deliberately adds a kurta, centers may
order it (transfers move any stock). The exclusion remains where the brief aimed it: the
India engine's candidate pool, forecast-analogy pools, and the vendor roster picker never
offer clothing. One test now locks BOTH sides of the line.

**2026-07-26 — Sweep rule tightened: never-stocked must also mean never-SOLD** *(Phase 5.y)*
The first sweep treated "no stock history + nothing on hand" as inert — wrong, because
snapshot history is weekly and only ~6 months deep: fast movers sell out between
snapshots, digital items never have stock at all. 1,308 items WITH sales (612 clothing,
299 digital, 123 snacks…) were swept and have been restored (Noah's four manual
blacklists preserved by name). The sweep now requires no sales_monthly rows too, so it
only catches genuinely dead entries; sales-active junk (fee lines, rentals) is left to
manual blacklisting — a human judgment the sweep shouldn't guess at.

**2026-07-27 — Two-way transfer sync: Odoo is a first-class actor in the flow** *(Phase 5.y)*
Noah's ask: transfers headed to floor staging should appear on coming-soon even when
drafted directly in Odoo, and warehouse actions in Odoo on app-placed transfers should
move the app workflow. Two halves, both built on existing foundations rather than a new
integration style: (1) INBOUND — a fifth sync domain (`transfers`, 10-min cadence, one
tiny search per run) snapshots pending staging-bound pickings into
`staging_inbound_moves`; coming-soon unions them per product with a dashed "Odoo ·
state" chip. App-placed pickings are excluded by id — no double counting. Validated or
cancelled pickings drop out of the pending search, so arrival self-cleans the list.
(2) OUTBOUND — the existing food-POS listener pattern (UI polls, GETs check Odoo,
per-row throttle stamp) extended from count-validation to the placement picking:
confirmed/assigned → working on it, done → sent → count transfer staged, cancel →
cancelled, each with an "… in Odoo — synced" event. The warehouse can now live
entirely in Odoo and the board stays truthful; role-gated app buttons remain for teams
who prefer clicking here. Detection ran clean against live (0 pending pickings on a
Sunday — honest empty, not an error).

**2026-07-27 — The pallet is the unit of warehouse work, not the transfer** *(Phase 5.y)*
Noah described the real process: outbound transfers are retargeted to III/Staging2 (a
warehouse consolidation location, live id 2030), picked in batches, and ONE pallet
transfer carries everything to III-FLORR-STAGING. The app now mirrors reality instead of
fighting it. A validated picking's DESTINATION decides what "sent" means: to floor
staging → count transfer immediately (the old path); to staging2 → the request waits
("waiting for the pallet") and the count is deferred. The new Staging 2 page shows the
consolidation point live (a deliberate on-demand read — it's an action screen) with one
big button that renders the pallet as a single draft via the existing, already-canaried
create_internal_transfer operation. Pallet validation in Odoo is the landing signal:
every SENT request still waiting gets its count transfer prepared in that moment. The
heuristic is honest about its grain — any request sent-and-waiting when a pallet lands
is assumed to be on it; Odoo's own availability check on the count picking is the
backstop for stragglers. staging2 stock counts as owned (org OOS, purchasing on-hand)
but NOT as warehouse-sellable (bwhse scope) — it's committed to the floor.

**2026-07-27 — Sourcing lives in Odoo, as product tags** *(pre-deploy tweaks)*
Domestic-tracked products kept leaking onto the India purchasing page because candidacy
was inferred (reference shape ^[A-Za-z]{2}\d{10}$, vendor assignment) rather than
declared. Noah wants the classification made IN ODOO, where products are managed. The
mechanism is product tags named exactly "Domestic" or "India" (case-insensitive — the
product form's Sales tab; `product.tag` and `all_product_tag_ids` verified live
2026-07-27, zero tags existed so the team starts clean). The product sync stores the
verdict in `products.sourcing`; a "Domestic" tag is a HARD exclude from India candidacy
— it outranks even the uploaded product list, mirroring the domestic-vendor rule,
because both are explicit human declarations — and "India" admits a product whose
reference doesn't look India-shaped. Domestic wins a double-tag conflict: the failure
mode of leaving an item off an India order (buyer adds it back) beats ordering from
Coimbatore something bought in Ohio. Untagged products behave exactly as before, so
nothing reclassifies until a human tags it.

**2026-07-27 — Search is tokenized per field, not fuzzy and not cross-field** *(pre-deploy tweaks)*
"Yoga mat" couldn't find "Yoga-Mat-Cotton-Brown" — the search was one contiguous
`%query%` ILIKE. Now the query splits into alphanumeric tokens and a product matches
when any ONE field (name, SKU, barcode, category) contains ALL tokens: order- and
separator-insensitive, and strictly more forgiving than before (every old match still
matches). Tokens deliberately do NOT mix across fields — "copper 00" must not return
every product whose SKU contains "00". One semantics, three twins, kept in lockstep:
`app/catalog/search.py` (SQL clause + in-memory matcher for availability/time-machine/
bot) and `frontend/src/search.ts` (the client-side filtered lists).

**2026-07-27 — Never-stocked items are hidden from OOS, not blacklisted** *(pre-deploy tweaks)*
Noah asked to blacklist everything showing "no stock history yet" on the out-of-stock
page (except IL-Service). The numbers said otherwise: 1,271 of 1,652 org-OOS rows had no
history, but 1,240 of them have real sales — 612 are the same clothing restored by hand
after the 07-26 sweep incident, 299 are digital downloads that sell without ever holding
stock. Blacklisting hides items app-wide (search, menus, reports), so with those numbers
on the table Noah chose the display fix: OOS lists now hide never-stocked items by
default (they didn't GO out of stock — the app has just never seen them stocked), with
an "Include never-stocked" chip to peek, scope-aware (no bwhse history = hidden from the
warehouse scope). The never-stocked AND never-sold subset was already blacklisted by the
07-26 sweep — zero new flags flipped. The org list dropped 1,652 → 381 actionable rows.

**2026-07-27 — Three palettes: Charcoal Pop leads, Neem Tree and Turmeric Root join** *(pre-deploy tweaks)*
Noah picked Charcoal Pop as the main theme and retired Sunset Studio, Indigo Violet, and
Forest & Clay. Pop's values moved into the @theme default block (an absent or stale
data-palette id now falls through to it; index.html validates stored ids). Two new
palettes built from Noah's swatches, tuned for M3 contrast: NEEM TREE — parchment
surfaces deepening toward desert sand, olive-bark secondary, a neem-leaf green tertiary
(the given palette had no leaf — a tree needs one), deep-mocha ink, stone-brown
secondary text. TURMERIC ROOT — cool lavender-slate ground so the gold and the brand
orange both glow; sunflower-gold secondary wears DARK text (#402d00, 7.7:1) because gold
is a light hue — M3 golds never carry white; slate-violet tertiary, carbon-black ink.
Each palette now themes inverse-surface too, so snackbars match their world. Dark mode
remains the one global slate-indigo scheme.

**2026-07-27 — Never-stocked OOS items are hidden, not blacklisted** *(pre-deploy tweaks)*
Noah asked to blacklist everything on the OOS page saying "no stock history yet"
(except IL-Service). The numbers said otherwise: of 1,270 such items, 1,240 had real
sales — 852 in the last 12 months, 612 of them the same clothing restored by hand after
the 07-26 sweep incident, 299 digital downloads that sell without ever holding stock.
Blacklisting is app-WIDE (search, menus, reports), so with those numbers surfaced Noah
chose the display fix: `oos_items` now drops items whose snapshot history never saw
them stocked (scope-aware), with an "Include never-stocked" chip as the peek. Live
effect: the Everywhere list fell 1,652 → 381 actionable rows. The never-sold subset
that COULD be safely blacklisted turned out to be zero — the 07-26 sweep had already
caught it (items whose only sales rows are zero/negative months count as activity,
deliberately). The bot API inherits the curated default.

**2026-07-27 — Three palettes: Charcoal Pop leads, Neem Tree and Turmeric Root join** *(pre-deploy tweaks)*
Noah promoted Charcoal Pop to the default and retired Sunset Studio, Indigo Violet, and
Forest & Clay. Pop's values moved INTO the `@theme` block (the default needs no
attribute; unknown/stale `data-palette` ids fall through to it, and index.html
validates stored ids so retired ones can't strand a browser). Two new palettes built
from Noah's swatches, tuned for M3 contrast: **Neem Tree** — olive-bark secondary
(#5c4f26), neem-leaf tertiary, parchment surfaces deepening toward desert sand,
stone-brown variant text; **Turmeric Root** — sunflower-gold secondary (#f5bd45)
wearing dark text (gold is a light hue; white-on-gold would fail contrast), slate-
violet tertiary, cool lavender surfaces, carbon ink. Each palette now themes
inverse-surface too, so snackbars match their world. Dark mode stays the one global
slate-indigo scheme.

**2026-08-05 — Security audit remediation: config fails closed, Google OAuth, verified-claim linking** *(security review)*
A four-agent defensive audit found the app layer sound (every route guarded, row scoping
correct, the Odoo write gateway bypass-free, no SQLi/command-injection/path-traversal, no
secret ever committed) and the risk concentrated in **deployment configuration** and in
**what leaves the building**. Live probe: an instance reachable off-loopback answered
`{"mode":"dev"}` while its `/health` reported `odoo_mode=live, writes_enabled=true`.

The fixes, and why they are shaped this way:

**Config now refuses to boot.** `Settings` gained a `model_validator` that raises
`InsecureConfig` when `ENV` is not in `DEV_ENVS` and any of: `AUTH_MODE != supabase`, an
empty/published/short `APP_JWT_SECRET`, or `*` in `CORS_ORIGINS`. The published default
`dev-only-change-me` is gone — `app_jwt_secret` defaults to `""`, and in dev a blank or
published value is replaced with a random per-process one (sessions end on restart; dev
codes render on screen, so that costs one click). The root cause was that nothing failed
when the config was wrong, so `test_config_security.py` is the control, not the patch.
`render.yaml` no longer commits `AUTH_MODE: dev` on the `ENV=prod` service — it is
`sync: false`, set deliberately.

**`dev_auth` is the gate, never `auth_mode`.** Anything that leaks a login code checks
`settings.dev_auth` = dev ENVIRONMENT **and** dev mode, so a mode mistake alone can't
hand a code to an anonymous caller. Login responses are now uniform for known, unknown
and inactive identifiers (the old 404 was an account-existence oracle) — `login()` in the
tests still works because dev mode still returns the code for a real user.

**Google OAuth is the production sign-in** (Noah's call, replacing email/SMS OTP).
`SUPABASE_OAUTH_PROVIDERS=google` and `SUPABASE_OTP_ENABLED=false` ship as defaults;
`/auth/config` advertises `oauth_providers` + `otp_enabled` and the sign-in page renders a
provider button per entry. `/auth/exchange` is unchanged — the app still only ever sees a
Supabase JWT, never a Google credential.

**Identity linking now requires a VERIFIED claim.** `match_supabase_claims_to_user` linked
`auth_uid` to an app user on a bare email/phone match, so anyone able to sign up on the
Supabase project could claim an admin's address and keep it. It now checks
`email_verified`/`phone_verified`, reading top-level then `app_metadata` then
`user_metadata` — first source carrying the field wins, because the first two are
Supabase-controlled and `user_metadata` is client-writable at signup. Missing or
unparseable counts as unverified (fail closed), and an unverified identifier that *would*
have matched raises 403 rather than linking. Google verifies emails, which is precisely
what makes it a safe default provider — but the Supabase project must still offer only
trusted providers.

**Sessions are revocable.** `users.token_epoch` (additive migration `c1f7a4d90b52`) is
embedded in the token and compared per request; bumped by `POST /auth/logout-everywhere`,
by a role change, and by deactivation. `UPDATE users SET token_epoch = token_epoch + 1`
logs everyone out without rotating the signing key.

**Outbound encoding.** The two worst findings were both output encoding, not input
validation: `ordering/export.py` wrote product names verbatim into CSV/XLSX that get
**emailed to Coimbatore** (openpyxl turns a leading `=` into a real formula cell), and four
`Content-Disposition` sinks interpolated unsanitized filenames — reachable with **no app
account**, since an inbound email attachment filename carries RFC 2231 escapes. Now
`_safe_cell` neutralizes formula-leading text cells (numbers keep native types) and
`app/downloads.py` is the single door for every file response (CR/LF stripped, RFC 6266
`filename*`, content-type allowlist so a stored `text/html` can't render in-origin).

**Rate limiting is in-process on purpose** (`app/ratelimit.py`): one uvicorn process, so a
process-local sliding window is as accurate as a library and adds no dependency.
Authenticated limits key on user id (exact); unauthenticated ones key on IP *and*
identifier, because behind the tunnel the IP is only real once uvicorn runs with
`--proxy-headers --forwarded-allow-ips=<proxy>` — which the entrypoint now does, never `*`.

Also: `allow_inf_nan=False` and bounds on `counted_qty` (NaN slipped past the writer
because `nan <= 0` is False — the writer's guards are now `math.isfinite` too), ceilings on
list bodies and `limit` params (`?limit=-1` emitted `LIMIT -1`), pallet creation deduped
against an open pallet, security headers + a real CSP (the pre-paint palette script moved
to `public/palette.js` so the CSP needs no per-edit hash), verified SMTP STARTTLS,
`/api/docs` and the detailed `/health` gated (an anonymous caller could read the write
posture), non-root containers, and pip-audit/npm-audit/gitleaks/Dependabot in CI. The
coordinator roster workbook left git and the image for `./private/` — treat it as already
disclosed and rotate the Stripe terminal registrations.

---

**2026-08-22 — Departments order by QR, approved by a shop team member** *(Phase 5.z)*
III departments walk into the Shoppe, take water/snacks/t-shirts and fill in a paper
sheet. They now scan a QR on the counter instead.

*Why the QR carries no credential.* The obvious design — a token in the code, exchanged
for a scoped session — makes a poster on a wall a bearer credential that anyone can
photograph and that never expires. Everyone at III has an `@ishausa.org` Google account,
which is already the production sign-in, so the code can be a plain link
(`/place-order?center=<id>`): scanning it while signed out lands on the normal Google
sign-in and returns to the department's form, and sessions last 30 days, so it's one
sign-in per phone. Rejected, for now: a separate kiosk credential class (its own
dependency that `get_current_user` never accepts, per-department PINs, short-lived
exchange, hard rate limits). That is the design to reach for if account-free access is
ever needed — the important part of it is that a kiosk token must never be "a user with
fewer roles", or one missed `require_roles` turns a poster into an admin.

Two consequences the code owns: the destination is parked in `sessionStorage`
(`auth/returnTo.ts`) because router state cannot survive the redirect to Google and
back, and `safeReturnPath` accepts only single-slash same-origin paths — the value
decides where a fresh session lands, and an open redirect immediately after login is
exactly how a scanned QR becomes a phishing page.

*Who approves.* The first cut of this had departments approving themselves, on the
reasoning that the paper sheet had no approval step either. Noah's correction, same day:
a shop team member should look. That is not the same as making them an Order Reviewer of
the III Departments review zone — the person behind the counter is an Inventory Flow
Manager or Floor Team, and a review zone is a coordinator's territory, with centers,
catalogs and a roster attached.

So it's an **add-on role**, the second one after `inventory_wrangler`:
`dept_order_approver`, held alongside whatever someone already is, granting exactly one
job. Add-ons carry no row scope on their assignment — an approver reaches every
departments-kind zone and nothing else — which means the permission is expressed in
three places that have to agree (router membership, `visible_center_ids`,
`_is_coordinator_of`) rather than in a zone id. The alternative, a zone-scoped
`zone_coordinator` row pointing at III Departments, would have been less code and the
wrong shape: it says "this person runs that review zone" when what's true is "this
person can approve a department's pickup."

The self-approval machinery is deleted rather than left switched off — the column, its
migration, the `ORDER_AUTO_APPROVED` notification kind and the "Record it" copy. A
disabled feature nobody uses is a thing the next person has to understand.

Also fixed in passing: both add-ons now appear in the Users page role picker.
`inventory_wrangler` had been grantable only through the API since it shipped, which
made it, in practice, a permission nobody could give anyone.

---

**2026-08-22 — Approving a count validates it in Odoo** *(Phase 5.z)*
This reverses, for one flow, the rule the project was built on: *nothing the app creates
is ever validated by the app.* Everywhere else that rule stands.

The rule exists so a human sees every stock movement before it happens. In counting, that
human has already looked: the reviewer compared a counted number against Odoo's, in a
screen built to show exactly that, and pressed Approve. Leaving the resulting adjustment
as a draft doesn't add a second judgement, it adds a second queue — 49 pickings on one
day, each needing a click that can only say "yes, what the reviewer decided." A queue
nobody works is worse than no queue: the shelf figures stay wrong while the app reports
the count as applied.

So `OdooWriter.validate_adjustment` posts it, and it is the only operation in the writer
that moves stock. Its guards are correspondingly tight, and the second one was found by
looking at production rather than by reasoning: `ILAPP-CNT-` is shared between count
adjustments and the floor's STAGING→FLOOR count transfers, so validating by reference
prefix — the obvious implementation, and what the request literally asked for — would
have posted pallets nobody had counted yet. The picking TYPE is what separates them.
Backorder wizards are refused rather than confirmed, because a quantity Odoo can't
satisfy is a question for a person.

The escape hatch is the feature flag: `write_validate_inventory_adjustment` off restores
the previous behaviour exactly — a draft, a deep link, a human. It ships on, unlike every
other write flag, because it *is* the requested behaviour and a flag-off default would
have shipped a feature that silently does nothing.

*The bug underneath it.* Looking at those 49, another 16 approvals turned out to have
written nothing at all: `_adjustment_env` re-read the picking type for every single
adjustment, and a 65-item review put Odoo's proxy over its rate limit (HTTP 429). The
approvals were recorded, Odoo never heard them. Operation types are configuration, so
both lookups are cached per process now, and `scripts/post_count_adjustments.py` re-runs
the ones that were lost.

---

**2026-08-24 — Counters and reviewers can see each other's counts** *(Phase 5.z)*
The 08-22 catch-up posted 81 adjustments, and three products came out holding a quantity
nobody had counted. Two people had walked the same rack; each submission froze the same
Odoo number; both deltas were then subtracted from it. The LS Peasant was counted 3, 6
and 5, and ended at 0.

Nothing in the app was wrong, exactly — the unique constraint stops a product appearing
twice in one submission, and a second submission is what a legitimate recount looks like,
so it must stay allowed. What was missing is that **nobody could see the other count**.
So the app now says so, in the two places where a person could still act on it: on the
counting row the moment a product is added, and on the review card above the Approve
button.

The distinction that carries the meaning is *applied*, not *approved*. A count that has
reached Odoo is harmless — the system quantity already includes it, and mentioning it
only explains why the number moved, so it fades after a week. A count that hasn't is the
hazard regardless of age, because it is still going to be measured against the old
number. With the posting flag off, an approved count is still unapplied: it's a draft.

Advisory on both sides. Blocking a second count would break recounts, which are the
whole point of the review loop.

**What this does not fix.** The delta is still computed against the Odoo quantity frozen
at count time, and applied whenever the adjustment posts. That gap is the actual defect —
it also produced the negative-stock refusal in the catch-up, where a count from the 22nd
met a shelf that had moved by the 24th. Re-reading Odoo at apply time (and refusing when
live has drifted from the captured baseline) would close the class; the warning only
makes it visible to a human first. Left open deliberately, as a decision about how much
the app should second-guess a reviewer, not an oversight.

---

**2026-08-24 — The count's baseline is re-read before it is applied** *(Phase 5.z)*
An inventory adjustment doesn't set stock to a number, it moves a difference: counted
minus what Odoo said when the count was taken. That difference is only the right
instruction while Odoo still says the same thing. It said 9 for the LS Peasant when three
people counted it; two differences were then applied to it; the shelf record went to 0.

So the approval re-reads Odoo first. If the number still matches, nothing changes — this
is the ordinary case and it behaves exactly as before. If Odoo has caught up to what was
counted, the item is approved with nothing to write, which is the goal state anyway. If
it has moved somewhere else, the approval is **refused**: nothing is written, and the
item stays open so the reviewer can ask for a recount.

Refusing rather than recomputing is the judgement call here. Recomputing against live
would silently do one of two contradictory things: for a duplicate count it lands on the
right answer, and for a product that simply sold three units since the count it re-adds
them. Guessing about stock is the thing this codebase does not do.

*But the app doesn't have to guess.* Odoo's move ledger records what moved and where it
went, and Odoo's own `stock.location.usage` names the kind: a `customer` counterpart is a
sale, an `inventory` one is a correction. So the refusal asks why first (`ledger.py`).
Drift explained entirely by sales, transfers and receipts → the count's finding is
untouched by any of that, so it applies, with the reason recorded on the item's history.
Any correction in the window → refuse, because the discrepancy has been fixed once
already. Unexplained residual, or Odoo silent → refuse, because not knowing is not the
same as knowing it is fine.

That distinction is what makes the guard usable: a shop floor sells all day, and a rule
that blocked on any movement would block nearly every afternoon approval. A rule that
blocks on *corrections* blocks exactly the thing that went wrong.

One subtlety worth keeping: the adjustment test is "did any happen", not "do they net to
zero". The Relaxed Henley's two corrections were +2 and then −2 — netting to nothing
while meaning the shelf had been corrected twice. Running the module against production
before wiring it in is what caught that.

Two details that carry weight. The refusal happens BEFORE the decision is recorded,
because `flow.can_review` allows approve/reject/recount only on an open item — approving
first and failing after would leave the item in a state with no way back. And a snapshot
fallback is never treated as drift: when Odoo is quiet, `quantities_at` returns the last
sync's figure, which is routinely behind for unrelated reasons, and blocking on it would
stop approvals every time the connection hiccuped.
