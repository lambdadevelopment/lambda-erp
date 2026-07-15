# Lambda ERP

**Open-source ERP you can run through chat — configurable in plain language**

Lambda ERP is a simpler ERP: create invoices, check inventory, answer accounting questions, and change reports by asking for what you need in plain language.

https://github.com/user-attachments/assets/1b2749ef-10e7-42f5-9cce-df5628292667

<p align="center">
  <a href="https://lambda-erp-demo.grayocean-53ec71ac.northeurope.azurecontainerapps.io/demo">
    <img alt="Try the Live Demo" src="https://img.shields.io/badge/%E2%96%B6%20Try%20the%20Live%20Demo-4fc3f7?style=for-the-badge&labelColor=000000">
  </a>
</p>

> Click **Enter Live Demo** for a 40-second scripted walkthrough and prompt freely after (rate-limited to ~$50/day of LLM spend across all visitors).

**Join the discussion** on Discord — report bugs, share prompts that broke (or surprised) the agent, or just see what other early users are building:

<p>
  <a href="https://discord.gg/ZwFh9hZJTb"><img alt="Join the Lambda ERP Discord" src="https://img.shields.io/discord/1496911123029557359?color=7289DA&label=Join%20on%20Discord&logo=discord&logoColor=white&style=for-the-badge"></a>
</p>

The two packages publish in lockstep on every release:

<p>
  <a href="https://pypi.org/project/lambda-erp/"><img alt="lambda-erp on PyPI" src="https://img.shields.io/pypi/v/lambda-erp?label=PyPI%20%7C%20lambda-erp&logo=pypi&logoColor=white"></a>
  <a href="https://www.npmjs.com/package/@lambda-development/erp-core"><img alt="@lambda-development/erp-core on npm" src="https://img.shields.io/npm/v/@lambda-development/erp-core?label=npm%20%7C%20%40lambda-development%2Ferp-core&logo=npm"></a>
</p>

