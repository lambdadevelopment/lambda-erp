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
  optionsSource?: "currency" | "pdfTemplate";
  default?: any;
  hint?: string; // tooltip text shown via a help icon next to the label
  // For a line's batch or storage location: propose what exists for the
  // line's item in its warehouse (typing something else stays possible).
  suggest?: "batch" | "location";
  // Row fields that hold the warehouse, first non-empty wins (default
  // ["warehouse"]); falls back to the document's own `warehouse`.
  suggestWarehouse?: string[];
}

export interface ChildTableDef {
  label: string;
  key: string; // field name on the parent doc (e.g. "items", "taxes", "accounts")
  fields: FieldDef[];
  // Lines can carry a deduction with its own text (price_list_rate stays the
  // full price, discount_amount per unit comes off it, discount_label is
  // printed as a sub-line). Selling documents the deduction flows through.
  lineDeductions?: boolean;
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
  // Currency-only catalogues do not carry a transaction conversion_rate.
  usesExchangeRate?: boolean;
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
  // list shows a field-aware search box; the backend can also match via a
  // registered related-table expansion (register_search_expansion). Omit for no
  // search box.
  // Documents with lines (items, subscription plans) also match a line's item
  // code, name or description on the backend.
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
  { name: "rate", label: "Rate", type: "currency", default: null, hint: "Leave blank to resolve the customer or company price on save. Enter 0 for a free line." },
  { name: "amount", label: "Amount", type: "currency", readOnly: true },
  { name: "price_source", label: "Price Source", type: "text", readOnly: true,
    hint: "Where the price came from: Item Price, Standard Rate, Entered, Manual (changed by hand; no longer repriced) or None (no price found)." },
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
      { name: "billing_address", label: "Invoice Address", type: "link", linkDoctype: "address",
        hint: "One of the customer's addresses. Empty: their default invoice address, else the customer's main address." },
      { name: "billing_address_display", label: "Invoice Address (as printed)", type: "textarea",
        hint: "Filled from the address picked. Change it here for this document only; clear it to fill it again." },
      { name: "shipping_address", label: "Delivery Address", type: "link", linkDoctype: "address",
        hint: "Where the goods go. Empty: the customer's default delivery address." },
      { name: "shipping_address_display", label: "Delivery Address (as printed)", type: "textarea",
        hint: "Filled from the address picked. Type an address the order names here; it takes precedence." },
      { name: "transaction_date", label: "Date", type: "date", required: true },
      { name: "pricing_date", label: "Pricing Date", type: "date",
        hint: "Prices and pricing rules valid on this date apply. Empty: the document date." },
      { name: "valid_till", label: "Valid Till", type: "date" },
      { name: "pdf_template", label: "PDF Layout", type: "select", optionsSource: "pdfTemplate",
        hint: "How the offer's PDF looks. Fixed when the offer is submitted." },
      { name: "company", label: "Company", type: "link", linkDoctype: "company", required: true },
      CURRENCY_FIELD,
      { name: "net_total", label: "Net Total", type: "currency", readOnly: true },
      { name: "total_taxes_and_charges", label: "Tax", type: "currency", readOnly: true },
      { name: "grand_total", label: "Grand Total", type: "currency", readOnly: true },
      { name: "intro_text", label: "Intro text", type: "textarea",
        hint: "Printed above the line items (where exactly is up to the PDF layout). New offers start with the default from Settings → PDF Templates." },
      { name: "remarks", label: "Closing text / terms", type: "textarea",
        hint: "Printed after the totals (where exactly is up to the PDF layout). New offers start with the default from Settings → PDF Templates." },
    ],
    childTables: [
      { key: "items", label: "Items", fields: QUOTATION_ITEM_FIELDS, lineDeductions: true },
      { key: "taxes", label: "Taxes", fields: TAX_FIELDS },
    ],
    listColumns: ["name", "customer", "transaction_date", "grand_total", "status"],
    searchFields: ["name", "customer", "customer_name", "external_reference"],
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
    searchFields: ["name", "title", "customer", "customer_name"],
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
      { name: "billing_address", label: "Invoice Address", type: "link", linkDoctype: "address",
        hint: "One of the customer's addresses. Empty: their default invoice address, else the customer's main address." },
      { name: "billing_address_display", label: "Invoice Address (as printed)", type: "textarea",
        hint: "Filled from the address picked. Change it here for this document only; clear it to fill it again." },
      { name: "shipping_address", label: "Delivery Address", type: "link", linkDoctype: "address",
        hint: "Where the goods go. Empty: the customer's default delivery address." },
      { name: "shipping_address_display", label: "Delivery Address (as printed)", type: "textarea",
        hint: "Filled from the address picked. Type an address the order names here; it takes precedence." },
      { name: "transaction_date", label: "Date", type: "date", required: true },
      { name: "pricing_date", label: "Pricing Date", type: "date",
        hint: "Prices and pricing rules valid on this date apply. Empty: the document date." },
      { name: "delivery_date", label: "Delivery Date", type: "date" },
      { name: "pdf_template", label: "PDF Layout", type: "select", optionsSource: "pdfTemplate",
        hint: "How the PDF looks. Fixed when the document is submitted." },
      { name: "company", label: "Company", type: "link", linkDoctype: "company", required: true },
      CURRENCY_FIELD,
      { name: "reserve_stock", label: "Reserve Stock", type: "select",
        options: [{ value: "0", label: "No" }, { value: "1", label: "Yes" }], default: "0",
        hint: "If Yes, submitting the order reserves what is in stock for it; nobody else can deliver or move that stock. What is not in stock stays open.",
      },
      { name: "per_delivered", label: "% Delivered", type: "number", readOnly: true },
      { name: "per_billed", label: "% Billed", type: "number", readOnly: true },
      { name: "net_total", label: "Net Total", type: "currency", readOnly: true },
      { name: "grand_total", label: "Grand Total", type: "currency", readOnly: true },
      { name: "intro_text", label: "Intro text", type: "textarea",
        hint: "Printed above the line items. New documents start with the default from Settings → PDF Templates." },
      { name: "remarks", label: "Printed notes / terms", type: "textarea" },
    ],
    childTables: [
      { key: "items", label: "Items", fields: ITEM_FIELDS, lineDeductions: true },
      { key: "taxes", label: "Taxes", fields: TAX_FIELDS },
    ],
    listColumns: ["name", "customer", "transaction_date", "grand_total", "status"],
    searchFields: ["name", "customer", "customer_name", "external_reference"],
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
      { name: "billing_address", label: "Invoice Address", type: "link", linkDoctype: "address",
        hint: "One of the customer's addresses. Empty: their default invoice address, else the customer's main address." },
      { name: "billing_address_display", label: "Invoice Address (as printed)", type: "textarea",
        hint: "Filled from the address picked. Change it here for this document only; clear it to fill it again." },
      { name: "shipping_address", label: "Delivery Address", type: "link", linkDoctype: "address",
        hint: "Where the goods go. Empty: the customer's default delivery address." },
      { name: "shipping_address_display", label: "Delivery Address (as printed)", type: "textarea",
        hint: "Filled from the address picked. Type an address the order names here; it takes precedence." },
      { name: "posting_date", label: "Posting Date", type: "date", required: true },
      { name: "pricing_date", label: "Pricing Date", type: "date",
        hint: "Prices and pricing rules valid on this date apply. Empty: the document date." },
      { name: "due_date", label: "Due Date", type: "date" },
      { name: "pdf_template", label: "PDF Layout", type: "select", optionsSource: "pdfTemplate",
        hint: "How the PDF looks. Fixed when the document is submitted." },
      { name: "company", label: "Company", type: "link", linkDoctype: "company", required: true },
      CURRENCY_FIELD,
      { name: "amount_only", label: "Amount only", type: "select",
        options: [{ value: "0", label: "No" }, { value: "1", label: "Yes" }], default: "0",
        hint: "Only on a credit / debit note. If Yes, it corrects the price and nothing else: no goods come back, stock and billed quantities stay as they are.",
      },
      { name: "net_total", label: "Net Total", type: "currency", readOnly: true },
      { name: "total_taxes_and_charges", label: "Tax", type: "currency", readOnly: true },
      { name: "grand_total", label: "Grand Total", type: "currency", readOnly: true },
      { name: "outstanding_amount", label: "Outstanding", type: "currency", readOnly: true },
      { name: "intro_text", label: "Intro text", type: "textarea",
        hint: "Printed above the line items. New documents start with the default from Settings → PDF Templates." },
      { name: "remarks", label: "Printed notes / terms", type: "textarea" },
    ],
    childTables: [
      { key: "items", label: "Items", fields: [...ITEM_FIELDS, { name: "income_account", label: "Income Account", type: "link", linkDoctype: "account" },
        { name: "location", label: "Storage Location", type: "text", suggest: "location", hint: "Only when this invoice also moves stock (Update Stock) and the warehouse uses storage locations." },
        { name: "serial_no", label: "Serial Numbers", type: "text",
          hint: "For serial-tracked items: one number per unit, as many as the quantity, separated by commas." },
        { name: "batch_no", label: "Batch", type: "text", suggest: "batch", hint: "Only when this invoice also moves stock (Update Stock) and the item is batch-tracked." },
        { name: "shelf_life_exception", label: "Shelf Life Exception", type: "text", hint: "Only when the batch has less shelf life left than the item requires: the approved reason for shipping it anyway." }], lineDeductions: true },
      { key: "taxes", label: "Taxes", fields: TAX_FIELDS },
    ],
    listColumns: ["name", "customer", "posting_date", "grand_total", "outstanding_amount", "status"],
    searchFields: ["name", "customer", "customer_name", "sales_order", "return_against", "external_reference"],
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
      { name: "pricing_date", label: "Pricing Date", type: "date",
        hint: "Prices and pricing rules valid on this date apply. Empty: the document date." },
      { name: "pdf_template", label: "PDF Layout", type: "select", optionsSource: "pdfTemplate",
        hint: "How the PDF looks. Fixed when the document is submitted." },
      { name: "company", label: "Company", type: "link", linkDoctype: "company", required: true },
      CURRENCY_FIELD,
      { name: "net_total", label: "Net Total", type: "currency", readOnly: true },
      { name: "grand_total", label: "Grand Total", type: "currency", readOnly: true },
      { name: "intro_text", label: "Intro text", type: "textarea",
        hint: "Printed above the line items. New documents start with the default from Settings → PDF Templates." },
      { name: "remarks", label: "Printed notes / terms", type: "textarea" },
    ],
    childTables: [
      { key: "items", label: "Items", fields: [...ITEM_FIELDS, { name: "warehouse", label: "Warehouse", type: "link", linkDoctype: "warehouse" },
        { name: "confirmed_date", label: "Confirmed Date", type: "date",
          hint: "The delivery date the supplier confirmed for this line. After the order is submitted, record it with the confirm_purchase_order action (or ask the assistant)." },
        { name: "confirmation_reference", label: "Confirmation Ref.", type: "text", hint: "The supplier's order confirmation number." }] },
      { key: "taxes", label: "Taxes", fields: TAX_FIELDS },
    ],
    listColumns: ["name", "supplier", "transaction_date", "grand_total", "status"],
    searchFields: ["name", "supplier", "supplier_name", "external_reference"],
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
      { name: "pricing_date", label: "Pricing Date", type: "date",
        hint: "Prices and pricing rules valid on this date apply. Empty: the document date." },
      { name: "due_date", label: "Due Date", type: "date" },
      { name: "company", label: "Company", type: "link", linkDoctype: "company", required: true },
      CURRENCY_FIELD,
      { name: "update_stock", label: "Update Stock", type: "select",
        options: [{ value: "0", label: "No" }, { value: "1", label: "Yes" }], default: "0",
        hint: "If Yes, submitting this invoice also receives stock into the item warehouses.",
      },
      { name: "amount_only", label: "Amount only", type: "select",
        options: [{ value: "0", label: "No" }, { value: "1", label: "Yes" }], default: "0",
        hint: "Only on a credit / debit note. If Yes, it corrects the price and nothing else: no goods come back, stock and billed quantities stay as they are.",
      },
      { name: "net_total", label: "Net Total", type: "currency", readOnly: true },
      { name: "grand_total", label: "Grand Total", type: "currency", readOnly: true },
      { name: "outstanding_amount", label: "Outstanding", type: "currency", readOnly: true },
      { name: "remarks", label: "Printed notes / terms", type: "textarea" },
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
          { name: "location", label: "Storage Location", type: "text", suggest: "location",
            hint: "Required when the warehouse uses storage locations: the place the goods are taken from or put." },
          { name: "serial_no", label: "Serial Numbers", type: "text",
            hint: "For serial-tracked items: one number per unit, as many as the quantity, separated by commas." },
          { name: "batch_no", label: "Batch", type: "text", suggest: "batch",
            hint: "Required on batch-tracked items when stock moves. One batch per line: split a quantity from two batches into two lines." },
          { name: "expiry_date", label: "Expiry Date", type: "date",
            hint: "Only needed for a batch that is new: it is stored on the batch." },
          { name: "expense_account", label: "Expense Account", type: "link", linkDoctype: "account" },
        ],
      },
      { key: "taxes", label: "Taxes", fields: TAX_FIELDS },
    ],
    listColumns: ["name", "supplier", "posting_date", "grand_total", "outstanding_amount", "status"],
    searchFields: ["name", "supplier", "supplier_name", "purchase_order", "return_against", "external_reference"],
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
      { name: "bank_reconciliation", label: "Bank Reconciliation", type: "text", readOnly: true },
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
    searchFields: ["name", "party", "party_name", "reference_no"],
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
      { name: "bank_reconciliation", label: "Bank Reconciliation", type: "text", readOnly: true },
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
    searchFields: ["name", "remark"],
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
        options: ["Material Receipt", "Material Issue", "Material Transfer", "Opening Stock", "Batch Assignment", "Serial Assignment"], required: true,
        hint: "Opening Stock is for one-time initial inventory (contra: Opening Balance Equity). Material Receipt/Issue are for adjustments and write-offs (contra: Stock Adjustment). Batch Assignment gives stock without a batch its batch, in place. Serial Assignment gives units without a serial number theirs, in place. A Material Transfer with the same warehouse on both sides moves stock between storage locations.",
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
          { name: "s_location", label: "Source Location", type: "text", suggest: "location", suggestWarehouse: ["s_warehouse"],
            hint: "The storage location the goods are taken from (warehouses with storage locations). Leave empty in a move inside one warehouse to put away stock that has no location yet." },
          { name: "t_location", label: "Target Location", type: "text", suggest: "location", suggestWarehouse: ["t_warehouse"],
            hint: "The storage location the goods are put into (warehouses with storage locations)." },
          { name: "serial_no", label: "Serial Numbers", type: "text",
            hint: "For serial-tracked items: one number per unit, as many as the quantity, separated by commas." },
          { name: "batch_no", label: "Batch", type: "text", suggest: "batch", suggestWarehouse: ["s_warehouse", "t_warehouse"],
            hint: "Required on batch-tracked items when stock moves. One batch per line: split a quantity from two batches into two lines." },
          { name: "expiry_date", label: "Expiry Date", type: "date",
            hint: "Only needed for a batch that is new: it is stored on the batch." },
          { name: "basic_rate", label: "Rate", type: "currency" },
          { name: "basic_amount", label: "Amount", type: "currency", readOnly: true },
        ],
      },
    ],
    listColumns: ["name", "stock_entry_type", "posting_date", "total_amount", "status"],
    searchFields: ["name", "from_warehouse", "to_warehouse"],
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
      { name: "billing_address", label: "Invoice Address", type: "link", linkDoctype: "address",
        hint: "One of the customer's addresses. Empty: their default invoice address, else the customer's main address." },
      { name: "billing_address_display", label: "Invoice Address (as printed)", type: "textarea",
        hint: "Filled from the address picked. Change it here for this document only; clear it to fill it again." },
      { name: "shipping_address", label: "Delivery Address", type: "link", linkDoctype: "address",
        hint: "Where the goods go. Empty: the customer's default delivery address." },
      { name: "shipping_address_display", label: "Delivery Address (as printed)", type: "textarea",
        hint: "Filled from the address picked. Type an address the order names here; it takes precedence." },
      { name: "posting_date", label: "Posting Date", type: "date", required: true },
      { name: "pricing_date", label: "Pricing Date", type: "date",
        hint: "Prices and pricing rules valid on this date apply. Empty: the document date." },
      { name: "pdf_template", label: "PDF Layout", type: "select", optionsSource: "pdfTemplate",
        hint: "How the PDF looks. Fixed when the document is submitted." },
      { name: "company", label: "Company", type: "link", linkDoctype: "company", required: true },
      CURRENCY_FIELD,
      { name: "net_total", label: "Net Total", type: "currency", readOnly: true },
      { name: "total_taxes_and_charges", label: "Tax", type: "currency", readOnly: true },
      { name: "grand_total", label: "Grand Total", type: "currency", readOnly: true },
      { name: "intro_text", label: "Intro text", type: "textarea",
        hint: "Printed above the line items. New documents start with the default from Settings → PDF Templates." },
      { name: "remarks", label: "Printed notes / terms", type: "textarea" },
    ],
    childTables: [
      {
        key: "items", label: "Items", fields: [
          ...ITEM_FIELDS,
          { name: "warehouse", label: "Warehouse", type: "link", linkDoctype: "warehouse", required: true },
          { name: "location", label: "Storage Location", type: "text", suggest: "location",
            hint: "Required when the warehouse uses storage locations: the place the goods are taken from or put." },
          { name: "serial_no", label: "Serial Numbers", type: "text",
            hint: "For serial-tracked items: one number per unit, as many as the quantity, separated by commas." },
          { name: "batch_no", label: "Batch", type: "text", suggest: "batch",
            hint: "Required on batch-tracked items when stock moves. One batch per line: split a quantity from two batches into two lines." },
          { name: "shelf_life_exception", label: "Shelf Life Exception", type: "text",
            hint: "Only when the batch has less shelf life left than the item requires: the approved reason for shipping it anyway." },
        ],
      },
      { key: "taxes", label: "Taxes", fields: TAX_FIELDS },
    ],
    listColumns: ["name", "customer", "posting_date", "grand_total", "status"],
    searchFields: ["name", "customer", "customer_name", "return_against"],
    canSubmit: true,
    canCancel: true,
    conversions: [
      { label: "Create Invoice", targetDoctype: "Sales Invoice" },
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
      { name: "pricing_date", label: "Pricing Date", type: "date",
        hint: "Prices and pricing rules valid on this date apply. Empty: the document date." },
      { name: "company", label: "Company", type: "link", linkDoctype: "company", required: true },
      CURRENCY_FIELD,
      { name: "net_total", label: "Net Total", type: "currency", readOnly: true },
      { name: "total_taxes_and_charges", label: "Tax", type: "currency", readOnly: true },
      { name: "grand_total", label: "Grand Total", type: "currency", readOnly: true },
      { name: "remarks", label: "Printed notes / terms", type: "textarea" },
    ],
    childTables: [
      {
        key: "items", label: "Items", fields: [
          ...ITEM_FIELDS,
          { name: "warehouse", label: "Warehouse", type: "link", linkDoctype: "warehouse", required: true },
          { name: "location", label: "Storage Location", type: "text", suggest: "location",
            hint: "Required when the warehouse uses storage locations: the place the goods are taken from or put." },
          { name: "serial_no", label: "Serial Numbers", type: "text",
            hint: "For serial-tracked items: one number per unit, as many as the quantity, separated by commas." },
          { name: "batch_no", label: "Batch", type: "text", suggest: "batch",
            hint: "Required on batch-tracked items when stock moves. One batch per line: split a quantity from two batches into two lines." },
          { name: "expiry_date", label: "Expiry Date", type: "date",
            hint: "Only needed for a batch that is new: it is stored on the batch." },
        ],
      },
      { key: "taxes", label: "Taxes", fields: TAX_FIELDS },
    ],
    listColumns: ["name", "supplier", "posting_date", "grand_total", "status"],
    searchFields: ["name", "supplier", "supplier_name", "return_against"],
    canSubmit: true,
    canCancel: true,
    conversions: [
      { label: "Create Invoice", targetDoctype: "Purchase Invoice" },
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
      { name: "pricing_date", label: "Pricing Date", type: "date",
        hint: "Prices and pricing rules valid on this date apply. Empty: the document date." },
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
          { name: "location", label: "Storage Location", type: "text", suggest: "location",
            hint: "Required when the warehouse uses storage locations: the place the goods are taken from or put." },
          { name: "serial_no", label: "Serial Numbers", type: "text",
            hint: "For serial-tracked items: one number per unit, as many as the quantity, separated by commas." },
          { name: "batch_no", label: "Batch", type: "text", suggest: "batch",
            hint: "Required on batch-tracked items when stock moves. One batch per line: split a quantity from two batches into two lines." },
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
    searchFields: ["name", "customer", "customer_name"],
    canSubmit: true,
    canCancel: true,
    conversions: [],
  },

  "price-list": {
    slug: "price-list", label: "Price List", dateField: "creation", usesExchangeRate: false,
    fields: [
      { name: "price_list_name", label: "Price List Name", type: "text", required: true },
      { name: "currency", label: "Currency", type: "select", optionsSource: "currency", required: true },
      { name: "selling", label: "Selling", type: "select", options: [{ value: "1", label: "Yes" }, { value: "0", label: "No" }], default: "1" },
      { name: "buying", label: "Buying", type: "select", options: [{ value: "1", label: "Yes" }, { value: "0", label: "No" }], default: "0" },
      { name: "enabled", label: "Enabled", type: "select", options: [{ value: "1", label: "Yes" }, { value: "0", label: "No" }], default: "1" },
    ],
    childTables: [], listColumns: ["name", "price_list_name", "currency", "selling", "buying", "enabled"],
    listFilters: ["enabled"], searchFields: ["name", "price_list_name"],
    canSubmit: false, canCancel: false, conversions: [],
  },
  "item-price": {
    slug: "item-price", label: "Item Price", dateField: "creation", amountField: "rate",
    fields: [
      { name: "item_code", label: "Item", type: "link", linkDoctype: "item", required: true },
      { name: "price_list", label: "Price List", type: "link", linkDoctype: "price-list", required: true },
      { name: "rate", label: "Rate", type: "currency", required: true, hint: "Quoted in the price list currency." },
      { name: "customer", label: "Customer", type: "link", linkDoctype: "customer", hint: "Optional customer-specific price. Leave customer and supplier blank for a general list price." },
      { name: "supplier", label: "Supplier", type: "link", linkDoctype: "supplier" },
      { name: "uom", label: "UOM", type: "text" },
      { name: "min_qty", label: "Min Qty", type: "number", default: 0 },
      { name: "valid_from", label: "Valid From", type: "date" },
      { name: "valid_upto", label: "Valid Upto", type: "date" },
      { name: "enabled", label: "Enabled", type: "select", options: [{ value: "1", label: "Yes" }, { value: "0", label: "No" }], default: "1" },
    ],
    childTables: [], listColumns: ["name", "item_code", "price_list", "customer", "rate", "min_qty", "enabled"],
    listFilters: ["enabled"], searchFields: ["name", "item_code", "price_list", "customer", "supplier"],
    canSubmit: false, canCancel: false, conversions: [],
  },

  "pricing-rule": {
    slug: "pricing-rule",
    label: "Pricing Rule",
    dateField: "valid_from",
    amountField: "discount_percentage",
    fields: [
      { name: "title", label: "Title", type: "text", required: true },
      { name: "apply_on", label: "Apply On", type: "select", options: ["Item Code", "Item Group"], default: "Item Code", required: true },
      { name: "item_code", label: "Item", type: "link", linkDoctype: "item", hint: "Required when Apply On is Item Code." },
      { name: "item_group", label: "Item Group", type: "text", hint: "Required when Apply On is Item Group." },
      { name: "apply_on_value", label: "Apply On Value", type: "text", hint: "Only for an Apply On key added by your deployment (for example a price family): the value the rule is for." },
      { name: "applicable_for", label: "Applicable For", type: "select", options: ["Customer", "Customer Group", "Territory", "Supplier", "Supplier Group"], hint: "Leave blank to apply to everyone. Otherwise fill the matching field below." },
      { name: "customer", label: "Customer", type: "link", linkDoctype: "customer" },
      { name: "customer_group", label: "Customer Group", type: "text" },
      { name: "territory", label: "Territory", type: "text" },
      { name: "supplier", label: "Supplier", type: "link", linkDoctype: "supplier" },
      { name: "supplier_group", label: "Supplier Group", type: "text" },
      { name: "applicable_for_value", label: "Applicable For Value", type: "text", hint: "Only for an Applicable For key added by your deployment (for example a purchasing group): the value the rule is for." },
      { name: "selling", label: "Selling", type: "select", options: ["1", "0"], default: "1" },
      { name: "buying", label: "Buying", type: "select", options: ["1", "0"], default: "0" },
      { name: "rate_or_discount", label: "Type", type: "select", options: ["Discount Percentage", "Discount Amount", "Rate"], default: "Discount Percentage" },
      { name: "discount_percentage", label: "Discount %", type: "number" },
      { name: "discount_amount", label: "Discount Amt", type: "currency" },
      { name: "rate", label: "Rate", type: "currency" },
      { name: "min_qty", label: "Min Qty", type: "number" },
      { name: "max_qty", label: "Max Qty", type: "number", hint: "Zero means no upper limit." },
      { name: "min_amt", label: "Min Amount", type: "currency" },
      { name: "max_amt", label: "Max Amount", type: "currency", hint: "Zero means no upper limit." },
      { name: "valid_from", label: "Valid From", type: "date" },
      { name: "valid_upto", label: "Valid Upto", type: "date" },
      { name: "priority", label: "Priority", type: "number", hint: "The rule with the highest priority applies. Among equals: the more specific match, the higher Min Qty, the narrower validity, the newer Valid From, then the name." },
      { name: "company", label: "Company", type: "link", linkDoctype: "company" },
      { name: "currency", label: "Currency", type: "select", optionsSource: "currency", hint: "Rate and Discount Amount apply only to documents in this currency. Defaults to the company currency." },
      { name: "enabled", label: "Enabled", type: "select", options: ["1", "0"], default: "1" },
    ],
    childTables: [],
    listColumns: ["name", "title", "item_code", "rate_or_discount", "discount_percentage", "status"],
    searchFields: ["name", "title", "item_code", "customer", "supplier"],
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
    searchFields: ["name", "account", "cost_center"],
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
    searchFields: ["name", "party"],
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
      { name: "bank_account_id", label: "External Bank Account", type: "link", linkDoctype: "bank-account", readOnly: true },
      { name: "bank_statement_import", label: "Statement Import", type: "text", readOnly: true },
      { name: "posting_date", label: "Date", type: "date", required: true },
      { name: "value_date", label: "Value Date", type: "date", readOnly: true },
      { name: "currency", label: "Currency", type: "text", readOnly: true },
      { name: "deposit", label: "Deposit", type: "currency" },
      { name: "withdrawal", label: "Withdrawal", type: "currency" },
      { name: "description", label: "Description", type: "text" },
      { name: "remittance_information", label: "Remittance Information", type: "textarea", readOnly: true },
      { name: "reference_number", label: "Reference No", type: "text" },
      { name: "counterparty_name", label: "Counterparty", type: "text", readOnly: true },
      { name: "counterparty_iban", label: "Counterparty IBAN", type: "text", readOnly: true },
      { name: "structured_reference", label: "Structured Reference", type: "text", readOnly: true },
      { name: "bank_transaction_code", label: "Bank Transaction Code", type: "text", readOnly: true },
      { name: "reference_doctype", label: "Matched Type", type: "text", readOnly: true },
      { name: "reference_name", label: "Matched Doc", type: "link", linkDoctypeField: "reference_doctype", readOnly: true },
      { name: "allocated_amount", label: "Allocated", type: "currency", readOnly: true },
      { name: "unallocated_amount", label: "Unallocated", type: "currency", readOnly: true },
      { name: "reconciled_by", label: "Reconciled By", type: "text", readOnly: true },
      { name: "reconciled_at", label: "Reconciled At", type: "datetime", readOnly: true },
      { name: "status", label: "Status", type: "text", readOnly: true },
    ],
    childTables: [{
      key: "details",
      label: "Statement Details",
      fields: [
        { name: "amount", label: "Amount", type: "currency", readOnly: true },
        { name: "currency", label: "Currency", type: "text", readOnly: true },
        { name: "credit_debit_indicator", label: "Direction", type: "text", readOnly: true },
        { name: "debtor_name", label: "Debtor", type: "text", readOnly: true },
        { name: "creditor_name", label: "Creditor", type: "text", readOnly: true },
        { name: "creditor_reference", label: "Creditor Reference", type: "text", readOnly: true },
        { name: "remittance_information", label: "Remittance Information", type: "text", readOnly: true },
      ],
    }],
    listColumns: ["name", "posting_date", "counterparty_name", "deposit", "withdrawal", "currency", "status"],
    searchFields: ["name", "counterparty_name", "remittance_information", "reference_number"],
    canSubmit: false,
    canCancel: false,
    conversions: [],
  },

  "bank-account": {
    slug: "bank-account",
    label: "Bank Account",
    dateField: "creation",
    fields: [
      { name: "account_name", label: "Bank Account Name", type: "text", required: true },
      { name: "company", label: "Company", type: "link", linkDoctype: "company", required: true },
      { name: "account", label: "GL Account", type: "link", linkDoctype: "account", required: true,
        hint: "The Bank-type ledger account where this real bank account is recorded." },
      { name: "iban", label: "IBAN", type: "text", required: true },
      { name: "currency", label: "Currency", type: "select", optionsSource: "currency", required: true },
      { name: "bank_name", label: "Bank", type: "text" },
      { name: "bic", label: "BIC", type: "text" },
      { name: "disabled", label: "Disabled", type: "select", options: [
        { value: "0", label: "No" }, { value: "1", label: "Yes" },
      ], default: "0" },
    ],
    childTables: [],
    listColumns: ["name", "account_name", "company", "account", "iban", "currency", "disabled"],
    canSubmit: false,
    canCancel: false,
    conversions: [],
  },

  // --- Rentals: unit-level assets + date-ranged reservations (ADR-0002).
  // Counted quantities per item, batch and storage location. Saving a draft
  // changes no stock; submitting posts the differences. See docs/stock-counts.md.
  "stock-count": {
    slug: "stock-count",
    label: "Stock Count",
    dateField: "posting_date",
    fields: [
      { name: "warehouse", label: "Warehouse", type: "link", linkDoctype: "warehouse", required: true },
      { name: "posting_date", label: "Posting Date", type: "date", required: true },
      { name: "reason", label: "Reason", type: "text",
        hint: "Why stock is corrected. Required to post the count; kept with every movement it makes." },
      { name: "counted_lines", label: "Counted Lines", type: "number", readOnly: true },
      { name: "difference_lines", label: "Lines With Difference", type: "number", readOnly: true },
      { name: "remarks", label: "Remarks", type: "textarea" },
    ],
    childTables: [
      {
        key: "items", label: "Items", fields: [
          { name: "location", label: "Storage Location", type: "text", suggest: "location" },
          { name: "item_code", label: "Item", type: "link", linkDoctype: "item", required: true },
          { name: "item_name", label: "Item Name", type: "text", readOnly: true },
          { name: "serial_no", label: "Serial Numbers", type: "text",
            hint: "For serial-tracked items: one number per unit, as many as the quantity, separated by commas." },
          { name: "batch_no", label: "Batch", type: "text", suggest: "batch" },
          { name: "expiry_date", label: "Expiry Date", type: "date", hint: "Needed for a batch the system does not know yet." },
          { name: "system_qty", label: "System Qty", type: "number", readOnly: true },
          { name: "counted_qty", label: "Counted Qty", type: "number", default: null,
            hint: "Leave empty for a line that was not counted; it is then left alone. Enter 0 if nothing is there." },
          { name: "difference_qty", label: "Difference", type: "number", readOnly: true },
        ],
      },
    ],
    listColumns: ["name", "warehouse", "posting_date", "counted_lines", "difference_lines", "reason", "status"],
    searchFields: ["name", "warehouse", "reason"],
    canSubmit: true,
    canCancel: true,
    conversions: [],
  },

  // What a customer reported as used from their consignment warehouse.
  // Submitting bills it and takes the goods out, once. See docs/consignment.md.
  "consignment-withdrawal": {
    slug: "consignment-withdrawal",
    label: "Consignment Withdrawal",
    dateField: "posting_date",
    partyField: "customer",
    partyLabel: "Customer",
    fields: [
      { name: "warehouse", label: "Consignment Warehouse", type: "link", linkDoctype: "warehouse", required: true,
        hint: "The customer's consignment warehouse. The customer is taken from it." },
      { name: "customer", label: "Customer", type: "link", linkDoctype: "customer", readOnly: true },
      { name: "posting_date", label: "Posting Date", type: "date", required: true },
      { name: "external_source", label: "Report Source", type: "text",
        hint: "Where the customer's report came from, e.g. the customer's ordering system. Together with the report number it prevents the same report from being billed twice." },
      { name: "external_reference", label: "Report Number", type: "text" },
      { name: "replenish", label: "Replenish", type: "select",
        options: [{ value: "0", label: "No" }, { value: "1", label: "Yes" }], default: "0",
        hint: "If Yes, submitting also drafts a transfer that restocks what was used. It is a separate document; the withdrawal is billed either way." },
      { name: "replenish_from_warehouse", label: "Replenish From", type: "link", linkDoctype: "warehouse",
        hint: "Defaults to the consignment warehouse's 'Replenished From'." },
      { name: "sales_invoice", label: "Sales Invoice", type: "text", readOnly: true },
      { name: "replacement_stock_entry", label: "Replacement Transfer", type: "text", readOnly: true },
      { name: "replenishment_status", label: "Replenishment", type: "text", readOnly: true },
      { name: "remarks", label: "Remarks", type: "textarea" },
    ],
    childTables: [
      {
        key: "items", label: "Items", fields: [
          { name: "item_code", label: "Item", type: "link", linkDoctype: "item", required: true },
          { name: "item_name", label: "Item Name", type: "text", readOnly: true },
          { name: "qty", label: "Qty", type: "number", required: true },
          { name: "serial_no", label: "Serial Numbers", type: "text",
            hint: "For serial-tracked items: one number per unit, as many as the quantity, separated by commas." },
          { name: "batch_no", label: "Batch", type: "text", suggest: "batch", hint: "The batch the customer reported. It must lie in this consignment warehouse." },
          { name: "location", label: "Storage Location", type: "text", suggest: "location" },
          { name: "rate", label: "Rate", type: "currency", default: null, hint: "Leave blank to bill the customer's price." },
        ],
      },
    ],
    listColumns: ["name", "customer", "warehouse", "posting_date", "external_reference", "status", "replenishment_status"],
    searchFields: ["name", "customer", "warehouse", "external_reference", "sales_invoice"],
    canSubmit: true,
    canCancel: true,
    conversions: [],
  },

  // Stock set aside for a sales order line, or with a reason. Status-driven,
  // no submit. See docs/reservations-and-fefo.md.
  "stock-reservation": {
    slug: "stock-reservation",
    label: "Stock Reservation",
    dateField: "creation",
    partyField: "customer",
    partyLabel: "Customer",
    fields: [
      { name: "item_code", label: "Item", type: "link", linkDoctype: "item", required: true },
      { name: "warehouse", label: "Warehouse", type: "link", linkDoctype: "warehouse", required: true },
      { name: "batch_no", label: "Batch", type: "text",
        hint: "Optional: hold exactly this batch. Nobody else can then take it out of the batch." },
      { name: "location", label: "Storage Location", type: "text",
        hint: "Optional: hold the stock in exactly this storage location." },
      { name: "reserved_qty", label: "Reserved Qty", type: "number", required: true,
        hint: "For a sales order line: the total reserved for the line. What is still held goes down as the line is delivered." },
      { name: "status", label: "Status", type: "select",
        options: ["Reserved", "Partially Delivered", "Delivered", "Released", "Cancelled"], default: "Reserved",
        hint: "Set Released to give the stock back. Partially Delivered, Delivered and Cancelled are set by the system." },
      { name: "sales_order", label: "Sales Order", type: "text",
        hint: "Leave empty for stock set aside without an order; Remarks must then say what for." },
      { name: "sales_order_item", label: "Sales Order Line", type: "text" },
      { name: "customer", label: "Customer", type: "link", linkDoctype: "customer", readOnly: true },
      { name: "reserved_by", label: "Reserved By", type: "text", readOnly: true },
      { name: "remarks", label: "Remarks", type: "textarea" },
    ],
    childTables: [],
    listColumns: ["name", "item_code", "warehouse", "reserved_qty", "sales_order", "customer", "status"],
    searchFields: ["name", "item_code", "sales_order", "customer", "remarks"],
    canSubmit: false,
    canCancel: false,
    conversions: [],
  },

  // These are core doctypes (lambda_erp/assets/); they post nothing to the GL
  // or stock ledger. See docs/adr-0002-asset-and-reservation.md.
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
      { name: "warehouse", label: "Home Yard", type: "link", linkDoctype: "warehouse", required: true },
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
      { name: "allocation_mode", label: "Allocation", type: "select", options: ["Unit", "Pool"], default: "Unit",
        hint: "Unit requires a specific machine. Choose Pool explicitly only when the machine will be assigned later." },
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
      { name: "purpose", label: "Purpose", type: "text", hint: "Required for an internal block without a customer (e.g. maintenance)." },
      { name: "company", label: "Company", type: "link", linkDoctype: "company" },
      { name: "pdf_template", label: "PDF Layout", type: "select", optionsSource: "pdfTemplate",
        hint: "How the reservation confirmation looks." },
      { name: "notes", label: "Notes", type: "textarea" },
    ],
    childTables: [],
    listColumns: ["name", "item_code", "asset", "party", "from_datetime", "to_datetime", "status"],
    listFilters: ["status"],
    searchFields: ["name", "party", "asset", "item_code", "voucher_no"],
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
