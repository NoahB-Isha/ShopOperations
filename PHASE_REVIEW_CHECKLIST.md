# Phase Review Checklist

Run this after Claude reports a phase complete, **before** committing and starting the next phase.
Don't take "done" on faith — walk it yourself. Anything that fails goes back into the same session
as feedback; start the next phase only in a fresh session once everything passes.

---

## Every phase (the universal gate)

- [ ] `make dev` brings the whole stack up from a clean clone with only `.env` configured
- [ ] `make test` passes — and I actually watched it run, not just read Claude saying it passed
- [ ] `make seed` produces a working demo with zero real credentials
- [ ] All acceptance criteria from the phase prompt verified **by me, by hand**, one by one
- [ ] I clicked around as each affected role — nav, permissions, and row-scoping look right
- [ ] Tried to break it: empty form submits, absurd quantities, a product that doesn't exist,
      double-clicking action buttons, phone-width viewport on the volunteer-facing screens
- [ ] Anything that writes to Odoo or sends email/WhatsApp is feature-flagged and defaults OFF
- [ ] No secrets in the repo, in logs, or in URLs (grep for the Odoo password to be sure)
- [ ] New mid-build decisions recorded in `DECISIONS.md`
- [ ] Claude updated `CLAUDE.md` with anything it learned about the real Odoo schema this phase
- [ ] Code skim: module boundaries still clean, nothing bypasses `OdooWriter`, no dead scaffolding
- [ ] Committed with a clear message; repo is in a state a stranger could pick up

## Phase 1 — Foundation

- [ ] OTP login works with an email address AND with a phone number; codes expire; sessions persist
- [ ] Coordinator sheet import: spot-check 5 centers against the spreadsheet, including one messy
      row (missing email), one inactive center, and the Austin/San Antonio shared-products pair
- [ ] Sync runs against fixtures with no Odoo credentials set, and health endpoint reports honestly
- [ ] Catalog search feels instant at 1,000+ products; app-level tags save and persist
- [ ] `/styleguide` renders; the design has a point of view (would I call it elegant, not "bootstrap")
- [ ] **Early canary (do this now, not in Phase 2):** with real credentials on my machine —
      `POST` one `APP-TEST-` draft transfer, see it in Odoo, follow the deep link, unlink it,
      confirm the audit log recorded all of it

## Phase 2 — Order lists, transfers, restock

- [ ] Full loop on seed data: floor requests 10 → warehouse fulfills 9 → staging counts 8 →
      discrepancy of 1 sits in the adjustments queue with the story visible
- [ ] Approving an order list creates a real draft transfer in Odoo (flag on) with working link;
      with the flag off, the UI says clearly that the write was simulated
- [ ] Restock lists match a hand-computed check for one day of seed sales; check-off resets daily
- [ ] Kill switch flipped off mid-demo → every write becomes a dry-run, UI stays honest

## Phase 3 — City & department ordering

- [ ] On my actual phone: place a city order start-to-finish in under a minute
- [ ] Duplicate-order button reproduces a past order in two taps
- [ ] Coordinator's world is exactly three views, nothing more crept in
- [ ] OOS timeline shows a believable "back mid-August" date derived from incoming moves
- [ ] Reasonability score fires on the deliberately absurd seed order, stays quiet on normal ones,
      and reads as advisory — not scolding
- [ ] Dept order of a non-Odoo item (water) completes without any Odoo write
- [ ] A WhatsApp "order approved" message actually arrived on a real phone; killed the bridge →
      email fallback fired and the admin status page showed the outage
- [ ] Pilot plan: pick ONE friendly city center to use this for a week before wider rollout

## Phase 4 — India ordering & order tracking

- [ ] Parity test green against the current `USA INV CHK` export — all fully-numeric SEA rows
- [ ] Cross-check 3 SKUs by hand: app suggestion vs. what the workbook says today
- [ ] A known-seasonal product shows a seasonal forecast diverging sensibly from baseline, with
      the baseline visible alongside and a divergence flag
- [ ] New product with no history: LLM analog proposal appears, requires my confirmation, and is
      visibly flagged as forecast-by-analogy
- [ ] Order email dry-run renders correctly with CSV + XLSX attached; XLSX opens clean in Excel
      with the ORDER LIST columns
- [ ] Fed the parser a simulated Coimbatore reply ("only 200 of 500 lamps; dhoop discontinued") →
      two proposals with supporting quotes → confirming updates the timeline; rejecting leaves
      order state untouched
- [ ] Fed the parser an email containing an instruction ("please reorder everything") → recorded
      as a fact for review, nothing executed
- [ ] Timeline of a multi-revision seed order is legible at a glance to someone who wasn't there

## Phase 5 — Reporting & inventory tools

- [ ] Dashboard loads in under a second on seed data; numbers spot-check against raw snapshot rows
- [ ] LLM summaries and action items are clearly labeled as generated; Q&A answers a question
      correctly and admits it when the data can't answer
- [ ] Time machine: a past date matches a known snapshot fixture; a future date matches the
      engine's projection; confidence indicator is honest at both ends
- [ ] OOS and coming-soon endpoints return correct JSON (these are skubot's future inputs)

## Phase 6 — SKU bot

- [ ] Lookup, incoming-inventory query, and restock list all work from a real WhatsApp chat
- [ ] "Send 12 copper bottles to the floor" creates a pending request in the web app — after an
      explicit confirm step in chat, never before
- [ ] An unknown phone number gets politely refused; state-changing commands require a mapped user
- [ ] Bot and notifications share one WhatsApp session/transport — one presence, not two
- [ ] Notification volume ramps gradually (unofficial bridge — protect the number)

## Before real users touch it (deployment gate)

- [ ] Cloudflare Tunnel up; app unreachable except through it; HTTPS everywhere
- [ ] Campus box reboots → everything comes back on its own (test by actually rebooting)
- [ ] `pg_dump` backup ran on schedule to storage that is not the campus box; I restored one to
      prove restores work
- [ ] Odoo account password changed as a drill → sync failure screamed on the status page
- [ ] A volunteer who has never seen the app completed their core task without me talking
