# ADR-0002 — Unit-level asset identity and a date-ranged reservation primitive

- **Status:** Accepted — implemented (Tier A)
- **Date:** 2026-08-12
- **Owner:** Jonathan
- **Relates to:** [`core-extension-architecture.md`](./core-extension-architecture.md); the
  tiering method is the one ADR-0001 established in `lambda-erp-internal`.

This ADR records why the core grew an `Asset` master and a `Reservation`
primitive, why they are deliberately *not* built on the stock ledger, and where
the line sits between what landed here and what belongs in a deployment repo.

---

## Context

A rental/hire business (the trigger was a Swiss construction-equipment rental
price list: ~200 machines, hourly and daily rates, 1/5/20-day price tiers, three
yards) can already be modelled here up to a point. What worked out of the box
was more than expected:

- **Items** carry the model, its specs, its UoM and its rate.
- **Pricing Rules already do duration tiers.** `min_qty` with
  `ORDER BY priority DESC, min_qty DESC LIMIT 1`
  (`controllers/pricing_rule.py`) is exactly a price-break ladder; with qty =
  rental days, the "ab 1 / ab 5 / ab 20 Tagen" ladder is three ordinary rules.
- **Warehouses** model the yards, **Subscription** covers long-term hire, and
  the whole Quotation → Order → Invoice cycle needs no change.

Two things did not exist, and no amount of configuration produces them:

1. **Unit-level identity.** `serial_no` / `batch_no` exist as *columns* on Stock
   Ledger Entry and are passed through by `stock/stock_ledger.py`, but there is
   no master behind them, no allocation, and no uniqueness. You cannot ask
   "which of our three 1.7 t excavators is this?".
2. **Availability over a date range.** `Bin.reserved_qty` is maintained by Sales
   Order submit/cancel (`selling/sales_order.py`) but is a **scalar with no
   dates**. It answers "how many are spoken for", never "is one free from the
   14th to the 19th" — which is the only question a hire desk asks.

## Decision

**Add an `Asset` master and a `Reservation` primitive to the core. Keep both
entirely out of the stock ledger and the general ledger.**

### 1. Asset, not Serial No

These are different concepts and conflating them is the expensive mistake:

| | Serial No | **Asset** |
|---|---|---|
| Models | unit identity of *inventory* | an owned unit that isn't consumed |
| Lifecycle | bought → held → **sold, retired** | out → back → out → back |
| Valuation | rides the stock ledger; sale posts COGS | fixed asset; never COGS'd |

Rental machines are Assets. Had we built hire on Serial No and routed dispatch
through a Delivery Note, every machine leaving the yard would deplete stock
valuation and post COGS — reporting a sale that never happened. So `Asset` is
its own master in its own package (`lambda_erp/assets/`, deliberately not
`lambda_erp/stock/`), and **serial-tracked sellable goods remains a separate,
still-open feature**. The README's "Serial & batch tracking" todo is unchanged
by this ADR.

`Item.is_fixed_asset` already existed as an inert column (declared in the schema,
referenced by zero lines of logic). It stays inert; the new flag is explicit.

### 2. Tracking is opt-in per Item, defaulting to off

`Item.is_asset_tracked INTEGER DEFAULT 0` (migration 21). Turning identity on
globally would be a breaking change dressed as a feature: every Purchase Receipt
and Stock Entry in every existing deployment would suddenly need unit
identities, and the deterministic demo simulation (`seed=42`, pinned by
`docs/agents/invariants.md`) would break. It is also simply wrong for a bag of
screws.

Creating an Asset against an Item that has not opted in is refused with a
message naming the flag to set. Verified against a database written by the
previous release: pre-existing items come through with `is_asset_tracked = 0`,
still refuse Assets, and opting in is a single field flip with no data
migration.

### 3. Reservation is two-level: pooled and unit

Hire desks commit capacity before they commit a machine:

- **pooled** — `item_code` + `warehouse` + `qty`: "*a* 1.7 t excavator out of
  St. Gallen, 14.–19." This is what a quotation or order holds.
