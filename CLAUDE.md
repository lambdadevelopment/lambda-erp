# CLAUDE.md

## Before touching accounting, stock, or document lifecycle

Read `docs/agents/invariants.md` and `docs/agents/gotchas.md` first.
They're short and capture rules the code doesn't enforce plus landmines
that have bitten us. Deeper references:

- `docs/agents/accounting.md` — sign conventions, returns, cancel guards
- `docs/agents/stock.md` — the three contra accounts, shared helpers, order of operations
- `docs/agents/testing.md` — what "done" means
- `docs/agents/decisions.md` — why the code looks the way it does

## Testing

When making changes to core business logic (`lambda_erp/`), always run the validation test suite before considering the work done:

```bash
python -m tests.test_erp_validation
```

This exercises the full ERP cycle (quotation through payment, purchasing, stock, journal entries) and includes regression tests for:
- Repeated invoice create/cancel preserving billed_qty
- Failed submit rolling back docstatus (transaction atomicity)

The test uses an in-memory SQLite database and is self-contained — no setup needed.

## Project Structure

- `lambda_erp/` — Pure Python business logic (no dependencies). Document lifecycle, GL posting, stock ledger, tax calculation.
- `api/` — FastAPI REST API + WebSocket chat endpoint wrapping the business logic.
- `frontend/` — React + Vite + TypeScript + Tailwind CSS app.
- `tests/` — Validation test suite.

## Running

```bash
# Backend
source .venv/bin/activate
uvicorn api.main:app --reload --port 8000

# Frontend
cd frontend && npm run dev

# Tests
python -m tests.test_erp_validation
```
