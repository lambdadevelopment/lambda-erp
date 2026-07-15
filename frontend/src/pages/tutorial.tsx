import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Card } from "@/components/ui/card";
import { useChat } from "@/components/chat/chat-provider";

interface StepLink {
  to: string;
  label: string;
}

interface Step {
  number: number;
  title: string;
  description: string;
  tip?: string;
  link?: string;
  linkLabel?: string;
  links?: StepLink[];
}

const STEPS: Step[] = [
  {
    number: 1,
    title: "Set Up Your Company",
    description:
      "Create a company with a base currency. This generates your Chart of Accounts (30 standard accounts across Assets, Liabilities, Equity, Income, and Expenses) and a default Cost Center. You can also seed demo data with sample customers, suppliers, and items to get started quickly.",
    link: "/setup",
    linkLabel: "Go to Setup",
  },
  {
    number: 2,
    title: "Create Master Data",
    description:
      "Before you can transact, you need master records. Create at least one Customer, one Supplier, one Item (with a standard rate), and one Warehouse. If you seeded demo data in Step 1, these already exist.",
    tip: "Items have a standard rate that auto-fills when you add them to documents. You can always override the rate per transaction.",
    links: [
      { to: "/masters/customer", label: "Customers" },
      { to: "/masters/supplier", label: "Suppliers" },
      { to: "/masters/item", label: "Items" },
      { to: "/masters/warehouse", label: "Warehouses" },
    ],
  },
  {
    number: 3,
    title: "Create a Quotation",
    description:
      "A Quotation is a non-binding offer to a customer. Select a customer, add line items with quantities and rates, optionally add tax rows, then save. Quotations have no financial impact \u2014 they don't create accounting or stock entries. Set a validity date so the offer expires automatically.",
    link: "/app/quotation/new",
    linkLabel: "New Quotation",
  },
  {
    number: 4,
    title: "Submit and Convert to Sales Order",
    description:
      "Open your saved Quotation and click Submit to confirm it. Then click \u201cCreate Sales Order\u201d to convert it. The Sales Order represents a confirmed commitment from the customer. It still has no financial impact, but it reserves stock for planning purposes.",
    tip: "Only submitted documents can be converted to the next step. Draft \u2192 Submit \u2192 Convert is the standard flow. For quick deals, you can skip the Sales Order and go directly from Quotation to Sales Invoice or Delivery Note.",
    link: "/app/quotation",
    linkLabel: "View Quotations",
  },
  {
    number: 5,
    title: "Check Stock Before Fulfilling",
    description:
      "Before you can deliver, check if you actually have the item in stock. Go to the Stock Balance report and look up the item. If your warehouse has zero quantity, you\u2019ll need to bring stock in first \u2014 either through a Purchase Order (buying from a supplier) or a Stock Entry (manual receipt).",
    tip: "This is a common real-world scenario: you sell something, then realize you need to buy it first. The ERP handles both flows.",
    link: "/reports/stock-balance",
    linkLabel: "Stock Balance",
  },
  {
    number: 6,
    title: "Purchase Cycle: Buy Stock from a Supplier",
    description:
      "If you need to buy stock, you have two valid paths. The standard path is Purchase Order -> Purchase Receipt -> Purchase Invoice: use this when goods arrive before or separately from the supplier bill. If the bill and receipt happen together, you can create the Purchase Invoice directly and enable Update Stock so the same document both receives inventory and records Accounts Payable.",
    tip: "Use Purchase Receipt first when receiving goods separately. Use Purchase Invoice with Update Stock when one step should both receive stock and book the supplier bill. In that direct path, set a warehouse on each stock item row.",
    links: [
      { to: "/app/purchase-order/new", label: "New Purchase Order" },
      { to: "/app/purchase-receipt", label: "Purchase Receipts" },
      { to: "/app/purchase-invoice", label: "Purchase Invoices" },
    ],
  },
  {
    number: 7,
    title: "Create a Delivery Note",
    description:
      "Now that you have stock, go back to your submitted Sales Order and click \u201cCreate Delivery Note.\u201d Set the warehouse on each item row (where the goods ship from), then submit. This moves inventory out of the warehouse \u2014 your stock balance decreases.",
    tip: "The Delivery Note is the shipping document. It reduces stock but doesn\u2019t create an invoice. You can deliver and invoice separately.",
    link: "/app/delivery-note",
    linkLabel: "Delivery Notes",
  },
  {
    number: 8,
    title: "Create and Submit the Sales Invoice",
    description:
      "From the submitted Sales Order, create a Sales Invoice. When you submit the invoice, GL entries are posted: Accounts Receivable is debited (the customer owes you) and Sales Revenue is credited (income earned). If taxes are configured, Tax Payable is also credited. The outstanding amount shows what the customer still owes.",
    link: "/app/sales-invoice",
    linkLabel: "Sales Invoices",
  },
  {
    number: 9,
    title: "Record Customer Payments",
    description:
      "Create a Payment Entry to record money received from the customer. Set the payment type to \u201cReceive,\u201d select the customer, specify the amount, and allocate it against the Sales Invoice. You can make partial payments \u2014 the invoice\u2019s outstanding amount updates accordingly. Create additional Payment Entries until the invoice is fully paid.",
    tip: "Partial payments are common. A 10,000 invoice might be paid as 3,000 now and 7,000 later. Each Payment Entry reduces the outstanding amount.",
    link: "/app/payment-entry/new",
    linkLabel: "New Payment Entry",
  },
  {
    number: 10,
    title: "Stock Entries (Manual Inventory)",
    description:
      "Use Stock Entries for inventory movements that aren\u2019t tied to purchases or sales. Material Receipt adds stock (opening balances, adjustments). Material Issue removes stock (write-offs, internal consumption). Material Transfer moves stock between warehouses. Each entry updates the stock ledger with moving-average valuation.",
    tip: "For purchased goods, use Purchase Receipts instead of Stock Entries \u2014 they link to the Purchase Order and give you a proper audit trail.",
    link: "/app/stock-entry/new",
    linkLabel: "New Stock Entry",
  },
  {
    number: 11,
    title: "Journal Entries",
    description:
      "Journal Entries are manual accounting adjustments \u2014 expense accruals, corrections, reclassifications, opening balances. Each entry must have balanced debits and credits (total debit = total credit). Use these when no other document type fits.",
    link: "/app/journal-entry/new",
    linkLabel: "New Journal Entry",
  },
  {
    number: 12,
    title: "Salary Payments",
    description:
      "Lambda ERP handles salaries through the existing accounting tools. First, accrue the salary expense: create a Journal Entry that debits Salary Expense and credits Salary Payable for the total payroll amount. Then pay the employees: create another Journal Entry (or Payment Entry) that debits Salary Payable and credits your bank account. This two-step process keeps your books accurate \u2014 the expense is recorded in the right period, and the cash outflow is tracked separately.",
    tip: "You can ask the AI assistant to do this for you: \u201cAccrue 15,000 in salaries for April\u201d followed by \u201cPay the April salaries from bank.\u201d It will create the right journal entries automatically.",
    link: "/app/journal-entry/new",
    linkLabel: "New Journal Entry",
  },
  {
    number: 13,
    title: "Returns and Credit Notes",
    description:
      "When a customer returns goods or you need to issue a credit, create a return. Returns use the same document type with negative quantities. Open a submitted Sales Invoice and create a Credit Note (Sales Invoice return) \u2014 this reverses the GL entries and reduces the original invoice\u2019s outstanding amount. For stock, create a Delivery Note return to bring goods back into the warehouse. On the buying side, create a Debit Note (Purchase Invoice return) to reverse a supplier bill, or a Purchase Receipt return to send goods back.",
    tip: "A Credit Note is just a Sales Invoice with is_return=1 and negative quantities. The same GL logic runs \u2014 negative amounts automatically flip to the correct debit/credit sides. For a full sales return, you need both a Credit Note (financials) and a Delivery Note return (stock).",
    links: [
      { to: "/app/sales-invoice", label: "Sales Invoices" },
      { to: "/app/delivery-note", label: "Delivery Notes" },
      { to: "/app/purchase-invoice", label: "Purchase Invoices" },
    ],
  },
  {
    number: 14,
    title: "Run Reports",
    description:
      "Check your books. The Profit & Loss shows income vs expenses and net profit for a period. The Balance Sheet shows your financial position (assets = liabilities + equity). AR Aging shows who owes you money and how overdue it is. AP Aging shows what you owe suppliers. The Trial Balance verifies double-entry integrity. The General Ledger shows every individual posting. Stock Balance shows current inventory.",
    links: [
      { to: "/reports/profit-and-loss", label: "Profit & Loss" },
      { to: "/reports/balance-sheet", label: "Balance Sheet" },
      { to: "/reports/ar-aging", label: "AR Aging" },
      { to: "/reports/ap-aging", label: "AP Aging" },
      { to: "/reports/trial-balance", label: "Trial Balance" },
      { to: "/reports/general-ledger", label: "General Ledger" },
      { to: "/reports/stock-balance", label: "Stock Balance" },
    ],
  },
  {
    number: 15,
    title: "Working in Foreign Currencies",
    description:
      "Your books are kept in a single base currency (chosen at company setup), but you can transact in any currency. Set a currency on an invoice or bill — or give a customer or supplier a default currency — and the exchange rate for that date is looked up automatically and stored on the document. The document keeps its amounts in its own currency, while the General Ledger always posts in your base currency. When you later collect or pay at a different rate, the realized exchange gain or loss is booked automatically to an Exchange Gain/Loss account. You can even hold a foreign-currency bank balance and convert it later at your bank’s rate — the difference versus its carried value is realized then. At month end you can revalue open foreign balances to the closing rate (an unrealized gain/loss that reverses next period), and you can view any financial statement translated into another currency for display.",
    tip: "This is easiest through the AI chat — try “Create a sales invoice for Lumiere Audio in EUR,” “Show me the balance sheet in EUR,” or “What’s our unrealized FX exposure at month end?” The seeded demo already includes a EUR customer (Lumiere Audio SARL) whose invoice was collected at a different rate, plus an open EUR supplier bill — open the General Ledger to see the realized FX postings.",
    links: [
      { to: "/chat", label: "Open AI Chat" },
      { to: "/app/sales-invoice", label: "Sales Invoices" },
      { to: "/reports/general-ledger", label: "General Ledger" },
    ],
  },
];