Want to see them wired together? **[lambda-erp-example](https://github.com/lambdadevelopment/lambda-erp-example)** is a minimal reference deployment that consumes both packages via version pins (no fork, no vendoring) — a ready-made template for your own deployment.

---

## Why another ERP?

Today, the bulk of an ERP rollout isn't the software license — it's everything that has to happen on top of it before the system actually fits the company. For a small or mid-sized company, the license is typically a few thousand a year, while getting it set up routinely runs **$10-50k** — many times the annual license spend before anyone has logged in. The system also keeps evolving after go-live — each custom report, country-specific tax rule, or workflow change is its own round of work. Larger systems like Oracle NetSuite and Microsoft Dynamics follow the same shape, and S/4HANA-class projects run into the millions.

ERPs cost what they cost because they're generic platforms that have to be bent into the shape of each company. The work is configuration, workflow design, custom reports, data migration, integrations — language and structure work. It's been slow and manual because software couldn't do it. Until recently.

We think that's about to flip. The bulk of an ERP implementation is text-transformation and configuration that LLMs are now genuinely good at:

- **Reading documents.** A supplier PDF becomes a Purchase Invoice. A bank statement becomes reconciled journal entries. An onboarding form becomes a Customer record.
- **Chart of Accounts design and mapping.** Taking a client's legacy accounts, translating to local GAAP, producing the mapping table - pure language-plus-structure work.
- **Master data migration.** Cleaning, deduplicating, and loading customer/supplier/item masters and opening balances from whatever mess the legacy system coughs up.
- **Custom reports and print formats.** The bespoke chart, the specific invoice layout, the cash-flow view nobody else has - unbounded client taste, infinite long tail.
- **End-user training and ongoing questions.** "How do I issue a credit note?" "Why is this balance off?" "What was last quarter's margin by product line?" - natural-language lookups that used to need a help-desk hour.

Each of those is hours of skilled work today. With a capable LLM in the loop, the same task takes seconds of compute and a review pass from someone who knows the business. The software starts tailoring itself to you, instead of the other way around. And because Lambda ERP is open source and self-hosted, the configuration doesn't stop at go-live — the system can keep evolving alongside the company. For implementation partners, the shape of the work shifts: less time spent hand-writing every change, more time spent on the judgment calls — chart-of-accounts design, compliance, change management — that actually need a human.


---

## How it works

```
┌────────────────────────────┐    ┌────────────────────────────┐
│  React + Vite frontend     │◄──►│  FastAPI backend           │
│  - Document forms          │    │  - Document CRUD API       │
│  - Reports / Analytics     │    │  - Report endpoints        │
│  - Chat (WebSocket)        │    │  - Auth (JWT cookie)       │
│  - Client-side JS runtime  │    │  - WebSocket chat gateway  │
│    (Web Worker) for charts │    └────────────┬───────────────┘
└────────────────────────────┘                 │
                                               ▼
                           ┌──────────────────────────────────────────┐
                           │  LLM orchestrator                        │
                           │  - GPT-5.4 drives the reasoning loop     │
                           │  - Tool-use: document CRUD, search,      │
                           │    reports, aggregations, analytics      │
                           │  - Delegates JS generation to Anthropic  │
                           │    code-specialist sub-agent             │
                           └──────────────────┬───────────────────────┘
                                              │
                                              ▼
                           ┌────────────────────────────────────────────┐
                           │  lambda_erp/ (pure Python, no framework)   │
                           │  - Document base class + lifecycle         │
                           │  - General Ledger (double-entry)           │
                           │  - Stock Ledger (moving average)           │
                           │  - Tax calculation engine                  │
                           │  - Pluggable DB backend (SQLite / Postgres)│
                           └────────────────────────────────────────────┘
```

**Key design choices:**

- **Chat-first, not chat-bolted-on.** The chat isn't a copilot sidebar - it's the primary way to interact with the system. Every document type, every report, every master record is reachable from tool-use.
- **One shape for every document.** Invoices, sales orders, stock entries, payments - all share a single `Document` base class and the same three-state lifecycle (Draft → Submitted → Cancelled) with `on_submit`/`on_cancel` hooks. The LLM learns the pattern once and drives every doctype the same way. Leading open-source and commercial ERPs have per-model action verbs spread across 150+ core models; each one is a separate tool the model has to get right.
- **Metadata-driven UI, shared with the LLM.** A single React form component renders every doctype from `frontend/src/lib/doctypes.ts`. The schema the model reasons over and the schema the user sees are literally the same file. Adding a field is two lines - one in the Python class, one in the config - not a new module with hand-written views and inheritance overlays.
- **Two-model orchestration.** A planner model handles reasoning and tool-use. When it needs to generate code for a custom report, it delegates to a code-specialist sub-agent. This keeps each model doing what it's best at and keeps latency down on simple turns.
- **Semantic datasets, not free SQL.** The LLM can't write raw SQL; it calls whitelisted semantic datasets (`sales_invoices`, `purchase_invoices`, `ar_open_items`, `stock_balances`, etc.) with whitelisted filters and group-bys. This makes the system auditable without sacrificing flexibility.
- **Client-side analytics runtime.** Custom report JS executes in a sandboxed Web Worker in the user's browser. The server never runs untrusted JS. Charts are persisted as portable specs, not screenshots.
- **Double-entry invariant enforced.** Every submitted document that touches the GL must balance to zero. The engine adds round-off entries for rounding gaps and refuses to post imbalanced vouchers.
- **One deployment per customer, simple to operate.** Lambda ERP is built to be self-hosted by a single company for its own books - not as a multi-tenant SaaS. One FastAPI process, one database, one VPS is enough. If you want a hosted offering, we'll ship a dedicated instance per customer.

### Tech stack at a glance

| Layer | What |
|---|---|
| Backend business logic | Pure Python, no framework (`lambda_erp/`) |
| Web API | FastAPI + Pydantic |
| Storage | SQLite by default; Postgres for production |
| Frontend | React + Vite + TypeScript + Tailwind + Recharts |
| Chat transport | WebSocket |
| LLM orchestrator | OpenAI (configurable) |
| Code specialist | Anthropic (configurable) |
| Auth | JWT httponly cookie, three roles + demo |

---

## Set up your books by describing your business

Standing up a new company usually means an accountant hand-building a chart of accounts. In Lambda ERP you do it in the chat: tell the assistant what kind of business you run and which country you're in, and it proposes the exact accounts — explaining the structure and the judgment calls, and creating nothing until you approve. It's the "chart-of-accounts design and mapping" work from above, done in seconds and reviewed by you.

Two dimensions shape the chart, and they compose:

- **Localization packs** bring a country's real chart and tax setup. Today: a **generic / international** chart (also the permanent fallback for anywhere not yet localized); **Switzerland** — the *Kontenrahmen KMU*, in German, in CHF, with MWST tax templates at the current rates; and **Germany** — the DATEV standard in both common variants, **SKR03** and **SKR04**, in German, in EUR, with Umsatzsteuer/Vorsteuer templates at 19 % and 7 %. Where a country ships more than one standard chart (Germany's SKR03 vs SKR04), you pick the variant; a bare "Germany" defaults to the more widely used SKR03. Each country is a self-contained pack, so more can land without disturbing the ones already there — that's the roadmap, one jurisdiction at a time.
- **Sector profiles** tailor the chart to how you operate — **services, retail / POS, hospitality, wholesale / distribution, import / export, manufacturing, construction** — adding the accounts that model actually needs (work-in-progress and retention for construction, food-vs-beverage cost lines for hospitality, landed-cost accounts for import/export, and so on). Profiles are country-independent: the same seven apply on top of every localization.

