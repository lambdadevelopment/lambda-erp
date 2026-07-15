# Company setup — localization packs + sector profiles

`lambda_erp/accounting/setup/` builds a new company's chart of accounts. It
replaces the single hardcoded `STANDARD_CHART` with a three-layer design so that
shipping one jurisdiction today does not force a rewrite to add the next.

## The three layers

```
account_type spine   universal Frappe taxonomy (root_type + account_type).
   │                 Never per-country; drives every report and posting rule.
   │                 → spine.py
   └─ LocalizationPack   registry keyed by country[.variant] (generic, ch,
        │                de.skr03, …). Base chart + anchors + defaults + tax hook.
        │                → pack.py, packs/*.py
        └─ SectorProfile   jurisdiction-INDEPENDENT operating-mode lens (~7).
                           Attaches accounts to pack *anchors*, never to a code.
                           → profiles.py
```

**Why it holds across countries:** a profile says "this business needs a
work-in-progress asset" in pure spine terms — an *anchor* (`CURRENT_ASSETS`) plus
an `account_type` (`Stock`) — and never names an account code. Each pack maps the
anchors to its own real group accounts (English "Current Assets", German
"Umlaufvermögen"), so the same seven profiles apply on every pack unchanged.

**Generic is not a prototype.** The generic pack is instance #1 in the registry
*and* the permanent fallback for any unlocalized country (`resolve_pack` returns
it when a country has no pack). A real international business can run on it
indefinitely; country packs sit beside it, never replace it.

## Entry points

- `plan_company_setup(name, country?, variant?, sector?, currency?)` — **preview,
  no writes.** Returns the account outline, sector-added accounts, guidance, and
  the big decisions the chat must confirm.
- `apply_company_setup(...)` — writes accounts + company defaults + pack tax +
  cost center in one transaction. Idempotency guard: refuses if the company
  already has accounts unless `force=True`.

Surfaces: the chat wizard (`api/chat.py` tools `plan_company_setup` /
`apply_company_setup`, admin-only) and `POST /api/setup/company` (the `country`
field selects the jurisdiction; `sector` applies the overlay). With neither, both
paths are byte-identical to the legacy `setup_chart_of_accounts`.

`packs/ch.py` is the single-chart worked example — a hand-authored Swiss KMU
chart with a German anchor map, CHF, `CH_DEFAULTS` covering every posting
default, and an MWST `setup_tax` hook building Sales/Purchase `Tax Template`s.
Copy its shape for the next country.

`packs/de_skr03.py` + `packs/de_skr04.py` are the **multi-variant** worked
example: one country, two registered charts (`country="de"` with
`variant="skr03"`/`"skr04"`), so `resolve_pack("de")` lands on the
alphabetically-first variant (SKR03) and `resolve_pack("de", "skr04")` selects
the other. Their identical VAT mechanics factor into `packs/de_common.py`
(`make_de_setup_tax(sales, purchase)`), which each variant calls with its own
tax-account leaf names — copy that split when a country's variants share a tax
regime but differ in numbering.

## Adding a jurisdiction (the whole point)

1. Create `packs/<country>.py` (or `packs/<country>_<variant>.py`, one module per
   variant — see the German pair). Hand-author the base chart tree (or adapt
   reference data — mind the license), map every `spine.ANCHORS` entry to a real
   group account in that tree, set the company `defaults`, and — if the
   jurisdiction has a standard VAT/GST regime — write a `setup_tax(company,
   currency)` hook (Python; flat data can't express reverse-charge / fiscal
   positions — that's the "hybrid" half).
2. `register_pack(LocalizationPack(country="de", variant="skr03", ...))`. Register
   one pack per chart; a bare `country` (no variant) is the default, else the
   first variant by key wins for a bare `resolve_pack(country)`.
3. Import it in `packs/__init__.py`.

No change to the engine, the profiles, or the chat. That's the invariant to
protect: **country-specific knowledge lives only in packs.**

## Adding / editing a sector profile

Add a `SectorProfile` in `profiles.py`. Each account needs an `anchor` from
`spine.ANCHORS` and an `account_type` from `spine.ACCOUNT_TYPES` — **never a
literal code** (the portability guard in `test_company_setup.py` enforces this).
`guidance` is the sector knowledge base the chat reads aloud; `big_decisions` are
the points it must confirm rather than auto-apply.

## Tests

`python -m tests.test_company_setup` — parity with the legacy chart, overlay
anchor resolution, default overrides, generic fallback, variant keying, plan
writes-nothing, idempotency, and the profile portability guard. Self-contained
(in-memory SQLite, no pytest/fastapi).