// Launch the chat with a prefilled message (reuses an existing session or
// creates one), mirroring the analytics "try in chat" flow.
function useTryInChat() {
  const navigate = useNavigate();
  const { sessions, createSession } = useChat();
  return async (prefill: string) => {
    let targetSessionId = sessions[0]?.id || "";
    if (!targetSessionId) {
      try {
        const created = await createSession();
        targetSessionId = created.id;
      } catch {
        navigate("/chat");
        return;
      }
    }
    navigate(`/chat/${targetSessionId}`, { state: { prefillMessage: prefill } });
  };
}

function CompanySetupHighlight() {
  const { t } = useTranslation();
  const tryInChat = useTryInChat();

  return (
    <Card className="border-emerald-200 bg-gradient-to-br from-emerald-50 to-white">
      <div className="flex flex-col gap-4 md:flex-row md:items-start">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-emerald-100 text-lg font-bold text-emerald-700">
          ▶
        </div>
        <div className="flex-1 space-y-3">
          <div>
            <p className="text-xs uppercase tracking-wider text-emerald-700">
              {t("tutorial.setupBadge")}
            </p>
            <h3 className="mt-0.5 text-lg font-semibold text-gray-900">
              {t("tutorial.setupTitle")}
            </h3>
          </div>
          <p className="text-sm leading-relaxed text-gray-700">
            {t("tutorial.setupBody")}
          </p>
          <p className="text-xs leading-relaxed text-gray-500">
            {t("tutorial.setupSectors")}
          </p>
          <div className="flex flex-wrap items-center gap-4 pt-1">
            <button
              type="button"
              onClick={() => tryInChat(t("tutorial.setupPrompt"))}
              className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-emerald-700"
            >
              {t("tutorial.setupGetStarted")} &rarr;
            </button>
            <span className="text-xs text-gray-500">
              {t("tutorial.setupManual")}{" "}
              <Link to="/setup" className="font-medium text-emerald-700 hover:text-emerald-900">
                {t("tutorial.setupManualLink")} &rarr;
              </Link>
            </span>
          </div>
        </div>
      </div>
    </Card>
  );
}

