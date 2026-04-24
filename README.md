# Lambda ERP

**An AI-native ERP - because consulting shouldn't cost more than the software.**

Lambda ERP is a working reference implementation of what we think a ERP should look like in 2026: chat as the primary interface, LLMs as the configuration layer, and consultant-weeks replaced by token-minutes.

https://github.com/user-attachments/assets/1b2749ef-10e7-42f5-9cce-df5628292667

<p>
  <a href="https://lambda.dev/erp"><img alt="Live demo" src="https://img.shields.io/badge/demo-lambda--erp-4fc3f7?style=flat"></a>
  <a href="https://discord.gg/ZwFh9hZJTb"><img alt="Discord" src="https://img.shields.io/discord/1496911123029557359?color=7289DA&label=Discord&logo=discord&logoColor=white"></a>
</p>

This release is not yet production-ready. It's a complete prototype but not vetted enough to run your payroll on it yet.

---

## Why another ERP?

Today, the most expensive part of an ERP rollout isn't paying for the software - it's paying the consultancies that configure it. A company can buy SAP Business One for 5 employees for roughly **$2,500 a year**, but getting anything actually working costs **$10-20k up front** for a quick-start package and **up to $50k** for a real implementation - roughly **20x the annual license spend on day one alone**. And the meter keeps running: every custom report, country-specific tax rule, or workflow change is another partner ticket at $150-220/hour. Systems like Oracle NetSuite and Microsoft Dynamics follow the same shape, and at the enterprise end (SAP S/4HANA and friends) implementations routinely run into the millions.

That entire industry exists because ERPs are generic platforms that have to be bent to each company's shape by humans writing configuration, workflows, and custom reports. It's expensive because it's manual, and it's manual because software couldn't do it - until recently.

We think that's about to flip. Most of what an ERP consultant actually bills for is text-transformation and configuration work that LLMs are now genuinely good at:

- **Reading documents.** A supplier PDF becomes a Purchase Invoice. A bank statement becomes reconciled journal entries. An onboarding form becomes a Customer record.
- **Chart of Accounts design and mapping.** Taking a client's legacy accounts, translating to local GAAP, producing the mapping table - pure language-plus-structure work.
- **Master data migration.** Cleaning, deduplicating, and loading customer/supplier/item masters and opening balances from whatever mess the legacy system coughs up.
- **Custom reports and print formats.** The bespoke chart, the specific invoice layout, the cash-flow view nobody else has - unbounded client taste, infinite long tail.
- **End-user training and ongoing questions.** "How do I issue a credit note?" "Why is this balance off?" "What was last quarter's margin by product line?" - natural-language lookups that used to need a help-desk hour.

A consultant bills $150-220 an hour for that work. The same task via a frontier LLM is cents and seconds. The money that used to go to consultants should go to compute. The software should tailor itself to you, not the other way around. And because Lambda ERP is open source and self-hosted, the configuration doesn't stop at go-live - the system can keep evolving. A consultant's role shrinks from writing each change to reviewing AI-drafted ones: a few dollars in tokens plus a sliver of review time, instead of days at $200/hour.


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
| Storage | SQLite in the prototype; Postgres planned for production |
| Frontend | React + Vite + TypeScript + Tailwind + Recharts |
| Chat transport | WebSocket |
| LLM orchestrator | OpenAI (configurable) |
| Code specialist | Anthropic (configurable) |
| Auth | JWT httponly cookie, three roles + demo |

---

## What works today

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
2. **Costs collapsed.** Generating a custom report via a code-specialist sub-agent costs cents. The same work as a consulting change-request is a 4-figure invoice. That's a three-to-four-order-of-magnitude gap.
3. **Structured output + function calling are first-class.** We can constrain the LLM's outputs to valid tool-call schemas, safe SQL parameters, and typed JSON - which is what makes an AI-native ERP even conceivable as a safe thing to run.
4. **Greenfield is finally cheaper than retrofit.** Twenty-year ERP codebases have hundreds of bespoke models and thousands of hand-written forms - teaching an LLM to drive that reliably means curating a custom tool layer over every quirk. Starting from scratch around one Document lifecycle and a metadata-driven UI is now cheaper than retrofitting an existing platform.

---

## Why open source

Every company configuring an ERP runs into the same problems: local tax rules, common workflow patterns, industry-specific accounting quirks. Most of that knowledge isn't a competitive advantage - it's just work that 10,000 consultants have done 10,000 times in slightly different ways.

We want Lambda ERP to be where that knowledge lives in public. The base system is MIT-licensed, and we're organizing the repo so that community contributions - a German VAT pack, a U.S. sales-tax-by-state module, an industry template for professional services - can slot in under `docs/` and be picked up by the LLM as reference material. The goal is a true community where running an ERP gets cheaper, faster, and more flexible for everyone, not just the implementer.

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

## Status

Version 0. Working prototype that implements the vision. Fine for demos, internal tools, and hacking. Not yet ready to handle your company's actual books - run it alongside your real ERP if you want to kick the tires.

If you try it, we'd love to know what broke.

---

## Trademarks and affiliations

Lambda ERP and [lambda.dev](https://lambda.dev/) are product and trade names of **TORUS INVESTMENTS AG**. It is not affiliated with, endorsed by, or sponsored by OpenAI, Anthropic, SAP, Oracle, Microsoft, or any other company named in this repository. SAP, Business One, S/4HANA, Oracle, NetSuite, Microsoft, Dynamics, OpenAI, GPT, Anthropic, and Claude are trademarks of their respective owners and are referenced here only for descriptive and comparative purposes (nominative fair use). We interoperate with OpenAI and Anthropic APIs as a customer like anyone else; you supply your own API keys.
