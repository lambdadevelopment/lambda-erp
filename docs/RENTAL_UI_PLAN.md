# Rental UI plan — visualizing assets, availability, and hires

- **Status:** Proposed · Phase 0 in progress
- **Date:** 2026-08-17
- **Relates to:** [`adr-0002-asset-and-reservation.md`](./adr-0002-asset-and-reservation.md) (the `Asset` + `Reservation` primitives this UI visualizes)

## Context

Core 0.7.0 (ADR-0002) added unit-level `Asset` identity and a date-ranged
`Reservation` primitive, plus the availability engine (`available_assets`,
`available_qty`, `overlapping_reservations`). Both doctypes are reachable via
REST, chat, and MCP — a chat query like *"which Kubota KX 042 is free 21.–23.
Aug?"* already works.

What's missing is **any visual layer**. The frontend has:

- generic, metadata-driven `masters/:type` and `app/:doctype` list/form pages,
- 9 reports — **all financial tables** (P&L, balance sheet, agings, …),
- **no time-based view of any kind** (no calendar, timeline, or Gantt), and
- **no `Asset`/`Reservation` entry** in the frontend doctype registry
  (`src/lib/doctypes.ts`) or nav — so today they're invisible in the UI.

The trigger is the BauRent AG Ost rental example (construction equipment;
~185 machines; hourly + 1/5/20-day tiers; multiple yards). A hire desk's core
question is *which unit, and is it free then* — an inherently visual, time-based
question the current UI cannot answer.

## Placement principle

`Asset`/`Reservation` are **core** doctypes, so their generic pages and the
availability calendar (which only visualizes core reservation data) belong in
**`erp-core`** — every asset/reservation deployment benefits. Rental-*specific*
pricing (duration tiers + mobilisation fee), utilization/pipeline dashboards,
and dispatch/return are **Tier B → a deployment plugin** (per ADR-0002 scope).
Pragmatic path: prototype rental-specific bits in the deployment, upstream the
generic parts to core once proven (same internal→core flow the backend tiering
used).

## The calendar visualization — library choice

The headline view is a **resource timeline** (machines on the Y axis, time on
the X axis, reservations as bars), NOT a month grid. That distinction matters
for licensing: the popular libraries give away the plain calendar but put the
*resource-timeline* view behind a paywall.

| Library | License | Verdict |
|---|---|---|
| **React Big Schedule** | **MIT** | ✅ Recommended — React-native resource scheduler, purpose-built for resources × time with event bars + drag. Fastest to this exact view. |
| **vis-timeline** | **Apache-2.0 OR MIT** | ✅ Fallback — mature/battle-tested engine; `groups`=machines, range items=reservations. Framework-agnostic → thin React wrapper. |
| MUI X Scheduler (Community) | MIT | ⚠️ Resource-aware layouts are free but it's v9-alpha and the dense timeline is Premium. Revisit as it matures. |
| visx / D3 | MIT / BSD | ⚠️ Build-your-own; max control, max effort. Last resort. |
| FullCalendar resource-timeline | **Commercial** | ❌ Standard views are MIT; resource-timeline/scheduler is paid (no free commercial use). |
| Schedule-X resource scheduler | **Paid plugin** | ❌ Core calendar MIT; the resource view is premium. |
| Planby | Commercial (PRO) | ❌ Now markets a paid PRO. |
| DHTMLX / Bryntum / Syncfusion / Mobiscroll / DayPilot | Commercial | ❌ Paid for production. |

**Decision:** prototype with **React Big Schedule** (MIT); keep **vis-timeline**
(Apache-2.0/MIT) as the fallback if we hit customization/scale limits. Avoid
FullCalendar / Schedule-X / Planby for the timeline specifically.

## Phased plan

### Phase 0 — Register `Asset` + `Reservation` in the frontend (prereq) — `erp-core`
Add both doctypes to `src/lib/doctypes.ts` (fields, list columns, party/link
metadata) and to the nav. This immediately yields generic list + form pages via
the existing metadata-driven components — the foundation everything else builds
on. **Effort:** ~½–1 day.

### Phase 1 — Fleet & hire basics — `erp-core` + small backend
- **Asset list** + **Asset detail** — rental-aware columns (status, yard, meter
  hours, booking history).
- **Reservation list** + **Booking form with live availability** (form checks
  for conflicts before saving).
- *Backend:* one **availability REST endpoint** wrapping the existing
  `available_assets` / `available_qty` / `overlapping_reservations` (logic
  exists in core; no HTTP route yet). Unblocks Phases 1 and 2.

**Effort:** ~2–4 days.

### Phase 2 — The availability calendar (headline, net-new UI) — `erp-core`
- **Fleet availability calendar/Gantt** — bars by status, filter by
  yard/category, click gap → book, click bar → open hire.
- **Availability search ("what's free")** — visual form of the chat query.
- **Per-machine timeline** — one asset's past + upcoming, maintenance inline.
- *Backend:* a reservations/asset **feed endpoint** (date-window query).

**Effort:** ~1–2 weeks (the real build; see library choice above).

### Phase 3 — Rental dashboards — deployment plugin (Tier B)
- **Fleet status board** (grid/kanban by status per yard).
- **Utilization dashboard** (% utilization per machine/category/yard, idle
  units, revenue-per-asset).
- **Hire pipeline & revenue** (upcoming hires, expected revenue, **overdue
  returns**).
- **Meter & maintenance-due** (units nearing service intervals via
  `meter_reading`).
- *Backend:* aggregation endpoints — the genuinely rental-specific analytics.

**Effort:** ~1–2 weeks.

## Sequencing

`0 → 1 → 2` is the critical path to a demo-able visual product (~2–3 weeks, one
frontend dev). `3` is the operations/analytics layer and can follow. The two
net-new engineering efforts are **Phase 2 (the timeline)** and **Phase 3
(rental analytics)**; Phases 0–1 are largely configuration on the generic infra
plus the single availability endpoint.

## Out of scope here (tracked elsewhere / later)
- Duration-tiered pricing display + mobilisation fee on the Item page (Tier B
  pricing plugin; duration tiers already expressible as core Pricing Rules).
- Dispatch/return workflow, deposits, damage (Tier B, ADR-0002 non-goals).
- Customer self-service booking portal (large; only if self-service is wanted).
