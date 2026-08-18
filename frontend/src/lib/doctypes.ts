/**
 * Doctype metadata registry.
 *
 * This drives the generic list and form pages. Instead of coding 8 separate
 * pages, a single registry defines the fields, columns, and actions for each
 * document type.
 */

export interface FieldDef {
  name: string;
  label: string;
  type: "text" | "number" | "currency" | "date" | "datetime" | "link" | "select" | "textarea";
  required?: boolean;
  readOnly?: boolean;
  linkDoctype?: string; // for type=link, which master to search
  linkDoctypeField?: string; // for type=link, read the linkDoctype from this sibling field's value (lowercase)
  // For type=select: either plain strings (shown as-is) or {value, label}
  // pairs when the stored value is machine-friendly (e.g. "1"/"0") but the
  // user-visible label should read differently ("Yes"/"No").
  options?: Array<string | { value: string; label: string }>;
  // For type=select with a runtime-populated option list. "currency" fills the
  // dropdown from the available-currencies endpoint instead of static options.
  optionsSource?: "currency";
  default?: any;
  hint?: string; // tooltip text shown via a help icon next to the label
}

export interface ChildTableDef {
  label: string;
  key: string; // field name on the parent doc (e.g. "items", "taxes", "accounts")
  fields: FieldDef[];
}

export interface ConversionDef {
  label: string;
  targetDoctype: string;
}

export interface DoctypeConfig {
  slug: string;
  label: string;
  dateField: string;
  partyField?: string;
  partyLabel?: string;
  amountField?: string;
  fields: FieldDef[];
  childTables: ChildTableDef[];
  listColumns: string[];
  // Field names to expose as dropdown filters above the list. Each must name a
  // `type: "select"` field in `fields`; its options drive the dropdown and the
  // selection is sent as a plain column filter (status=Contacted, fit=A). When
  // set, these replace the built-in Draft/Submitted/Cancelled status dropdown —
  // the right call for non-submittable doctypes whose `status` is a business
  // field, not a docstatus. Omit to keep the default status filter.
  listFilters?: string[];
  // Column names the list's free-text search box matches against (case-
  // insensitive substring), e.g. ["company_name", "uid", "town"]. When set, the
  // list shows a debounced search box; the backend can also match via a
  // registered related-table expansion (register_search_expansion). Omit for no
  // search box.
  searchFields?: string[];
  canSubmit: boolean;
  canCancel: boolean;
  conversions: ConversionDef[];
  // A non-document "soft cancel": flip a field to a terminal value (e.g. a
  // Reservation's status -> "Cancelled", which frees the calendar) via a plain
  // update — the ERP has no hard delete for documents. Renders a "Cancel
  // booking" button. Omit for doctypes that don't have one.
  softCancel?: { field: string; value: string };
}

// --- Shared child table definitions ---

const ITEM_FIELDS: FieldDef[] = [
  { name: "item_code", label: "Item", type: "link", linkDoctype: "item", required: true },
  { name: "item_name", label: "Item Name", type: "text", readOnly: true },
  // Per-line description/blurb. Defaults from the Item master on save (an empty
  // value falls back to the master, so leaving it blank never wipes anything);
  // type here to override it for this document only — like a Proposal position.
  { name: "description", label: "Description", type: "textarea" },
  { name: "qty", label: "Qty", type: "number", required: true },
  { name: "rate", label: "Rate", type: "currency", required: true },
  { name: "amount", label: "Amount", type: "currency", readOnly: true },
];

// Quotation lines additionally carry a billing frequency: one-time or a
// recurring cadence. Recurring lines are totalled separately on the offer
// (their own per-period subtotal), so they don't inflate the one-time total.
// Values match the Subscription billing intervals exactly (kept in English;
// the branded PDF localizes them for display).
const QUOTATION_ITEM_FIELDS: FieldDef[] = [
  ...ITEM_FIELDS,
  {
    name: "frequency",
    label: "Frequency",
    type: "select",
    options: ["One-time", "Monthly", "Quarterly", "Half-Yearly", "Yearly"],
    default: "One-time",
  },
];

