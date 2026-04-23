# GUI Architecture Plan

## Context

Lambda ERP has a working backend: 8 document types, double-entry accounting, stock ledger, tax calculation — all in pure Python with SQLite. This plan adds a web GUI using **FastAPI + React (Vite) + shadcn/ui**.

The backend is already structured around a Document base class with `save()`, `submit()`, `cancel()`, `load()`, `as_dict()`. The API layer is a thin wrapper that exposes these as REST endpoints. The frontend is a metadata-driven React app — one generic list component and one generic form component handle all 8 document types.

## Stack

| Layer | Technology | Why |
|---|---|---|
| API | FastAPI | Auto-generates OpenAPI docs, Pydantic validation, async-ready |
| Frontend | React + Vite + TypeScript | Fast dev server, hot reload, type safety |
| Components | shadcn/ui (Radix + Tailwind) | Copy-paste components you own, great form/table primitives |
| Data grids | TanStack Table | Sorting, filtering, pagination, inline editing |
| Server state | TanStack Query | Caching, refetching, loading/error states |
| Routing | React Router v6 | Standard, nested layouts |

## Project Structure

```
lambda-erp/
├── lambda_erp/              # existing business logic (unchanged)
├── api/                     # FastAPI backend
│   ├── main.py              # App entry, lifespan, CORS, static mount
│   ├── deps.py              # Dependency injection (get_db)
│   ├── errors.py            # Exception -> HTTP status mapping
│   ├── services.py          # Bridge: FastAPI <-> Document classes
│   └── routers/
│       ├── documents.py     # Generic CRUD for all 8 doctypes
│       ├── masters.py       # Customer, Supplier, Item, Warehouse, Account
│       ├── reports.py       # Trial Balance, GL, Stock Balance
│       └── setup.py         # Company creation, demo seed
├── frontend/                # React + Vite
│   ├── src/
│   │   ├── api/client.ts    # Typed fetch wrapper
│   │   ├── lib/
│   │   │   ├── utils.ts     # cn(), formatCurrency()
│   │   │   └── doctypes.ts  # Metadata registry (fields, columns, actions per doctype)
│   │   ├── hooks/           # useDocumentList, useDocument, useReport
│   │   ├── components/
│   │   │   ├── ui/          # shadcn/ui primitives (auto-generated)
│   │   │   ├── layout/      # app-shell, sidebar, breadcrumbs
│   │   │   ├── document/    # status-badge, document-actions, child-table-editor, link-field
│   │   │   └── reports/     # report-table, report-filters
│   │   ├── pages/
│   │   │   ├── dashboard.tsx
│   │   │   ├── documents/
│   │   │   │   ├── document-list.tsx   # Generic, driven by doctype registry
│   │   │   │   └── document-form.tsx   # Generic, driven by doctype registry
│   │   │   ├── masters/               # customer, supplier, item, warehouse, account-tree
│   │   │   └── reports/               # trial-balance, general-ledger, stock-balance
│   │   └── routes.tsx
│   ├── vite.config.ts       # Proxy /api -> localhost:8000
│   └── package.json
├── demo.py
└── pyproject.toml           # Add: fastapi, uvicorn, pydantic
```

## API Design

### Document endpoints (generic for all 8 types)

Doctype names become URL slugs: `"Sales Invoice"` -> `sales-invoice`.

```
GET    /api/documents/{doctype}                  List (filters: status, party, from_date, to_date, limit, offset)
GET    /api/documents/{doctype}/{name}            Get single document with child tables
POST   /api/documents/{doctype}                  Create as draft
PUT    /api/documents/{doctype}/{name}            Update draft
POST   /api/documents/{doctype}/{name}/submit     Submit (posts GL/SLE)
POST   /api/documents/{doctype}/{name}/cancel     Cancel (reverses GL/SLE)
POST   /api/documents/{doctype}/{name}/convert    Convert to next doc (e.g. Quotation -> Sales Order)
```

### Master data endpoints

```
GET    /api/masters/{type}                       List (type: customer, supplier, item, warehouse, account, company)
GET    /api/masters/{type}/{name}                 Get single
POST   /api/masters/{type}                       Create
PUT    /api/masters/{type}/{name}                 Update
DELETE /api/masters/{type}/{name}                 Delete
GET    /api/masters/{type}/search?q=...           Autocomplete for link fields (top 10)
GET    /api/masters/account/tree                  Chart of Accounts as nested tree
```