function CustomAnalyticsHighlight() {
  const { t } = useTranslation();
  const tryInChat = useTryInChat();

  const samplePrompts = [
    t("tutorial.caPrompt1"),
    t("tutorial.caPrompt2"),
    t("tutorial.caPrompt3"),
    t("tutorial.caPrompt4"),
  ];

  return (
    <Card className="border-indigo-200 bg-gradient-to-br from-indigo-50 to-white">
      <div className="flex flex-col gap-4 md:flex-row md:items-start">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-lg font-bold text-indigo-700">
          ★
        </div>
        <div className="flex-1 space-y-3">
          <div>
            <h3 className="text-base font-semibold text-gray-900">
              {t("tutorial.caTitle")}
            </h3>
            <p className="mt-1 text-xs uppercase tracking-wider text-indigo-700">
              {t("tutorial.caBadge")}
            </p>
          </div>
          <p className="text-sm leading-relaxed text-gray-700">
            {t("tutorial.caBody")}
          </p>
          <div className="rounded-md border border-indigo-100 bg-white px-3 py-2 text-xs text-gray-600">
            <div className="mb-1 font-medium text-gray-800">{t("tutorial.caTryAsking")}</div>
            <ul className="space-y-0.5">
              {samplePrompts.map((prompt) => (
                <li key={prompt}>
                  <button
                    type="button"
                    onClick={() => tryInChat(prompt)}
                    className="text-left text-indigo-700 hover:text-indigo-900 hover:underline"
                  >
                    &ldquo;{prompt}&rdquo;
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <div className="flex flex-wrap gap-3 pt-1">
            <button
              type="button"
              onClick={() => tryInChat(samplePrompts[2])}
              className="text-sm font-medium text-indigo-700 hover:text-indigo-900"
            >
              {t("tutorial.caTryInChat")} &rarr;
            </button>
            <Link
              to="/reports/analytics"
              className="text-sm font-medium text-indigo-700 hover:text-indigo-900"
            >
              {t("tutorial.caOpenWorkspace")} &rarr;
            </Link>
          </div>
        </div>
      </div>
    </Card>
  );
}

function FlowDiagram() {
  const { t } = useTranslation();
  const chip = (k: string) => t(`tutorial.chips.${k}`, { defaultValue: k });
  return (
    <Card>
      <h3 className="text-base font-semibold text-gray-900 mb-3">
        {t("tutorial.flowTitle")}
      </h3>
      <div className="space-y-4 text-sm text-gray-600">
        <div>
          <div className="font-medium text-gray-800 mb-1">{t("tutorial.salesCycle")}</div>
          <div className="flex flex-wrap items-center gap-1 font-mono text-xs">
            <span className="rounded bg-blue-50 px-2 py-0.5 text-blue-700">{chip("Quotation")}</span>
            <span className="text-gray-400">&rarr;</span>
            <span className="rounded bg-blue-50 px-2 py-0.5 text-blue-700">{chip("Sales Order")}</span>
            <span className="text-gray-400">&rarr;</span>
            <span className="rounded bg-green-50 px-2 py-0.5 text-green-700">{chip("Delivery Note")}</span>
            <span className="text-gray-300">/</span>
            <span className="rounded bg-amber-50 px-2 py-0.5 text-amber-700">{chip("Sales Invoice")}</span>
            <span className="text-gray-400">&rarr;</span>
            <span className="rounded bg-purple-50 px-2 py-0.5 text-purple-700">{chip("Payment Entry")}</span>
          </div>
          <div className="mt-1 text-xs text-gray-400 italic">
            {t("tutorial.flowShortcut")}
          </div>
        </div>
        <div>
          <div className="font-medium text-gray-800 mb-1">{t("tutorial.purchaseCycle")}</div>
          <div className="flex flex-wrap items-center gap-1 font-mono text-xs">
            <span className="rounded bg-blue-50 px-2 py-0.5 text-blue-700">{chip("Purchase Order")}</span>
            <span className="text-gray-400">&rarr;</span>
            <span className="rounded bg-green-50 px-2 py-0.5 text-green-700">{chip("Purchase Receipt")}</span>
            <span className="text-gray-300">/</span>
            <span className="rounded bg-amber-50 px-2 py-0.5 text-amber-700">{chip("Purchase Invoice")}</span>
            <span className="text-gray-400">&rarr;</span>
            <span className="rounded bg-purple-50 px-2 py-0.5 text-purple-700">{chip("Payment Entry")}</span>
          </div>
        </div>
        <div>
          <div className="font-medium text-gray-800 mb-1">{t("tutorial.returns")}</div>
          <div className="flex flex-wrap items-center gap-1 font-mono text-xs">
            <span className="rounded bg-amber-50 px-2 py-0.5 text-amber-700">{chip("Sales Invoice")}</span>
            <span className="text-gray-400">&rarr;</span>
            <span className="rounded bg-red-50 px-2 py-0.5 text-red-700">{chip("Credit Note")}</span>
            <span className="mx-2 text-gray-300">|</span>
            <span className="rounded bg-green-50 px-2 py-0.5 text-green-700">{chip("Delivery Note")}</span>
            <span className="text-gray-400">&rarr;</span>
            <span className="rounded bg-red-50 px-2 py-0.5 text-red-700">{chip("DN Return")}</span>
            <span className="mx-2 text-gray-300">|</span>
            <span className="rounded bg-amber-50 px-2 py-0.5 text-amber-700">{chip("Purchase Invoice")}</span>
            <span className="text-gray-400">&rarr;</span>
            <span className="rounded bg-red-50 px-2 py-0.5 text-red-700">{chip("Debit Note")}</span>
          </div>
        </div>
        <div className="border-t pt-3 text-xs text-gray-500">
          <span className="rounded bg-green-50 px-1.5 py-0.5 text-green-700">Green</span> = {t("tutorial.legendStock")},{" "}
          <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-700">Amber</span> = {t("tutorial.legendGl")},{" "}
          <span className="rounded bg-purple-50 px-1.5 py-0.5 text-purple-700">Purple</span> = {t("tutorial.legendCash")},{" "}
          <span className="rounded bg-red-50 px-1.5 py-0.5 text-red-700">Red</span> = {t("tutorial.legendReversal")}
        </div>
      </div>
    </Card>
  );
}

function LifecycleCard() {
  const { t } = useTranslation();
  const chip = (k: string) => t(`tutorial.chips.${k}`, { defaultValue: k });
  return (
    <Card>
      <h3 className="text-base font-semibold text-gray-900 mb-3">
        {t("tutorial.lifecycleTitle")}
      </h3>
      <div className="space-y-2 text-sm text-gray-600">
        <div className="flex flex-wrap items-center gap-2 font-mono text-xs">
          <span className="rounded bg-gray-100 px-2 py-0.5 text-gray-600">{chip("Draft")}</span>
          <span className="text-gray-400">&rarr; save()</span>
          <span className="rounded bg-gray-100 px-2 py-0.5 text-gray-600">{chip("Draft")}</span>
          <span className="text-gray-400">&rarr; submit()</span>
          <span className="rounded bg-blue-100 px-2 py-0.5 text-blue-700">{chip("Submitted")}</span>
          <span className="text-gray-400">&rarr; cancel()</span>
          <span className="rounded bg-red-100 px-2 py-0.5 text-red-700">{chip("Cancelled")}</span>
        </div>
        <ul className="list-disc pl-5 space-y-1 text-xs text-gray-500">
          <li><strong>{t("tutorial.lcDraftLabel")}</strong> {t("tutorial.lcDraftDesc")}</li>
          <li><strong>{t("tutorial.lcSubmittedLabel")}</strong> {t("tutorial.lcSubmittedDesc")}</li>
          <li><strong>{t("tutorial.lcCancelledLabel")}</strong> {t("tutorial.lcCancelledDesc")}</li>
          <li>{t("tutorial.lcNoDelete")}</li>
          <li>{t("tutorial.lcCorrect")}</li>
        </ul>
      </div>
    </Card>
  );
}

export default function TutorialPage() {
  const { t } = useTranslation();
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-gray-900">
          {t("tutorial.pageTitle")}
        </h2>
        <p className="mt-1 text-sm text-gray-500">
          {t("tutorial.intro")}
        </p>
        <Link
          to="/chat"
          className="mt-3 inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
        >
          {t("tutorial.openChat")} &rarr;
        </Link>
        <p className="mt-4 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
          <strong>{t("tutorial.tipLabel")}</strong> {t("tutorial.topTip")}
        </p>
        <p className="mt-4 text-sm text-gray-500">
          {t("tutorial.manualIntro")}
        </p>
      </div>

      <CompanySetupHighlight />
      <CustomAnalyticsHighlight />
      <FlowDiagram />
      <LifecycleCard />

      {STEPS.map((step) => (
        <Card key={step.number}>
          <div className="flex gap-4">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-100 text-sm font-bold text-blue-700">
              {step.number}
            </div>
            <div className="space-y-2">
              <h3 className="text-base font-semibold text-gray-900">
                {t(`tutorial.steps.${step.number}.title`, { defaultValue: step.title })}
              </h3>
              <p className="text-sm leading-relaxed text-gray-600">
                {t(`tutorial.steps.${step.number}.description`, { defaultValue: step.description })}
              </p>
              {step.tip && (
                <p className="text-xs leading-relaxed text-amber-700 bg-amber-50 rounded px-3 py-2">
                  <strong>{t("tutorial.tipLabel")}</strong> {t(`tutorial.steps.${step.number}.tip`, { defaultValue: step.tip })}
                </p>
              )}
              <div className="flex flex-wrap gap-3 pt-1">
                {step.link && step.linkLabel && (
                  <Link
                    to={step.link}
                    className="text-sm font-medium text-blue-600 hover:text-blue-800"
                  >
                    {t(`tutorial.links.${step.linkLabel}`, { defaultValue: step.linkLabel })} &rarr;
                  </Link>
                )}
                {step.links?.map((l) => (
                  <Link
                    key={l.to}
                    to={l.to}
                    className="text-sm font-medium text-blue-600 hover:text-blue-800"
                  >
                    {t(`tutorial.links.${l.label}`, { defaultValue: l.label })} &rarr;
                  </Link>
                ))}
              </div>
            </div>
          </div>
        </Card>
      ))}

      <Card>
        <div className="flex gap-4">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-green-100 text-sm font-bold text-green-700">
            ✓
          </div>
          <div className="space-y-2">
            <h3 className="text-base font-semibold text-gray-900">
              {t("tutorial.readyTitle")}
            </h3>
            <p className="text-sm leading-relaxed text-gray-600">
              {t("tutorial.readyBody1")}
            </p>
            <p className="text-sm leading-relaxed text-gray-600">
              {t("tutorial.readyBody2")}
            </p>
            <Link
              to="/chat"
              className="inline-block text-sm font-medium text-blue-600 hover:text-blue-800"
            >
              {t("tutorial.openChat")} &rarr;
            </Link>
          </div>
        </div>
      </Card>
    </div>
  );
}
