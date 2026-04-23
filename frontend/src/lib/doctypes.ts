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
  type: "text" | "number" | "currency" | "date" | "link" | "select" | "textarea";
  required?: boolean;
  readOnly?: boolean;
  linkDoctype?: string; // for type=link, which master to search
  linkDoctypeField?: string; // for type=link, read the linkDoctype from this sibling field's value (lowercase)
  // For type=select: either plain strings (shown as-is) or {value, label}
  // pairs when the stored value is machine-friendly (e.g. "1"/"0") but the
  // user-visible label should read differently ("Yes"/"No").
  options?: Array<string | { value: string; label: string }>;
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
  canSubmit: boolean;
  canCancel: boolean;
  conversions: ConversionDef[];
}

// --- Shared child table definitions ---

const ITEM_FIELDS: FieldDef[] = [
  { name: "item_code", label: "Item", type: "link", linkDoctype: "item", required: true },
  { name: "item_name", label: "Item Name", type: "text", readOnly: true },
  { name: "qty", label: "Qty", type: "number", required: true },
  { name: "rate", label: "Rate", type: "currency", required: true },
  { name: "amount", label: "Amount", type: "currency", readOnly: true },
];

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
      { name: "net_total", label: "Net Total", type: "currency", readOnly: true },
      { name: "total_taxes_and_charges", label: "Tax", type: "currency", readOnly: true },
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
      { label: "Create Sales Order", targetDoctype: "Sales Order" },
      { label: "Create Sales Invoice", targetDoctype: "Sales Invoice" },
      { label: "Create Delivery Note", targetDoctype: "Delivery Note" },
    ],
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
};

export function getDoctypeConfig(slug: string): DoctypeConfig | undefined {
  return CONFIGS[slug];
}

export function getAllDoctypeConfigs(): DoctypeConfig[] {
  return Object.values(CONFIGS);
}

export const DOCTYPE_SLUGS = Object.keys(CONFIGS);