### Reports

```
GET    /api/reports/trial-balance?company=...&from_date=...&to_date=...
GET    /api/reports/general-ledger?account=...&from_date=...&to_date=...&party=...
GET    /api/reports/stock-balance?item_code=...&warehouse=...
GET    /api/reports/dashboard-summary?company=...
```

### Setup

```
POST   /api/setup/company                        Create company + chart of accounts + cost center
GET    /api/setup/status                          Check if any company exists (first-run detection)
POST   /api/setup/seed-demo                       Seed demo data
```

### Error mapping

| Lambda ERP Exception | HTTP Status |
|---|---|
| `ValidationError` (message ends with "not found") | 404 |
| `ValidationError` (other) | 422 |
| `DocumentStatusError` | 409 |
| `DebitCreditNotEqual` | 422 |
| `NegativeStockError` | 422 |
| Generic `Exception` | 500 |

### services.py — the bridge

Central registry mapping doctype slugs to classes and conversion functions:

```python
DOCUMENT_CLASSES = {
    "Quotation": Quotation,
    "Sales Order": SalesOrder,
    "Sales Invoice": SalesInvoice,
    "Purchase Order": PurchaseOrder,
    "Purchase Invoice": PurchaseInvoice,
    "Payment Entry": PaymentEntry,
    "Journal Entry": JournalEntry,
    "Stock Entry": StockEntry,
}

CONVERTERS = {
    ("Quotation", "Sales Order"): make_sales_order,
    ("Sales Order", "Sales Invoice"): make_sales_invoice,
    ("Purchase Order", "Purchase Invoice"): make_purchase_invoice,
}
```

### SQLite thread safety

FastAPI runs requests concurrently. Add a `threading.Lock` to the `Database` class, acquired on write operations (`insert`, `set_value`, `delete`, `commit`). Simpler than connection-per-request and compatible with the existing singleton design.

## Frontend Design

### Sidebar navigation

```
Home / Dashboard

Selling
  ├── Quotation
  ├── Sales Order
  └── Sales Invoice

Buying
  ├── Purchase Order
  └── Purchase Invoice

Accounting
  ├── Payment Entry
  ├── Journal Entry
  └── Chart of Accounts

Stock
  └── Stock Entry

Reports
  ├── Trial Balance
  ├── General Ledger
  └── Stock Balance

Masters
  ├── Customer
  ├── Supplier
  ├── Item
  └── Warehouse
```

### Doctype metadata registry (the key abstraction)

Instead of coding 8 separate list pages and 8 separate form pages, a single registry drives both the generic list and form components. Each entry defines:

- `slug`, `label` — URL and display name
- `fields` — parent-level fields with name, label, type (text/number/currency/date/link/select), required, readOnly
- `childTables` — child table definitions, each with their own field list
- `listColumns` — which columns to show in the list view
- `partyField` — "customer" or "supplier" (for filtering)
- `dateField` — "posting_date" or "transaction_date"
- `actions` — available transitions and conversions per doctype

### Document form layout

```
┌─────────────────────────────────────────────────────────┐
│  [Breadcrumb: Quotation > QTN-0001]                     │
│  [StatusBadge: Draft]          [Save] [Submit] [Cancel]  │
│                                                          │
│  ┌──────────────────────────────────────────────────┐    │
│  │  Customer: [Riverside Manufacturing ▾]             │    │
│  │  Date: [2026-04-13]     Valid Till: [2026-05-13] │    │
│  │  Company: [Lambda Corp ▾]                        │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  Items                                      [+ Add Row]  │
│  ┌──────────────────────────────────────────────────┐    │
│  │ #  Item        Qty    Rate       Amount      [x] │    │
│  │ 1  Bolt Pack M8  10    100.00     1,000.00    [x] │    │
│  │ 2  Gasket Set K2  5    250.00     1,250.00    [x] │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  Taxes                                      [+ Add Row]  │
│  ┌──────────────────────────────────────────────────┐    │
│  │ #  Type           Account      Rate   Amount     │    │
│  │ 1  On Net Total   Tax Payable  10%    225.00     │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│              Net Total:   2,250.00                        │
│              Tax:           225.00                        │
│              Grand Total: 2,475.00                        │
│                                                          │
│  [Connected: Quotation QTN-0001 -> Sales Order SO-0001]  │
└─────────────────────────────────────────────────────────┘
```

