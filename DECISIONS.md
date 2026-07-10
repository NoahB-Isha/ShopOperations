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
