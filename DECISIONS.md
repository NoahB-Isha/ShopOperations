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