Key behaviors:
- **Drafts are editable, submitted/cancelled are read-only**
- **Client-side calculation**: qty * rate = amount updates live; net_total and tax recalculate instantly. Server recalculates authoritatively on save.
- **Link fields**: Combobox with search autocomplete (calls `/api/masters/{type}/search?q=...`)
- **Child table**: Inline-editable grid using TanStack Table, tab key moves between cells
- **Connected documents**: Clickable links to related documents in the chain

### Status badges

| Status | Color |
|---|---|
| Draft | Gray |
| Open / Submitted | Blue |
| To Deliver and Bill / To Bill / To Deliver | Orange |
| Completed / Ordered / Paid | Green |
| Cancelled / Overdue | Red |

### Dashboard

4 metric cards (Revenue, Outstanding Receivable, Outstanding Payable, Stock Value) + recent documents table + pending actions list.

## Implementation Phases

### Phase 1: FastAPI skeleton
Create `api/` with all routes. Verify with curl / Swagger UI: create company, create customer + items, create a quotation, submit it, fetch it back.

**Files:** `api/main.py`, `api/deps.py`, `api/errors.py`, `api/services.py`, `api/routers/documents.py`, `api/routers/masters.py`, `api/routers/setup.py`

### Phase 2: React scaffold with layout
Scaffold Vite project, install shadcn/ui, create app shell with sidebar navigation and placeholder pages. Configure Vite proxy to FastAPI.

**Files:** `frontend/` scaffold, `src/App.tsx`, `src/routes.tsx`, `src/components/layout/`

### Phase 3: Master data pages
List and form pages for Customer, Supplier, Item, Warehouse, Account tree. Uses TanStack Table + TanStack Query.

**Files:** `src/hooks/`, `src/pages/masters/`

### Phase 4: Generic document list
Metadata-driven list page working for all 8 doctypes. Status badges, date/party filtering.

**Files:** `src/lib/doctypes.ts`, `src/components/document/status-badge.tsx`, `src/pages/documents/document-list.tsx`

### Phase 5: Document form with child tables
The big one. Generic form with inline-editable child tables, link field autocomplete, live amount calculation, Save/Submit/Cancel actions.

**Files:** `src/components/document/child-table-editor.tsx`, `src/components/document/link-field.tsx`, `src/components/document/document-actions.tsx`, `src/pages/documents/document-form.tsx`

### Phase 6: Document flow and conversion
Convert buttons (Quotation -> Sales Order -> Invoice), connected documents panel. Test full sales and purchase cycles through the UI.

### Phase 7: Reports
Trial Balance, General Ledger, Stock Balance pages with filter bars and TanStack Table rendering.

**Files:** `api/routers/reports.py`, `src/pages/reports/`

### Phase 8: Dashboard and polish
Dashboard with metrics, toast notifications, loading skeletons, empty states, first-run setup page.

## Verification

After each phase, verify end-to-end:

1. **Phase 1**: `curl -X POST localhost:8000/api/setup/company -d '{"name":"Test","currency":"USD"}'` then create and submit a quotation via the API
2. **Phase 2**: Open `localhost:5173`, navigate the sidebar, confirm `/api/docs` loads through the proxy
3. **Phase 3**: Create a customer through the UI, see it in the list
4. **Phase 4**: Navigate to `/app/quotation`, see columns and empty state
5. **Phase 5**: Create a Quotation with items and taxes, see live totals, save and submit
6. **Phase 6**: Full cycle: Quotation -> Sales Order -> Sales Invoice -> Payment, check GL entries via Trial Balance
7. **Phase 7**: View Trial Balance after the cycle, confirm debits = credits
8. **Phase 8**: Fresh database -> setup page -> full cycle -> dashboard shows metrics

## What's NOT in scope

- Authentication / user management
- Multi-currency exchange rate handling
- Print / PDF generation
- Email notifications
- Mobile responsiveness (can be added later, shadcn/ui is responsive by default)