// Transaction currency picker, shared by all sales/purchase documents. The
// dropdown is populated at runtime (base + convertible currencies) and the
// form pre-selects the party's (or company's) default currency on new docs.
const CURRENCY_FIELD: FieldDef = {
  name: "currency", label: "Currency", type: "select", optionsSource: "currency",
  hint: "The currency this document is transacted in. Defaults from the party's (or the company's) default currency. The ledger still posts in the company base currency, translated at this document's exchange rate.",
};

const TAX_FIELDS: FieldDef[] = [
  {
    name: "charge_type", label: "Type", type: "select",
    options: ["On Net Total", "On Previous Row Amount", "On Previous Row Total", "Actual", "On Item Quantity"],
    default: "On Net Total",
  },
  { name: "account_head", label: "Account", type: "link", linkDoctype: "account" },
  { name: "description", label: "Description", type: "text" },
  { name: "rate", label: "Rate (%)", type: "number" },
  { name: "tax_amount", label: "Amount", type: "currency", readOnly: true },
];

// --- Doctype configs ---

const CONFIGS: Record<string, DoctypeConfig> = {
  quotation: {
    slug: "quotation",
    label: "Quotation",
    dateField: "transaction_date",
    partyField: "customer",
    partyLabel: "Customer",
    amountField: "grand_total",
    fields: [
      { name: "customer", label: "Customer", type: "link", linkDoctype: "customer", required: true },
      { name: "transaction_date", label: "Date", type: "date", required: true },
      { name: "valid_till", label: "Valid Till", type: "date" },
      { name: "company", label: "Company", type: "link", linkDoctype: "company", required: true },
      CURRENCY_FIELD,
      { name: "net_total", label: "Net Total", type: "currency", readOnly: true },
      { name: "total_taxes_and_charges", label: "Tax", type: "currency", readOnly: true },
      { name: "grand_total", label: "Grand Total", type: "currency", readOnly: true },
      { name: "remarks", label: "Notes / Terms", type: "textarea" },
    ],
    childTables: [
      { key: "items", label: "Items", fields: QUOTATION_ITEM_FIELDS },
      { key: "taxes", label: "Taxes", fields: TAX_FIELDS },
    ],
    listColumns: ["name", "customer", "transaction_date", "grand_total", "status"],
    canSubmit: true,
    canCancel: true,
    conversions: [
      { label: "Create Sales Order", targetDoctype: "Sales Order" },
      { label: "Create Sales Invoice", targetDoctype: "Sales Invoice" },
      { label: "Create Delivery Note", targetDoctype: "Delivery Note" },
    ],
  },

  // Proposal (Sammelofferte) — the form is a custom builder (see
  // pages/proposals/proposal-form.tsx, routed ahead of the generic form). This
  // config only drives the generic LIST page: columns + label + title.
  proposal: {
    slug: "proposal",
    label: "Proposal",
    dateField: "proposal_date",
    partyField: "customer",
    partyLabel: "Customer",
    fields: [],
    childTables: [],
    listColumns: ["name", "title", "customer", "modified"],
    canSubmit: false,
    canCancel: false,
    conversions: [],
  },

  "sales-order": {
    slug: "sales-order",
    label: "Sales Order",
    dateField: "transaction_date",
    partyField: "customer",
    partyLabel: "Customer",
    amountField: "grand_total",
    fields: [
      { name: "customer", label: "Customer", type: "link", linkDoctype: "customer", required: true },
      { name: "transaction_date", label: "Date", type: "date", required: true },
      { name: "delivery_date", label: "Delivery Date", type: "date" },
      { name: "company", label: "Company", type: "link", linkDoctype: "company", required: true },
      CURRENCY_FIELD,
      { name: "per_delivered", label: "% Delivered", type: "number", readOnly: true },
      { name: "per_billed", label: "% Billed", type: "number", readOnly: true },
      { name: "net_total", label: "Net Total", type: "currency", readOnly: true },
      { name: "grand_total", label: "Grand Total", type: "currency", readOnly: true },
      { name: "remarks", label: "Notes / Terms", type: "textarea" },
    ],
    childTables: [
      { key: "items", label: "Items", fields: ITEM_FIELDS },
      { key: "taxes", label: "Taxes", fields: TAX_FIELDS },
    ],
    listColumns: ["name", "customer", "transaction_date", "grand_total", "status"],
    canSubmit: true,
    canCancel: true,
    conversions: [
      { label: "Create Sales Invoice", targetDoctype: "Sales Invoice" },
      { label: "Create Delivery Note", targetDoctype: "Delivery Note" },
    ],
  },

  "sales-invoice": {
    slug: "sales-invoice",
    label: "Sales Invoice",
    dateField: "posting_date",
    partyField: "customer",
    partyLabel: "Customer",
    amountField: "grand_total",
    fields: [
      { name: "customer", label: "Customer", type: "link", linkDoctype: "customer", required: true },
      { name: "posting_date", label: "Posting Date", type: "date", required: true },
      { name: "due_date", label: "Due Date", type: "date" },
      { name: "company", label: "Company", type: "link", linkDoctype: "company", required: true },
      CURRENCY_FIELD,
      { name: "net_total", label: "Net Total", type: "currency", readOnly: true },
      { name: "total_taxes_and_charges", label: "Tax", type: "currency", readOnly: true },
      { name: "grand_total", label: "Grand Total", type: "currency", readOnly: true },
      { name: "outstanding_amount", label: "Outstanding", type: "currency", readOnly: true },
      { name: "remarks", label: "Notes / Terms", type: "textarea" },
    ],
    childTables: [
      { key: "items", label: "Items", fields: [...ITEM_FIELDS, { name: "income_account", label: "Income Account", type: "link", linkDoctype: "account" }] },
      { key: "taxes", label: "Taxes", fields: TAX_FIELDS },
    ],
    listColumns: ["name", "customer", "posting_date", "grand_total", "outstanding_amount", "status"],
    canSubmit: true,
    canCancel: true,
    conversions: [
      { label: "Create Credit Note", targetDoctype: "Sales Invoice" },
    ],
  },

  "purchase-order": {
    slug: "purchase-order",
    label: "Purchase Order",
    dateField: "transaction_date",
    partyField: "supplier",
    partyLabel: "Supplier",
    amountField: "grand_total",
    fields: [
      { name: "supplier", label: "Supplier", type: "link", linkDoctype: "supplier", required: true },
      { name: "transaction_date", label: "Date", type: "date", required: true },
      { name: "company", label: "Company", type: "link", linkDoctype: "company", required: true },
      CURRENCY_FIELD,
      { name: "net_total", label: "Net Total", type: "currency", readOnly: true },
      { name: "grand_total", label: "Grand Total", type: "currency", readOnly: true },
      { name: "remarks", label: "Notes / Terms", type: "textarea" },
    ],
    childTables: [
      { key: "items", label: "Items", fields: [...ITEM_FIELDS, { name: "warehouse", label: "Warehouse", type: "link", linkDoctype: "warehouse" }] },
      { key: "taxes", label: "Taxes", fields: TAX_FIELDS },
    ],
    listColumns: ["name", "supplier", "transaction_date", "grand_total", "status"],
    canSubmit: true,
    canCancel: true,
    conversions: [
      { label: "Create Purchase Invoice", targetDoctype: "Purchase Invoice" },
      { label: "Create Purchase Receipt", targetDoctype: "Purchase Receipt" },
    ],
  },

  "purchase-invoice": {
    slug: "purchase-invoice",
    label: "Purchase Invoice",
    dateField: "posting_date",
    partyField: "supplier",
    partyLabel: "Supplier",
    amountField: "grand_total",
    fields: [
      { name: "supplier", label: "Supplier", type: "link", linkDoctype: "supplier", required: true },
      { name: "posting_date", label: "Posting Date", type: "date", required: true },
      { name: "due_date", label: "Due Date", type: "date" },
      { name: "company", label: "Company", type: "link", linkDoctype: "company", required: true },
      CURRENCY_FIELD,
      { name: "update_stock", label: "Update Stock", type: "select",
        options: [{ value: "0", label: "No" }, { value: "1", label: "Yes" }], default: "0",
        hint: "If Yes, submitting this invoice also receives stock into the item warehouses.",
      },
      { name: "net_total", label: "Net Total", type: "currency", readOnly: true },
      { name: "grand_total", label: "Grand Total", type: "currency", readOnly: true },
      { name: "outstanding_amount", label: "Outstanding", type: "currency", readOnly: true },
      { name: "remarks", label: "Notes / Terms", type: "textarea" },
    ],
    childTables: [
      {
        key: "items",
        label: "Items",
        fields: [
          ...ITEM_FIELDS,
          { name: "warehouse", label: "Warehouse", type: "link", linkDoctype: "warehouse",
            hint: "Required when Update Stock is Yes — the received goods land in this warehouse.",
          },
          { name: "expense_account", label: "Expense Account", type: "link", linkDoctype: "account" },
        ],
      },
      { key: "taxes", label: "Taxes", fields: TAX_FIELDS },
    ],
    listColumns: ["name", "supplier", "posting_date", "grand_total", "outstanding_amount", "status"],
    canSubmit: true,
    canCancel: true,
    conversions: [
      { label: "Create Debit Note", targetDoctype: "Purchase Invoice" },
    ],
  },

  "payment-entry": {
    slug: "payment-entry",
    label: "Payment Entry",
    dateField: "posting_date",
    partyField: "party",
    partyLabel: "Party",
    amountField: "paid_amount",
    fields: [
      {
        name: "payment_type", label: "Payment Type", type: "select",
        options: ["Receive", "Pay", "Internal Transfer"], required: true,
        hint: "Receive: customer pays you. Pay: you pay a supplier. Internal Transfer: move money between your own accounts.",
      },
      { name: "posting_date", label: "Posting Date", type: "date", required: true },
      { name: "company", label: "Company", type: "link", linkDoctype: "company", required: true },
      { name: "party_type", label: "Party Type", type: "select", options: ["Customer", "Supplier"],
        hint: "Select Customer when receiving money, Supplier when paying.",
      },
      { name: "party", label: "Party", type: "link", linkDoctypeField: "party_type" },
      { name: "paid_from", label: "Paid From", type: "link", linkDoctype: "account",
        hint: "The account money is coming from (e.g. Accounts Receivable when receiving, Bank when paying).",
      },
      { name: "paid_to", label: "Paid To", type: "link", linkDoctype: "account",
        hint: "The account money is going to (e.g. Bank when receiving, Accounts Payable when paying).",
      },
      { name: "paid_amount", label: "Paid Amount", type: "currency", required: true,
        hint: "The total amount of money changing hands. This is the amount that hits the bank account.",
      },
    ],
    childTables: [
      {
        key: "references", label: "Payment References", fields: [
          { name: "reference_doctype", label: "Type", type: "select", options: ["Sales Invoice", "Purchase Invoice"], default: "Sales Invoice" },
          { name: "reference_name", label: "Invoice", type: "link", linkDoctypeField: "reference_doctype",
            hint: "The invoice this payment is being applied against.",
          },
          { name: "total_amount", label: "Total", type: "currency", readOnly: true,
            hint: "The full amount of the referenced invoice.",
          },
          { name: "outstanding_amount", label: "Outstanding", type: "currency", readOnly: true,
            hint: "How much is still unpaid on this invoice.",
          },
          { name: "allocated_amount", label: "Allocated", type: "currency",
            hint: "How much of this payment to apply against this invoice. Can be less than Paid Amount for partial payments, or split across multiple invoices.",
          },
        ],
      },
    ],
    listColumns: ["name", "payment_type", "party", "posting_date", "paid_amount", "status"],
    canSubmit: true,
    canCancel: true,
    conversions: [],
  },

  "journal-entry": {
    slug: "journal-entry",
    label: "Journal Entry",
    dateField: "posting_date",
    amountField: "total_debit",
    fields: [
      { name: "posting_date", label: "Posting Date", type: "date", required: true },
      { name: "company", label: "Company", type: "link", linkDoctype: "company", required: true },
      { name: "remark", label: "Remark", type: "textarea" },
      { name: "total_debit", label: "Total Debit", type: "currency", readOnly: true },
      { name: "total_credit", label: "Total Credit", type: "currency", readOnly: true },
    ],
    childTables: [
      {
        key: "accounts", label: "Account Entries", fields: [
          { name: "account", label: "Account", type: "link", linkDoctype: "account", required: true },
          { name: "party_type", label: "Party Type", type: "select", options: ["", "Customer", "Supplier"] },
          { name: "party", label: "Party", type: "link", linkDoctypeField: "party_type" },
          { name: "debit", label: "Debit", type: "currency" },
          { name: "credit", label: "Credit", type: "currency" },
          { name: "cost_center", label: "Cost Center", type: "link", linkDoctype: "cost-center" },
        ],
      },
    ],
    listColumns: ["name", "posting_date", "remark", "total_debit", "status"],
    canSubmit: true,
    canCancel: true,
    conversions: [],
  },

  "stock-entry": {
    slug: "stock-entry",
    label: "Stock Entry",
    dateField: "posting_date",
    amountField: "total_amount",
    fields: [
      {
        name: "stock_entry_type", label: "Type", type: "select",
        options: ["Material Receipt", "Material Issue", "Material Transfer", "Opening Stock"], required: true,
        hint: "Opening Stock is for one-time initial inventory (contra: Opening Balance Equity). Material Receipt/Issue are for adjustments and write-offs (contra: Stock Adjustment).",
      },
      { name: "posting_date", label: "Posting Date", type: "date", required: true },
      { name: "company", label: "Company", type: "link", linkDoctype: "company", required: true },
      { name: "from_warehouse", label: "Source Warehouse", type: "link", linkDoctype: "warehouse" },
      { name: "to_warehouse", label: "Target Warehouse", type: "link", linkDoctype: "warehouse" },
      { name: "total_incoming_value", label: "Incoming Value", type: "currency", readOnly: true },
      { name: "total_outgoing_value", label: "Outgoing Value", type: "currency", readOnly: true },
    ],
    childTables: [
      {
        key: "items", label: "Items", fields: [
          { name: "item_code", label: "Item", type: "link", linkDoctype: "item", required: true },
          { name: "item_name", label: "Item Name", type: "text", readOnly: true },
          { name: "qty", label: "Qty", type: "number", required: true },
          { name: "s_warehouse", label: "Source", type: "link", linkDoctype: "warehouse" },
          { name: "t_warehouse", label: "Target", type: "link", linkDoctype: "warehouse" },
          { name: "basic_rate", label: "Rate", type: "currency" },
          { name: "basic_amount", label: "Amount", type: "currency", readOnly: true },
        ],
      },
    ],
    listColumns: ["name", "stock_entry_type", "posting_date", "total_amount", "status"],
    canSubmit: true,
    canCancel: true,
    conversions: [],
  },

  "delivery-note": {
    slug: "delivery-note",
    label: "Delivery Note",
    dateField: "posting_date",
    partyField: "customer",
    partyLabel: "Customer",
    amountField: "grand_total",
    fields: [
      { name: "customer", label: "Customer", type: "link", linkDoctype: "customer", required: true },
      { name: "posting_date", label: "Posting Date", type: "date", required: true },
      { name: "company", label: "Company", type: "link", linkDoctype: "company", required: true },
      CURRENCY_FIELD,
      { name: "net_total", label: "Net Total", type: "currency", readOnly: true },
      { name: "total_taxes_and_charges", label: "Tax", type: "currency", readOnly: true },
      { name: "grand_total", label: "Grand Total", type: "currency", readOnly: true },
      { name: "remarks", label: "Notes / Terms", type: "textarea" },
    ],
    childTables: [
      {
        key: "items", label: "Items", fields: [
          ...ITEM_FIELDS,
          { name: "warehouse", label: "Warehouse", type: "link", linkDoctype: "warehouse", required: true },
        ],
      },
      { key: "taxes", label: "Taxes", fields: TAX_FIELDS },
    ],
    listColumns: ["name", "customer", "posting_date", "grand_total", "status"],
    canSubmit: true,
    canCancel: true,
    conversions: [
      { label: "Create Return", targetDoctype: "Delivery Note" },
    ],
  },

  "purchase-receipt": {
    slug: "purchase-receipt",
    label: "Purchase Receipt",
    dateField: "posting_date",
    partyField: "supplier",
    partyLabel: "Supplier",
    amountField: "grand_total",
    fields: [
      { name: "supplier", label: "Supplier", type: "link", linkDoctype: "supplier", required: true },
      { name: "posting_date", label: "Posting Date", type: "date", required: true },
      { name: "company", label: "Company", type: "link", linkDoctype: "company", required: true },
      CURRENCY_FIELD,
      { name: "net_total", label: "Net Total", type: "currency", readOnly: true },
      { name: "total_taxes_and_charges", label: "Tax", type: "currency", readOnly: true },
      { name: "grand_total", label: "Grand Total", type: "currency", readOnly: true },
      { name: "remarks", label: "Notes / Terms", type: "textarea" },
    ],
    childTables: [
      {
        key: "items", label: "Items", fields: [
          ...ITEM_FIELDS,
          { name: "warehouse", label: "Warehouse", type: "link", linkDoctype: "warehouse", required: true },
        ],
      },
      { key: "taxes", label: "Taxes", fields: TAX_FIELDS },
    ],
    listColumns: ["name", "supplier", "posting_date", "grand_total", "status"],
    canSubmit: true,
    canCancel: true,
    conversions: [
      { label: "Create Return", targetDoctype: "Purchase Receipt" },
    ],
  },

  "pos-invoice": {
    slug: "pos-invoice",
    label: "POS Invoice",
    dateField: "posting_date",
    partyField: "customer",
    partyLabel: "Customer",
    amountField: "grand_total",
    fields: [
      { name: "customer", label: "Customer", type: "link", linkDoctype: "customer", required: true },
      { name: "posting_date", label: "Posting Date", type: "date", required: true },
      { name: "company", label: "Company", type: "link", linkDoctype: "company", required: true },
      CURRENCY_FIELD,
      { name: "update_stock", label: "Update Stock", type: "select",
        options: [{ value: "1", label: "Yes" }, { value: "0", label: "No" }], default: "1",
        hint: "If Yes, submitting this invoice also reduces stock from the item warehouses.",
      },
      { name: "net_total", label: "Net Total", type: "currency", readOnly: true },
      { name: "total_taxes_and_charges", label: "Tax", type: "currency", readOnly: true },
      { name: "grand_total", label: "Grand Total", type: "currency", readOnly: true },
      { name: "paid_amount", label: "Paid Amount", type: "currency", readOnly: true },
      { name: "change_amount", label: "Change", type: "currency", readOnly: true },
    ],
    childTables: [
      {
        key: "items", label: "Items", fields: [
          ...ITEM_FIELDS,
          { name: "warehouse", label: "Warehouse", type: "link", linkDoctype: "warehouse",
            hint: "Required when Update Stock is Yes — this is where stock is drawn from.",
          },
        ],
      },
      { key: "taxes", label: "Taxes", fields: TAX_FIELDS },
      {
        key: "payments", label: "Payments", fields: [
          { name: "mode_of_payment", label: "Mode", type: "select", options: ["Cash", "Card", "Bank Transfer"], default: "Cash" },
          { name: "account", label: "Account", type: "link", linkDoctype: "account" },
          { name: "amount", label: "Amount", type: "currency", required: true },
        ],
      },
    ],
    listColumns: ["name", "customer", "posting_date", "grand_total", "status"],
    canSubmit: true,
    canCancel: true,
    conversions: [],
  },

  "pricing-rule": {
    slug: "pricing-rule",
    label: "Pricing Rule",
    dateField: "valid_from",
    amountField: "discount_percentage",
    fields: [
      { name: "title", label: "Title", type: "text", required: true },
      { name: "item_code", label: "Item", type: "link", linkDoctype: "item", required: true },
      { name: "selling", label: "Selling", type: "select", options: ["1", "0"], default: "1" },
      { name: "buying", label: "Buying", type: "select", options: ["1", "0"], default: "0" },
      { name: "rate_or_discount", label: "Type", type: "select", options: ["Discount Percentage", "Discount Amount", "Rate"], default: "Discount Percentage" },
      { name: "discount_percentage", label: "Discount %", type: "number" },
      { name: "discount_amount", label: "Discount Amt", type: "currency" },
      { name: "rate", label: "Rate", type: "currency" },
      { name: "min_qty", label: "Min Qty", type: "number" },
      { name: "valid_from", label: "Valid From", type: "date" },
      { name: "valid_upto", label: "Valid Upto", type: "date" },
      { name: "priority", label: "Priority", type: "number" },
      { name: "company", label: "Company", type: "link", linkDoctype: "company" },
      { name: "enabled", label: "Enabled", type: "select", options: ["1", "0"], default: "1" },
    ],
    childTables: [],
    listColumns: ["name", "title", "item_code", "rate_or_discount", "discount_percentage", "status"],
    canSubmit: false,
    canCancel: false,
    conversions: [],
  },

  "budget": {
    slug: "budget",
    label: "Budget",
    dateField: "fiscal_year",
    amountField: "budget_amount",
    fields: [
      { name: "account", label: "Account", type: "link", linkDoctype: "account", required: true },
      { name: "cost_center", label: "Cost Center", type: "link", linkDoctype: "cost-center" },
      { name: "fiscal_year", label: "Fiscal Year", type: "text", required: true },
      { name: "company", label: "Company", type: "link", linkDoctype: "company", required: true },
      { name: "budget_amount", label: "Budget Amount", type: "currency", required: true },
      { name: "action_if_exceeded", label: "If Exceeded", type: "select", options: ["Stop", "Warn", "Ignore"], default: "Warn",
        hint: "Stop: prevents the transaction. Warn: allows it but logs a warning. Ignore: no check.",
      },
    ],
    childTables: [
      {
        key: "monthly_distribution", label: "Monthly Distribution", fields: [
          { name: "month", label: "Month", type: "select", options: [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
          ]},
          { name: "percentage", label: "%", type: "number" },
        ],
      },
    ],
    listColumns: ["name", "account", "cost_center", "fiscal_year", "budget_amount"],
    canSubmit: false,
    canCancel: false,
    conversions: [],
  },

  "subscription": {
    slug: "subscription",
    label: "Subscription",
    dateField: "start_date",
    fields: [
      { name: "party_type", label: "Party Type", type: "select", options: ["Customer", "Supplier"], required: true },
      { name: "party", label: "Party", type: "link", linkDoctypeField: "party_type", required: true },
      { name: "company", label: "Company", type: "link", linkDoctype: "company" },
      { name: "start_date", label: "Start Date", type: "date", required: true },
      { name: "end_date", label: "End Date", type: "date" },
      { name: "billing_interval", label: "Billing Interval", type: "select", options: ["Monthly", "Quarterly", "Half-Yearly", "Yearly"], default: "Monthly" },
      { name: "current_invoice_start", label: "Current Period Start", type: "date", readOnly: true },
      { name: "current_invoice_end", label: "Current Period End", type: "date", readOnly: true },
      { name: "status", label: "Status", type: "text", readOnly: true },
    ],
    childTables: [
      {
        key: "plans", label: "Plan Items", fields: [
          { name: "item_code", label: "Item", type: "link", linkDoctype: "item", required: true },
          { name: "item_name", label: "Item Name", type: "text", readOnly: true },
          { name: "qty", label: "Qty", type: "number", default: 1 },
          { name: "rate", label: "Rate", type: "currency", required: true },
        ],
      },
    ],
    listColumns: ["name", "party", "billing_interval", "start_date", "status"],
    canSubmit: false,
    canCancel: false,
    conversions: [],
  },

  "bank-transaction": {
    slug: "bank-transaction",
    label: "Bank Transaction",
    dateField: "posting_date",
    fields: [
      { name: "bank_account", label: "Bank Account", type: "link", linkDoctype: "account", required: true },
      { name: "posting_date", label: "Date", type: "date", required: true },
      { name: "deposit", label: "Deposit", type: "currency" },
      { name: "withdrawal", label: "Withdrawal", type: "currency" },
      { name: "description", label: "Description", type: "text" },
      { name: "reference_number", label: "Reference No", type: "text" },
      { name: "reference_doctype", label: "Matched Type", type: "select", options: ["", "Payment Entry", "Sales Invoice", "Purchase Invoice", "Journal Entry"] },
      { name: "reference_name", label: "Matched Doc", type: "link", linkDoctypeField: "reference_doctype" },
      { name: "allocated_amount", label: "Allocated", type: "currency", readOnly: true },
      { name: "unallocated_amount", label: "Unallocated", type: "currency", readOnly: true },
      { name: "status", label: "Status", type: "text", readOnly: true },
    ],
    childTables: [],
    listColumns: ["name", "posting_date", "deposit", "withdrawal", "status"],
    canSubmit: false,
    canCancel: false,
    conversions: [],
  },

  // --- Rentals: unit-level assets + date-ranged reservations (ADR-0002).
  // These are core doctypes (lambda_erp/assets/); they post nothing to the GL
  // or stock ledger. See docs/RENTAL_UI_PLAN.md.
  "asset": {
    slug: "asset",
    label: "Asset",
    dateField: "purchase_date",
    fields: [
      { name: "item_code", label: "Item (type)", type: "link", linkDoctype: "item", required: true,
        hint: "The Item is the machine TYPE and carries the rate; an Asset is one physical unit of it. The Item must have is_asset_tracked = 1." },
      { name: "asset_tag", label: "Asset Tag / Plate", type: "text",
        hint: "Plate or serial — must be unique across assets." },
      { name: "asset_name", label: "Name", type: "text" },
      { name: "warehouse", label: "Home Yard", type: "link", linkDoctype: "warehouse" },
      { name: "company", label: "Company", type: "link", linkDoctype: "company" },
      { name: "status", label: "Status", type: "select",
        options: ["Available", "On Hire", "Maintenance", "Retired"], default: "Available",
        hint: "The unit's state today (not a date range). Retired units leave the pool." },
      { name: "purchase_date", label: "Purchase Date", type: "date" },
      { name: "purchase_value", label: "Purchase Value", type: "currency" },
      { name: "meter_reading", label: "Meter Reading", type: "number", hint: "Operating hours / km." },
      { name: "meter_uom", label: "Meter Unit", type: "text" },
      { name: "notes", label: "Notes", type: "textarea" },
    ],
    childTables: [],
    listColumns: ["name", "item_code", "asset_tag", "warehouse", "status", "meter_reading"],
    listFilters: ["status"],
    searchFields: ["asset_tag", "item_code", "asset_name"],
    canSubmit: false,
    canCancel: false,
    conversions: [],
  },

  "reservation": {
    slug: "reservation",
    label: "Reservation",
    dateField: "from_datetime",
    partyField: "party",
    partyLabel: "Customer",
    fields: [
      { name: "item_code", label: "Item (type)", type: "link", linkDoctype: "item",
        hint: "Pooled booking ('a 1.7t excavator'): set Item + Yard + Qty. For a specific machine, set Asset instead." },
      { name: "warehouse", label: "Yard", type: "link", linkDoctype: "warehouse" },
      { name: "asset", label: "Asset (specific unit)", type: "link", linkDoctype: "asset",
        hint: "Book one specific machine. A unit booking also consumes a slot of its Item+Yard pool." },
      { name: "qty", label: "Qty", type: "number", default: 1 },
      { name: "from_datetime", label: "From", type: "datetime", required: true,
        hint: "Exact start — date and time (e.g. 14:27). A bare date means 00:00." },
      { name: "to_datetime", label: "To", type: "datetime", required: true,
        hint: "Exact end. Windows are half-open [from, to): a hire ending 09:00 and the next starting 09:00 do NOT clash." },
      { name: "status", label: "Status", type: "select",
        options: ["Reserved", "Out", "Returned", "Cancelled"], default: "Reserved" },
      { name: "party_type", label: "Party Type", type: "select", options: ["Customer", "Supplier"], default: "Customer" },
      { name: "party", label: "Party", type: "link", linkDoctypeField: "party_type" },
      { name: "purpose", label: "Purpose", type: "text" },
      { name: "company", label: "Company", type: "link", linkDoctype: "company" },
      { name: "notes", label: "Notes", type: "textarea" },
    ],
    childTables: [],
    listColumns: ["name", "item_code", "asset", "party", "from_datetime", "to_datetime", "status"],
    listFilters: ["status"],
    canSubmit: false,
    canCancel: false,
    conversions: [],
    softCancel: { field: "status", value: "Cancelled" },
  },
};

export function getDoctypeConfig(slug: string): DoctypeConfig | undefined {
  return CONFIGS[slug];
}

/**
 * Register (or override) a doctype config. A customer deployment built on the
 * published library calls this at startup to add a new document type or swap
 * the form/list schema of a core one. The list/form pages read CONFIGS live
 * via getDoctypeConfig, so a registration takes effect immediately.
 */
export function registerDoctype(config: DoctypeConfig) {
  CONFIGS[config.slug] = config;
}

export function getAllDoctypeConfigs(): DoctypeConfig[] {
  return Object.values(CONFIGS);
}

export const DOCTYPE_SLUGS = Object.keys(CONFIGS);