The result is booked in one step — chart of accounts, sensible default accounts, tax templates, and a cost center — ready for opening balances. New here? The in-app **Tutorial** has a one-click **Get started** that opens the wizard in chat.

---

## What works today

- **Guided company setup through chat** — pick a country (generic, Switzerland, or Germany SKR03/SKR04, more coming) and business type, preview the exact chart of accounts, confirm, and it's booked
- Full sales and purchase cycles (Quotation → Sales Order → Delivery Note → Sales Invoice → Payment Entry, and the buying equivalents)
- Returns / credit notes / debit notes with proper GL and stock reversal
- Moving-average stock ledger with negative-stock protection
- Double-entry General Ledger with cancellation reversal
- Preset reports: Trial Balance, Profit & Loss, Balance Sheet, General Ledger, AR/AP Aging, Stock Balance
- Custom analytics drafts via chat (persisted, shareable, editable)
- Server-side aggregation tool for in-chat factual answers across large datasets
- PDF / image attachment → add invoices, create quotations, etc. all directly by adding them in the chat
- Auth with admin/manager/viewer roles plus a public demo mode
- Full test suite that exercises every cycle against an in-memory SQLite

## What's still todo (Suggestions welcome)

- MCP integration for supplier/customer communication (quotes, orders, confirmations)
- Multi-currency beyond the simplified current handling
- Workflows / approval chains
- Serial & batch tracking
- Manufacturing (BOM, work orders)
- HR / Payroll beyond the journal-entry workaround
- Regional compliance packs (GST, VAT returns, etc.) - see below
- PDF report creation directly inside the chat

---

## Why now

Four things had to be true for this to work, and they all became true in the last ~18 months:

1. **LLMs can reliably call tools.** A year ago, models would hallucinate tool calls, mangle JSON, or drift after 2–3 steps. Today's frontier models can run an 8-step reasoning loop over a real tool inventory without falling off.
2. **Costs collapsed.** Generating a custom report via a code-specialist sub-agent is cents of compute. Even keeping a human reviewer fully in the loop, the marginal cost of "one more report" or "one more dashboard" drops by orders of magnitude — which means companies actually ask for them, instead of living with the defaults.
3. **Structured output + function calling are first-class.** We can constrain the LLM's outputs to valid tool-call schemas, safe SQL parameters, and typed JSON - which is what makes an AI-native ERP even conceivable as a safe thing to run.
4. **Greenfield is finally cheaper than retrofit.** Twenty-year ERP codebases have hundreds of bespoke models and thousands of hand-written forms - teaching an LLM to drive that reliably means curating a custom tool layer over every quirk. Starting from scratch around one Document lifecycle and a metadata-driven UI is now cheaper than retrofitting an existing platform.