- **unit** — `asset` set: "*that* machine." Assigned at dispatch, or earlier if
  the customer insists.

A unit reservation also consumes one slot of its pool, so the two levels stay
consistent: three pooled bookings plus one pinned machine is four units of
capacity, not one plus three. Starting unit-only and retrofitting pooled later
would have meant reworking every caller, which is why both levels are in the
first cut.

### 4. Windows are half-open, `[from, to)`

Two windows collide when `existing.from < new.to AND existing.to > new.from`. A
hire ending at 09:00 and the next starting at 09:00 do not clash — the behaviour
a dispatcher expects. All bounds are normalised to fixed-width
`YYYY-MM-DD HH:MM:SS`, which makes the overlap test a plain TEXT comparison that
behaves identically on SQLite and Postgres. A bare date means midnight, so
`2026-08-14` and `2026-08-14 00:00:00` are one instant rather than two
incomparable strings. Timezone suffixes are dropped: a hire window is wall-clock
time at the yard.

### 5. It posts nothing

No GL entry, no Stock Ledger Entry, no Bin mutation. A commitment is not a
transaction; the invoice that eventually bills the hire is one, and it flows
through the ordinary selling cycle untouched. This is what makes the change safe
to land in the core:

- It touches **none** of the rules in `docs/agents/invariants.md` — no balancing
  requirement, no cancel chain, no valuation path, no return caps.
- It is purely additive: two new tables plus one defaulted-off column.
- The validation suite asserts the GL and SLE counts are unchanged across the
  whole assets section, so the invariant is enforced, not just intended.

### 6. `Bin.reserved_qty` is left alone

It stays the Sales Order's stock semantic; Reservation is the date-aware layer
beside it. Unifying them would drag the reservation calendar into the Sales
Order lifecycle for no gain, and would change behaviour for deployments that
have no assets at all.

### 7. Asset is a doctype, not a registered master

`Asset` and `Reservation` go in `DOCUMENT_CLASSES` only. Adding `asset` to
`MASTER_TABLES` was considered and rejected: `create_master_record` writes
without running `validate()`, which would open a second, unvalidated write path
straight past the opt-in guard, the tag-uniqueness check and the overlap check.
One validated write path is worth more than the master CRUD surface here. Both
are still fully reachable from chat and MCP, which drive registered doctypes.

Deleting an Item or Warehouse that an Asset or Reservation references is now
blocked by `DELETE_REFERENCE_CHECKS`, consistent with every other reference.

## Scope — what is deliberately NOT here

Tier A is **identity plus a calendar**. Everything below is a deployment
concern (Tier B) or later core work, and keeping it out is what makes this
reviewable and generic:

- depreciation and asset accounting
- maintenance scheduling (a service slot is just a Reservation with no party)
- hour-meter / usage-based billing, minimum-hours rules, shift bases
- deposits, insurance deductibles, damage handling
- mobilisation / handling fees
- dispatch and return documents, and the assignment of a pooled booking to a
  specific unit at dispatch
- any UI. The frontend has no Asset page or availability calendar yet; both
  doctypes are reachable via the REST API, chat and MCP.

## Consequences

- A rental deployment can now ask the two questions that were unanswerable:
  *which unit* and *is it free then*. Everything above that (dispatch, metering,
  billing) is buildable on plugin seams without further core changes.
- Deployments that never set `is_asset_tracked` are bit-for-bit unaffected.
- Pooled reservation of ordinary, untracked stock is **not** supported:
  capacity is counted from Asset rows, so an item with no units has no pool.
  That is a deliberate boundary, not an oversight — if it is ever wanted, it
  should reuse `Bin` rather than this table.

## Verification

- `python -m tests.test_erp_validation` — exit 0, trial balance balanced, with a
  new section covering the opt-in guard, duplicate tags, unit double-booking,
  abutting windows, pool exhaustion, per-yard isolation, self-edit, release, and
  the no-GL/no-SLE invariant.
- Upgrade path exercised explicitly: a database written by the previous release,
  reopened with this code, gains migration 21 and both tables with legacy rows
  intact and untracked.
