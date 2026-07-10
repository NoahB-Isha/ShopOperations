# Isha Life North America Ops App — Prompt Set for Claude (Fable 5)

**How to use this document.** Prompt 0 is the project brief — give it to Claude at the start of every session (or save it as `CLAUDE.md` / a Project doc so it's always in context). Then run the phase prompts one at a time, in order. Each phase ends with acceptance criteria; don't move to the next phase until they pass. Attach the referenced files (`Operations Overview.txt`, `OPS APP FEATURE LIST.xlsx`, `IL City Coordinators.xlsx`, and the `Copy of USA INV CHK.xlsx` workbook) wherever noted.

Items marked `[DECIDE]` are open decisions — resolve them before running the relevant prompt, or let Claude propose a default and confirm.

---

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

**This is a brand-new repo.** The projects below are *reference material*, not a codebase to extend. Read them to learn what worked, what the data looks like, and what mistakes to avoid — then implement fresh, borrowing ideas (and test fixtures) freely:

- `https://github.com/noahballinger/ops` — a previous attempt at the India ordering tool: FastAPI + SQLModel + Postgres, an Odoo 19 JSON client (session auth over `/web/session/authenticate` + `/web/dataset/call_kw`, background snapshot sync with self-healing cache), a **demand forecasting engine with a 281-row parity test** against the `USA INV CHK` workbook, sea/air split logic, config-driven category rules, and CSV/XLSX export. The most valuable things to study: the projection/forecasting math and its parity tests (reproduce these — they're the spec of record for ordering), the Odoo session-auth approach (there's no External API on this instance), the snapshot-cache pattern for surviving Odoo outages, and the credential-safety posture.
- `https://github.com/noahballinger/skubot` — Python WhatsApp chatbot volunteers use for product/inventory lookups. The app must expose an API the bot can call, so bot features and web features share one backend.
- `https://github.com/noahballinger/ILscripts` — the current restock-sheet script (restock lists from yesterday's sales). Its logic gets absorbed into the app.

### Users and roles

Role-based access with row-level scoping:

| Role | Sees / does |
|---|---|
| **Admin / Office** | Everything. Manages users, catalogs, order lists, India orders, reports. |
| **Warehouse** | Incoming shipments, transfer fulfillment, inventory adjustments queue, OOS/coming-soon lists. |
| **Shoppe Floor** | Restock lists, BWHSE→Floor transfer requests, staging reconciliation. |
| **Zone Coordinator** | Three simple views: (1) my city centers + their pending orders, (2) pending orders for approval, (3) order history. Approval creates the draft Odoo transfer. |
| **City Center Orderer** | Two views only: Place an Order, Order History (with a one-click "duplicate order" button). Nothing else in the app. |
| **Dept Liaison / Dept Orderer** | Same shape as coordinator/orderer, scoped to the III Departments zone and its catalog. |

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
   - **(b) A recorded-fixture Odoo simulator**: capture real production *read* responses (sanitized) as fixtures, and build a small in-process fake that implements the handful of models the app touches — `create` returns an id, subsequent reads reflect the write, state transitions behave like Odoo's. All integration tests run against this simulator in CI. When production schema might have drifted, a read-only contract check (`fields_get`, `search_count`) against production re-validates the fixtures — reads are always safe.
   - **(c) A gated canary protocol against production** for each new write operation before it's enabled: draft records are inert in Odoo (they move no stock), so the canary creates one clearly-marked draft (reference prefixed `APP-TEST-`, minimal lines), reads it back, verifies the deep link, then unlinks it. This runs only from an explicit admin action with the operation's feature flag still off — never automatically, never in CI. Every canary is audit-logged like any other write.
5. **Blast-radius habits:** writes are idempotent (client-generated reference on every created record so retries don't duplicate); `unlink` is permitted only for records the app itself created, matched by reference prefix; new write operations start life feature-flagged and graduate only after their canary passes.
5. **Credentials** live in environment variables, are held in memory, and are never logged, persisted, or placed in URLs. The app currently authenticates as a **repurposed personal Odoo account that also has human activity on it** (a dedicated user may come later). Two consequences the code must own: (a) Odoo's own logs can't distinguish app actions from the human's, so the app's audit log is the source of truth, and **every record the app creates carries an app reference prefix** (e.g., `ILAPP-…`) so app-created records are identifiable regardless of which account made them — canary cleanup and `unlink` safety match on this prefix, never on the account; (b) the human may change the password or trip 2FA — auth failures must surface loudly on the admin status page rather than silently stalling sync.

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

## PROMPT 1 — Foundation: scaffold, auth, Odoo sync, catalog, design system

Using the project brief: scaffold the monorepo and build the foundation layer. Scope:

1. **Repo scaffold** — `backend/`, `frontend/`, `worker/`, `infra/` (Docker Compose, Caddy/nginx config), `Makefile` with `make dev`, `make test`, `make seed`. CI config included.
2. **Auth & roles** — Supabase Auth with one-time codes via email or SMS, user/role/zone/center models, admin UI for inviting users and assigning roles, row-level scoping middleware. Import script that seeds centers, zones, and coordinators from `IL City Coordinators.xlsx` (handle the messy real-world data in that sheet: missing emails/phones flagged for follow-up, inactive centers, shared product sets like Austin/San Antonio, the Canada sheets).
3. **Odoo sync + writer** — build the JSON client (using the ops repo's session-auth client as a reference implementation) and the background snapshot sync in `worker/`. Snapshot: products (SKU, name, category, cost, price, case size, tags), on-hand by location (BWHSE, FLOOR, STAGING), monthly sales history by channel, incoming stock moves. Health endpoint reporting sync age and staleness. Stand up the `OdooWriter` gateway with its first operation (`create_internal_transfer`), the audit log, dry-run mode, the kill switch, and all three test layers described in the brief — later phases add operations to this gateway, never around it.
4. **Product catalog module** — unified product table keyed by Global SKU with US SKU / Odoo ref resolution (the ops repo's `resolver.py` shows the mapping problem), an admin UI to view products and edit app-level tags (Air Only, Sea Only, Gold, Bloom, Silver, Camphor, Toothpaste, Expires + date), and support for **non-Odoo items** (water, cookies — dept-orderable items with no stock tracking).
5. **Design system** — tokens (color, type, spacing), the core components (AppShell with role-aware nav, DataTable with sort/filter/search, forms, buttons, badges, toasts, empty states), and a `/styleguide` route that renders them all. Build one real page with it: a product catalog browser with live search.

Acceptance: `make dev` brings up the full stack; I can log in as three different roles and see different navs; the Odoo sync runs against a fixture file when no credentials are set; the catalog page searches 1,000+ products smoothly; the styleguide renders.

---

## PROMPT 2 — Internal flow: Order Lists + BWHSE→Floor transfers + restock lists

This phase replaces the WhatsApp "floor transfers" group and the restock scripts.

1. **Order Lists** — create/edit/delete lists of items with quantities; clone a list; assign a list to a zone coordinator; coordinator approval creates a **draft** internal transfer in Odoo via the OdooWriter (behind the feature flag), with full audit logging and a **direct link to the created Odoo record** shown immediately. Show write status (created / failed / disabled) honestly in the UI.
2. **BWHSE→Floor requests** — floor volunteers build a transfer request (searchable product picker showing floor qty vs. warehouse qty), warehouse reviews and adjusts, both sides see one shared status timeline (requested → picked → in staging → counted → on floor). Model the STAGING→FLOOR reconciliation step: counted quantities vs. sent quantities, with discrepancies flagged into a warehouse "adjustments to review" queue instead of vanishing into chat.
3. **Restock lists** — port the ILscripts logic: a floor restock list and a back-stock restock list computed from yesterday's sales and current floor/back quantities, refreshed on each sync, viewable on a phone, with check-off state that resets daily.

Acceptance: end-to-end demo on seed data — floor requests 10 items, warehouse fulfills 9, staging count finds 8, the discrepancy appears in the adjustments queue; approving an order list creates a draft transfer (or a clearly-labeled simulated one when writes are off); restock lists match a hand-computed fixture.

---

## PROMPT 3 — City Center & Department ordering

1. **City Center order form** (mobile-first): autofilled name + center, searchable item list scoped to that center's catalog, order notes. Special features: per-item **OOS timeline status** ("out of stock — expected back mid-August" from incoming stock moves) and a **reasonability score** — an LLM+rules assessment of the order against that center's sales history and current warehouse stock, shown as a gentle badge (e.g., "3× your usual volume of Copper Bottles — warehouse has 40"). Score is advisory only.
2. **Orderer views**: Place an Order and Order History with one-click duplicate. Nothing else in their nav.
3. **Zone Coordinator views** (exactly three, keep them simple): my centers + pending orders; pending approvals (approve/adjust/reject, approval → draft Odoo transfer with a direct link to it); order history.
4. **Department ordering**: same machinery, III Departments zone, department catalog including non-Odoo items, fulfillment source defaulting to `III-FLOOR`.
5. **Status notifications — WhatsApp is the primary channel.** WhatsApp currently runs over an **unofficial bridge** (skubot's existing connection); an official WhatsApp Business API account is in progress. So: build the notification service around a clean `WhatsAppTransport` interface with the unofficial bridge as the first implementation, designed so the official Cloud API becomes a drop-in second implementation later (expect that migration — keep message formatting simple enough to survive the official API's template-message rules). Reuse skubot's session so there's one WhatsApp presence, not two. Because unofficial bridges drop sessions and risk bans, the service must detect delivery failure and disconnection, fall back to email automatically, and surface bridge health on an admin status page. "Order approved," "order shipped," etc. go out over WhatsApp from day one. The full conversational bot experience still lands in Prompt 6; this phase only needs outbound sends.

Acceptance: on a phone-sized viewport, a city orderer can place an order in under a minute; a coordinator approves it, a draft transfer appears with a working link into Odoo, and the orderer receives a WhatsApp "approved" notification; a department orders water (non-Odoo item) and it flows through without touching Odoo; reasonability flags fire on a deliberately absurd seed order.

---

## PROMPT 4 — India→USA ordering + Order Action Tracking

The heart of the app. The ops repo solved the math; reproduce it here and finish what it left as seams.

1. **Ordering engine** — implement the demand forecasting and 6-month projection with sea/air split, matching the `USA INV CHK` workbook's `SEA` sheet logic exactly at baseline (the ops repo's README documents the formulas and its parity-test approach — reproduce that test: every fully-numeric SEA row must match within rounding). Odoo holds **24 months of sales history**, which is enough to enable seasonal-index forecasting on top of the flat baseline — always show the baseline alongside the seasonal number with a divergence flag. Inputs come from the app's own Odoo snapshot, with workbook/CSV upload as a fallback path. Outputs: per-SKU sea/air suggestions, 6-month projection, months-on-hand, flags (low confidence, divergence, expiry, air-only, bulk-cycle items like toothpaste/camphor). Category rules live in config, overridable without code changes.
2. **New products** — no sales history → either LLM-suggested similar-product matching (proposal + confirm) or hardcoded monthly estimates; visibly flagged as forecast-by-analogy and auto-graduating once real data accumulates.
3. **Review screen** — the ops repo's editable review table, upgraded into the new design system: filter by tags, per-line suggested vs. override, plain-language air-split reasoning, projection sparkline per SKU.
4. **Place order** — the big button: freezes the snapshot, generates CSV+XLSX matching the `ORDER LIST` format, and sends the order email to Coimbatore from the app mailbox (feature-flagged; dry-run mode renders the email without sending).
5. **Order Action Tracking** — the interactive timeline. Ingest replies on the order's email thread; LLM-parse them into proposed `OrderLineEvent`s (qty change, substitution, discontinuation, method change, split, availability) each with the supporting quote and a confidence score; human confirms/edits/rejects each proposal; confirmed events update order state append-only. Manual event entry and file attachment upload for anything the parser misses. The timeline view should make an order's whole life legible at a glance: origin → revisions → splits (`Q3 / Q3 ADD / Q3 ADD AIR` style legs) → final.
6. **Domestic vendor orders** — same order + timeline machinery with a simpler generation step (manual or MOQ-rule based per the DOMESTIC category rules), per-vendor contact and email thread.
7. **Canada seams** — model `destination` on orders (III vs. CAN) and stub the USA→CAN flow (SO + transfer + customs paperwork placeholder) without building it out.

Acceptance: parity tests green; generating an order from the seeded snapshot reproduces known-good quantities; a simulated reply email ("we can only send 200 of the 500 lamps, and dhoop sticks are discontinued") yields two correctly-parsed proposals with quotes, and confirming them updates the timeline; export opens cleanly in Excel with the agreed columns.

---

## PROMPT 5 — Reporting, inventory tools, and dashboard

1. **Sales dashboard** — by channel (Shoppe, online, city centers), category, product, and time period, straight from the Odoo snapshot. Auto-generated narrative summaries and suggested action items (LLM, clearly labeled as generated), plus an in-dashboard Q&A box that answers questions against the data ("which centers grew fastest this quarter?"). Match the spirit of the June 2026 sales report reference: highlights up top, drill-down below.
2. **Inventory Time Machine** — pick any date: past dates reconstruct inventory from snapshot history; future dates (up to 6 months) project from the forecasting engine net of incoming shipments. One slider, one honest confidence indicator.
3. **OOS list** and **Coming Soon list** — live views from the snapshot + incoming moves, filterable, subscribable (email digest), and exposed via API for skubot.
4. Amazon and Canada data sources: stub the ingestion interfaces, don't build.

Acceptance: dashboard loads in under a second on seed data; time machine's past view matches a known snapshot fixture and its future view matches the engine's projection; OOS/coming-soon endpoints return correct JSON for the bot.

---

## PROMPT 6 — SKU Bot integration

Refactor skubot to be a thin WhatsApp client over the app's API (keep its existing lookup UX), running on the same `WhatsAppTransport` the notification service uses — one connection, one abstraction, so the eventual move to the official Business API swaps a single implementation for both bot and notifications. New bot capabilities, all via the API: incoming-inventory lookups ("when is X back?"), restock lists on demand, creating BWHSE→Floor transfer requests conversationally ("send 12 copper bottles to the floor" → drafts a request for floor/warehouse confirmation in the app), and order status notifications pushed to orderers/coordinators who opt in. Bot messages are authenticated by phone-number mapping to app users; anything that changes state requires an explicit confirm step in the chat. Label printing and replen rules remain out of scope.

Acceptance: from WhatsApp (or the bot's test harness): a stock lookup, an incoming lookup, and a transfer request that appears in the web app pending confirmation.

---

## Open questions

None blocking. Revisit later: migrating to the official WhatsApp Business API when the account is approved, and moving off the shared personal Odoo account to a dedicated one.

## Resolved decisions (for the record)

- Fresh repo; `ops`, `skubot`, and `ILscripts` are reference material.
- Full Odoo read/write access, but the app never validates anything — all created records stay in draft, always with a direct link to the record in Odoo. Testing via faked-client unit tests, a recorded-fixture simulator in CI, and gated `APP-TEST-` canaries against production (no staging instance exists).
- Odoo account: a repurposed personal account with human activity on it for now — hence the `ILAPP-` reference prefix on everything the app creates, and the app's own audit log as source of truth.
- Database on Supabase Postgres; auth via Supabase one-time codes to email or phone, fresh code per login.
- App + worker hosted on an on-campus box, exposed via Cloudflare Tunnel (or equivalent) since users are continent-wide.
- Dedicated app mailbox: approved — gates the Prompt 4 email features.
- Notifications: WhatsApp first, email fallback. WhatsApp runs on skubot's existing **unofficial bridge for now**; an official Business API account is in progress — build behind a `WhatsAppTransport` interface so the official Cloud API is a drop-in swap, and treat bridge disconnection as an expected condition (auto email fallback, admin health status).
- Sales history: 24 months exists in Odoo; one full backfill at setup, then small hourly incremental pulls (current month only; previous month once daily).
- `USA INV CHK` workbook is the live ordering spec — use it for parity fixtures.
- Clothing: out of scope entirely for now. Excluded from all flows; just don't paint the architecture into a corner.