---

## Why open source

Every company configuring an ERP runs into the same problems: local tax rules, common workflow patterns, industry-specific accounting quirks. Most of that knowledge isn't a competitive advantage — it's the same ground being re-covered separately at every implementation, by every team, in slightly different ways.

We want Lambda ERP to be where that knowledge lives in public. The base system is MIT-licensed, and the repo has real seams for that knowledge to slot into: a new country's chart of accounts and tax rules is a self-contained **localization pack**, and an industry's way of operating is a **sector profile** — both live in `lambda_erp/accounting/setup/` and are documented for contributors in [`docs/agents/company_setup.md`](docs/agents/company_setup.md). An Austrian or French chart, a U.S. sales-tax-by-state module, a template for professional services — each is an additive contribution that every deployment picks up (the Swiss KMU and German DATEV packs already in the tree are the worked examples). The goal is a true community where running an ERP gets cheaper, faster, and more flexible for everyone, not just the implementer.

---

## Get it running

### Docker (one command)

You need Docker and Compose v2. The canonical installs:

- **Mac / Windows**: install [Docker Desktop](https://www.docker.com/products/docker-desktop/) — ships `docker compose` v2 built in.
- **Linux / WSL**: Docker's own one-liner installs both the engine and the Compose plugin:
  ```bash
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker $USER && newgrp docker
  ```
  (Ubuntu's `docker.io` apt package and Snap's `docker` omit Compose; use the script above instead.)

Then, from the repo root:

```bash
cp .env.example .env          # add your OPENAI_API_KEY and ANTHROPIC_API_KEY (for custom analytics)
docker compose up --build
```

First boot takes **~2-3 minutes** — the container runs a 3-year historical simulator to populate realistic demo data. Watch the logs; you'll see monthly `[sim]` progress lines and, when it's done, a clear banner:

```
======================================================
  Lambda ERP is READY — open http://127.0.0.1:8000 in your browser
======================================================
```

Hitting the URL before that banner will look like the page "hangs" — uvicorn isn't listening yet, so requests queue at the TCP layer until bootstrap finishes. Wait for the banner, then open the URL.

> **Use `127.0.0.1`, not `localhost`.** On WSL2 + Docker Desktop, browsers occasionally stall for minutes on WebSocket upgrades to `localhost` even when HTTP works fine. `127.0.0.1` has never been observed to misbehave.

The container serves both the UI and the API at the same origin - there's no separate frontend port in Docker mode. You'll land on the login page with **register-your-first-admin** enabled; create an account and that account becomes the admin for your instance.

If instead you want the hosted-demo experience (a shared `public_manager` account and the "Enter Live Demo" button), add `LAMBDA_ERP_ENABLE_PUBLIC_DEMO=1` to your `.env`.

State persists to a named Docker volume, so subsequent `docker compose up` starts in seconds. `docker compose down -v` wipes the volume to force a fresh re-seed.

### Local dev

Requires Python 3.10+ and Node 18+.

```bash
# Backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn api.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Vite proxies `/api/*` to the backend.

### Environment

```
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...    # optional, used for the code-specialist sub-agent
ANTHROPIC_CODE_MODEL=claude-opus-4-7   # optional, default shown
```

Chat needs `OPENAI_API_KEY`. Custom-report code generation uses `ANTHROPIC_API_KEY` when set; otherwise it falls back and the chat will tell you it can't generate reports.

### Run the validation suite

```bash
source .venv/bin/activate
python -m tests.test_erp_validation
```

Runs a full cycle in-memory: setup, sales cycle, purchase cycle, returns, submits, cancels, payments, trial balance. No credentials, no network, ~2 seconds.

---

## Repository layout

```
lambda_erp/         # Pure Python business logic (no framework)
  accounting/         # GL, journal entries, invoices, payments
  selling/            # Quotations, sales orders
  buying/             # Purchase orders
  stock/              # Stock ledger, stock entries
  controllers/        # Tax engine
api/                # FastAPI + WebSocket chat
  routers/            # REST endpoints
  chat.py             # LLM orchestrator, tool schemas, reasoning loop
frontend/           # React + Vite app
tests/              # Validation / regression suite
docs/agents/        # Invariants, gotchas, design decisions (LLM-readable)
```

`docs/agents/` is worth a read if you're going to contribute - it captures the invariants the code assumes but doesn't always enforce, plus the landmines that have bitten us.

---

## Building a customer deployment on top of the core

Lambda ERP is **one deployment per customer** (see the design choices above). A
customer that needs **core business-logic changes** should **not fork this
repo**. Instead, create a **separate private repo that depends on this one as
the core** and overrides behavior at defined seams. Core fixes then arrive via a
version bump, not a merge into a diverging fork. Full plan and rationale:
[`docs/core-extension-architecture.md`](docs/core-extension-architecture.md).

**Customer repo layout**

```
acme-erp/
  pyproject.toml          # depends on lambda-erp (git / path / PyPI)
  acme/
    plugin.py             # register() — wires backend overrides + hooks
    sales_invoice.py      # e.g. class AcmeSalesInvoice(SalesInvoice): ...
  frontend/
    package.json          # depends on @lambda-development/erp-core
    tailwind.config.ts    # scans @lambda-development/erp-core dist + adds its preset
    src/
      plugin.ts           # registers frontend overrides (doctypes/routes/nav/branding)
      main.tsx            # import plugin + styles, then bootstrap()
  config/                 # branding, enabled features, base currency, OAuth
  deploy/                 # Dockerfile, env/secrets
```

**Override core logic (replace) — subclass + register**

```python
# acme/sales_invoice.py
from lambda_erp.accounting.sales_invoice import SalesInvoice
class AcmeSalesInvoice(SalesInvoice):
    def _get_gl_entries(self):
        gl = super()._get_gl_entries()
        # customer-specific posting
        return gl
```

Registering it makes every loader path (`create/load/update/submit/cancel`) **and
document conversions** use the subclass.

**Add behavior (don't replace) — lifecycle hooks**

```python
from lambda_erp.hooks import register_hook
register_hook("Sales Invoice:after_submit", push_to_external_system)
```

Events are `"<DocType>:{before,after}_{save,submit,cancel}"`. `before_*` run
**inside** the document's transaction (a raise aborts and rolls back — use for
guards/validation); `after_*` run **post-commit** (the voucher is durable — use
for side-effects/integrations).

**Wire it up**

```python
# acme/plugin.py
from api.services import register_doctype, register_converter
from lambda_erp.hooks import register_hook
from .sales_invoice import AcmeSalesInvoice

def register():
    register_doctype("Sales Invoice", AcmeSalesInvoice)
    register_hook("Sales Invoice:after_submit", push_to_external_system)
    # register_converter(source, target, fn)   # only to replace conversion *logic*
```

Point the deployment at it with `LAMBDA_ERP_PLUGINS=acme` (comma-separated for
several). On startup the core imports each module and calls `register()`. Unset
= the core runs unchanged.

**Frontend overrides — the `@lambda-development/erp-core` library**

The frontend ships as a library. The customer app depends on it, registers its
overrides in a plugin module, then boots the shared app shell. The seams mirror
the backend: add/replace doctypes, routes, nav, whole components, branding, and
the API base — without editing core files.

```ts
// frontend/src/plugin.ts — runs before bootstrap()
import {
  registerDoctype, registerRoute, registerNavGroup, registerComponent,
  configureBranding, configureApiBase,
} from "@lambda-development/erp-core";
import AcmeDashboard from "./acme-dashboard";

configureApiBase(import.meta.env.VITE_API_BASE ?? "/api");
configureBranding({ appName: "Acme ERP", tokens: { "--brand": "260 80% 55%" } });

registerDoctype({ slug: "service-ticket", label: "Service Ticket", /* …schema… */ });
registerNavGroup({ label: "Service", icon: null, items: [{ label: "Tickets", path: "/app/service-ticket" }] });
registerRoute({ path: "reports/sla", element: <SlaReport /> });   // under the app shell
registerComponent("Dashboard", AcmeDashboard);                    // swap a core component
```

```tsx
// frontend/src/main.tsx
import "./plugin";                         // register overrides first
import "@lambda-development/erp-core/styles.css";      // base tokens + layers (your Tailwind processes it)
import "./acme.css";                       // optional: override :root tokens, add utilities
import { bootstrap } from "@lambda-development/erp-core";
bootstrap();                               // builds routes AFTER registration, then mounts
```

Styling follows the **"consumer scans source"** model — your app runs Tailwind
and the library provides the tokens and preset:

```ts
// frontend/tailwind.config.ts
import erpPreset from "@lambda-development/erp-core/tailwind-preset";
export default {
  content: ["./src/**/*.{ts,tsx}", "./node_modules/@lambda-development/erp-core/dist/**/*.js"],
  presets: [erpPreset],
};
```

Rebrand by overriding the `:root` CSS variables (`--brand`, `--surface`, `--text`,
…) — at runtime via `configureBranding({ tokens })` or statically in your own CSS.

**Wire up overrides:** registry registration at runtime (above) covers most
cases. For a build-time whole-module swap, point a Vite `resolve.alias` at your
replacement file.

**Rules**

- **Don't edit core files** — override at a seam. If what you need to change
  isn't a seam yet, add the seam to the core (a PR here), then override from the
  customer repo.
- Keep branding / feature toggles / base currency / auth config in `config`/env,
  not code.
- Bump the core version to pull fixes; never copy core code in.

Both the backend seams (document classes, lifecycle hooks, converters, plugin
loading) and the frontend seams (doctype/route/nav/component registries,
branding, configurable API base, Tailwind preset) are implemented. The backend
also builds a clean pip wheel and the frontend a `@lambda-development/erp-core` npm library
— see [`docs/packaging-distribution-plan.md`](docs/packaging-distribution-plan.md)
for the publish path.

---

## Contributing

This is early. The project needs:

- Country compliance packs (tax rules, invoice formats, mandatory fields)
- Industry templates (services, retail, light manufacturing, SaaS)
- More preset reports
- A Postgres storage adapter (the current SQLite layer is fine for local evaluation but will need to be swapped for real multi-user write loads)
- Better observability around token spend per turn
- Native messenger integration, for WhatsApp, Telegram, etc.

PRs welcome. File an issue first if it's a big change, or drop by our [Discord](https://discord.gg/ZwFh9hZJTb) to discuss ideas.

---

## License

MIT. See [LICENSE](./LICENSE) for the full text.

---

## Changelog

Release notes live in [CHANGELOG.md](./CHANGELOG.md). Releases are tagged
`vX.Y.Z` and published in lockstep to PyPI (`lambda-erp`) and npm
(`@lambda-development/erp-core`).

---

## Status

Version 0, and it already implements the vision: invoicing, inventory, accounting, and reporting — all driven by chat. Good for demos, internal tools, and real day-to-day work. It's under active development and improving fast.

If you try it, we'd love to know what broke.

---

## Trademarks and affiliations

Lambda ERP and [lambda.dev](https://lambda.dev/) are product and trade names of **TORUS INVESTMENTS AG**. It is not affiliated with, endorsed by, or sponsored by OpenAI, Anthropic, SAP, Oracle, Microsoft, or any other company named in this repository. SAP, Business One, S/4HANA, Oracle, NetSuite, Microsoft, Dynamics, OpenAI, GPT, Anthropic, and Claude are trademarks of their respective owners and are referenced here only for descriptive and comparative purposes (nominative fair use). We interoperate with OpenAI and Anthropic APIs as a customer like anyone else; you supply your own API keys.
