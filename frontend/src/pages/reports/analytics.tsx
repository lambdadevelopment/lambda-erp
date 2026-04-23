import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useUrlState } from "@/hooks/use-url-state";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useChat } from "@/components/chat/chat-provider";
import { flt, formatCurrency, formatDate, formatNumber } from "@/lib/utils";
import { api } from "@/api/client";

type RuntimeDatasetResult = {
  name: string;
  dataset: string;
  rows: Array<Record<string, unknown>>;
  fields: Record<string, string>;
  row_count: number;
  truncated: boolean;
  limit: number;
};

type RuntimeReportOutput = {
  title?: string;
  summary?: string;
  kpis?: Array<{ label: string; value: number | string; format?: "currency" | "percent" | "number" | "date" }>;
  tables?: Array<{
    id?: string;
    title: string;
    columns: Array<{ key: string; label: string; type?: string; format?: string }>;
    rows: Array<Record<string, unknown>>;
  }>;
  charts?: Array<{
    id?: string;
    title: string;
    type: "bar" | "line" | "pie";
    dataTable?: string;
    data?: Array<Record<string, unknown>>;
    x: string;
    y: string;
  }>;
};

type RuntimeReportTable = NonNullable<RuntimeReportOutput["tables"]>[number];

function buildExampleCustomReport(): string {
  // Rolling 12-month window ending today — no hardcoded company so this works
  // on any tenant's data out of the box.
  const today = new Date();
  const to = today.toISOString().slice(0, 10);
  const start = new Date(today);
  start.setMonth(start.getMonth() - 12);
  const from = start.toISOString().slice(0, 10);

  return JSON.stringify(
    {
      title: "Top Customers by Revenue — Last 12 Months",
      description: "Top customers ranked by net sales revenue over the past 12 months.",
      data_requests: [
        {
          name: "sales",
          dataset: "sales_invoices",
          fields: [
            "posting_date",
            "customer",
            "customer_name",
            "net_total",
            "is_return",
          ],
          filters: {
            posting_date: { from, to },
            is_return: 0,
          },
        },
      ],
      transform_js: `const grouped = helpers.group(sales, ['customer', 'customer_name'], {
  revenue: ['sum', 'net_total'],
  invoice_count: ['count', 'net_total'],
});
const sorted = helpers.sortBy(grouped, 'revenue', 'desc');
const top = helpers.topN(sorted, 10);
const totalRevenue = helpers.sum(grouped, 'revenue');

return {
  title: 'Top Customers by Revenue — Last 12 Months',
  kpis: [
    { label: 'Total Revenue', value: totalRevenue, format: 'currency' },
    { label: 'Top 10 Revenue', value: helpers.sum(top, 'revenue'), format: 'currency' },
    { label: 'Customers Shown', value: top.length, format: 'number' },
  ],
  tables: [
    {
      title: 'Top Customers by Revenue',
      columns: [
        { key: 'customer_name', label: 'Customer', type: 'string' },
        { key: 'invoice_count', label: 'Invoices', type: 'number' },
        { key: 'revenue', label: 'Revenue', type: 'currency' },
      ],
      rows: top.map(function(r) { return {
        customer: r.customer,
        customer_name: r.customer_name || r.customer,
        invoice_count: r.invoice_count,
        revenue: r.revenue,
      }; }),
    },
  ],
  charts: [
    {
      title: 'Top Customers by Revenue',
      type: 'bar',
      x: 'customer_name',
      y: 'revenue',
      dataTable: 'Top Customers by Revenue',
    },
  ],
};`,
    },
    null,
    2,
  );
}

const EXAMPLE_CUSTOM_REPORT = buildExampleCustomReport();

export default function AnalyticsPage() {
  const navigate = useNavigate();
  const { sessions, createSession } = useChat();

  const [reportId] = useUrlState<string>("report_id", "");
  const [metric] = useUrlState<string>("metric", "");
  const [groupBy] = useUrlState<string>("group_by", "");
  const [urlFrom] = useUrlState<string>("from", "");
  const [urlTo] = useUrlState<string>("to", "");
  const [urlCompany] = useUrlState<string>("company", "");
  const [customSpec, setCustomSpec] = useState(EXAMPLE_CUSTOM_REPORT);
  const [runtimeData, setRuntimeData] = useState<RuntimeDatasetResult[]>([]);
  const [runtimeOutput, setRuntimeOutput] = useState<RuntimeReportOutput | null>(null);
  const [runtimeError, setRuntimeError] = useState("");
  const [running, setRunning] = useState(false);
  const [loadingDraft, setLoadingDraft] = useState(false);
  const [sourceChatSessionId, setSourceChatSessionId] = useState("");
  const workerRef = useRef<Worker | null>(null);
  const loadedDraftRef = useRef<string>("");
  const loadedSpecRef = useRef<string>("");
  // Monotonic counter so stale runs (e.g. superseded by a StrictMode
  // double-invoke) can be ignored when their timers or messages fire late.
  const runIdRef = useRef(0);

  const legacySpecText = useMemo(() => {
    if (!metric || !groupBy) return "";
    return JSON.stringify(buildLegacyRuntimeSpec({
      metric,
      groupBy,
      fromDate: urlFrom || undefined,
      toDate: urlTo || undefined,
      company: urlCompany || undefined,
    }), null, 2);
  }, [metric, groupBy, urlFrom, urlTo, urlCompany]);

  const onRunCustom = async (specText?: string) => {
    const runId = ++runIdRef.current;
    const isCurrent = () => runIdRef.current === runId;
    setRuntimeError("");
    setRunning(true);
    try {
      const source = specText ?? customSpec;
      const spec = JSON.parse(source);
      const dataResponse = await api.runtimeData({ requests: spec.data_requests || [] });
      if (!isCurrent()) return;
      setRuntimeData(dataResponse.datasets);

      workerRef.current?.terminate();
      const worker = new Worker(new URL("../../workers/report-runtime.worker.ts", import.meta.url), {
        type: "module",
      });
      workerRef.current = worker;

      const result = await new Promise<RuntimeReportOutput>((resolve, reject) => {
        const timer = window.setTimeout(() => {
          worker.terminate();
          reject(new Error("Report runtime exceeded 4 seconds"));
        }, 4000);
        worker.onmessage = (event) => {
          window.clearTimeout(timer);
          worker.terminate();
          if (workerRef.current === worker) workerRef.current = null;
          if (event.data?.ok) {
            resolve(event.data.output);
          } else {
            reject(new Error(event.data?.error || "Runtime failed"));
          }
        };
        worker.postMessage({
          datasets: dataResponse.datasets,
          params: {},
          transformJs: spec.transform_js || "return {};",
        });
      });

      if (!isCurrent()) return;
      setRuntimeOutput(result);
    } catch (error) {
      if (!isCurrent()) return;
      setRuntimeOutput(null);
      setRuntimeError(error instanceof Error ? error.message : String(error));
    } finally {
      if (isCurrent()) setRunning(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    if (reportId) {
      if (loadedDraftRef.current === reportId) return;
      setLoadingDraft(true);
      setRuntimeError("");
      api.getRuntimeDraft(reportId)
        .then((draft) => {
          if (cancelled) return;
          const nextSpec = JSON.stringify(draft.definition, null, 2);
          loadedDraftRef.current = reportId;
          loadedSpecRef.current = nextSpec;
          setSourceChatSessionId(draft.source_chat_session_id || "");
          setCustomSpec(nextSpec);
          return onRunCustom(nextSpec);
        })
        .catch((error) => {
          if (!cancelled) {
            setRuntimeOutput(null);
            setRuntimeError(error instanceof Error ? error.message : String(error));
          }
        })
        .finally(() => {
          if (!cancelled) setLoadingDraft(false);
        });
      return () => {
        cancelled = true;
      };
    }

    loadedDraftRef.current = "";
    loadedSpecRef.current = "";
    setSourceChatSessionId("");
    if (legacySpecText) {
      loadedSpecRef.current = legacySpecText;
      setCustomSpec(legacySpecText);
      void onRunCustom(legacySpecText);
    }
    return () => {
      cancelled = true;
    };
  }, [reportId, legacySpecText]);

  const showLegacyBanner = Boolean(!reportId && legacySpecText);
  const renderIssues = useMemo(() => describeRuntimeOutputIssues(runtimeOutput), [runtimeOutput]);

  const onFixInChat = async () => {
    const isDirty = customSpec.trim() !== loadedSpecRef.current.trim();
    const reportLabel = reportId || "unsaved-runtime-report";
    const preview = runtimeData.map((dataset) => ({
      name: dataset.name,
      dataset: dataset.dataset,
      row_count: dataset.row_count,
      sample_rows: dataset.rows.slice(0, 3),
    }));
    const parts = [
      "This custom analytics report does not work yet. Please repair the existing report draft instead of creating a new one.",
      "",
      `Report ID: ${reportLabel}`,
      "",
      "Current issue summary:",
      runtimeError || renderIssues.join("\n") || "No runtime error captured; please review and improve the report definition.",
    ];

    if (isDirty) {
      parts.push(
        "",
        "The analytics editor contains unsaved local changes. Use this JSON as the source of truth when repairing the draft:",
        "```json",
        customSpec,
        "```",
      );
    } else {
      parts.push(
        "",
        "Load the current saved draft with `get_custom_analytics_report` before making changes.",
      );
    }

    if (preview.length > 0) {
      parts.push(
        "",
        "Fetched dataset preview:",
        "```json",
        JSON.stringify(preview, null, 2),
        "```",
      );
    }

    const message = parts.join("\n");

    let targetSessionId = sourceChatSessionId;
    if (!targetSessionId) {
      targetSessionId = sessions[0]?.id || "";
    }
    if (!targetSessionId) {
      const created = await createSession();
      targetSessionId = created.id;
    }
    navigate(`/chat/${targetSessionId}`, { state: { prefillMessage: message } });
  };

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Analytics</h2>
            <p className="text-sm text-gray-600">
              All analytics now run through the custom runtime and semantic datasets.
            </p>
          </div>
          <div className="flex items-center gap-3">
            {reportId ? (
              <div className="text-xs text-gray-500">Loaded draft: {reportId}</div>
            ) : showLegacyBanner ? (
              <div className="text-xs text-gray-500">Loaded runtime report from legacy URL filters.</div>
            ) : null}
            {reportId ? (
              <Button variant="secondary" onClick={() => void onFixInChat()}>
                Fix In Chat
              </Button>
            ) : null}
            <Button onClick={() => void onRunCustom()} disabled={running}>
              {running ? "Running..." : "Run Report"}
            </Button>
          </div>
        </div>
      </Card>

      {loadingDraft ? (
        <Card>
          <p className="text-sm text-gray-600">Loading draft...</p>
        </Card>
      ) : runtimeError ? (
        <Card>
          <p className="text-sm text-red-600">{runtimeError}</p>
        </Card>
      ) : runtimeOutput ? (
        <RuntimeReportRenderer output={runtimeOutput} />
      ) : (
        <Card>
          <p className="text-sm text-gray-600">
            Edit the runtime JSON below or open a draft from chat, then run the report.
          </p>
        </Card>
      )}

      {runtimeData.length > 0 && (
        <Card title="Fetched Datasets">
          <div className="space-y-4">
            {runtimeData.map((dataset) => (
              <div key={dataset.name} className="rounded-md border border-gray-200 p-4">
                <div className="flex items-baseline justify-between gap-4">
                  <div>
                    <div className="font-medium text-gray-900">{dataset.name}</div>
                    <div className="text-xs text-gray-500">{dataset.dataset}</div>
                  </div>
                  <div className="text-xs text-gray-500">
                    {dataset.row_count} rows
                    {dataset.truncated ? ` (truncated at ${dataset.limit})` : ""}
                  </div>
                </div>
                <pre className="mt-3 overflow-x-auto rounded bg-gray-50 p-3 text-xs text-gray-700">
                  {JSON.stringify(dataset.rows.slice(0, 5), null, 2)}
                </pre>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card>
        <label className="mb-2 block text-sm font-medium text-gray-700">Report Definition JSON</label>
        <textarea
          className="min-h-[520px] w-full rounded-md border border-gray-300 p-3 font-mono text-xs text-gray-900 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
          value={customSpec}
          onChange={(e) => setCustomSpec(e.target.value)}
          spellCheck={false}
        />
      </Card>
    </div>
  );
}

function RuntimeReportRenderer({ output }: { output: RuntimeReportOutput }) {
  const tables = (output.tables ?? []).map((table, idx) => ({
    ...table,
    id: table.id || `table_${idx + 1}`,
  }));
  const chartTables = buildChartTableIndex(tables);
  return (
    <div className="space-y-4">
      <Card>
        <div className="flex items-baseline justify-between gap-4">
          <div>
            <h3 className="text-lg font-semibold text-gray-900">{output.title || "Custom Report"}</h3>
            {output.summary && <p className="mt-1 text-sm text-gray-600">{output.summary}</p>}
          </div>
        </div>
        {output.kpis?.length ? (
          <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            {output.kpis.map((kpi, idx) => (
              <div key={`${kpi.label}-${idx}`} className="rounded-md border border-gray-200 p-4">
                <div className="text-sm text-gray-500">{kpi.label}</div>
                <div className="mt-1 text-2xl font-semibold text-gray-900">
                  {formatRuntimeValue(kpi.value, kpi.format)}
                </div>
              </div>
            ))}
          </div>
        ) : null}
      </Card>

      {output.charts?.map((chart, idx) => {
        const table = chart.dataTable ? chartTables.get(chart.dataTable) : undefined;
        const rows = chart.data ?? table?.rows ?? [];
        if (!rows.length) {
          return (
            <Card key={chart.id || `chart_${idx + 1}`} title={chart.title}>
              <p className="text-sm text-red-600">Chart has no usable data.</p>
            </Card>
          );
        }
        const xLabels = chart.type === "pie"
          ? []
          : rows.map((row) => String(row[chart.x] ?? ""));
        const layout = computeAxisLayout(xLabels);
        const containerHeight = Math.max(320, 200 + layout.axisHeight);
        return (
          <Card key={chart.id || `chart_${idx + 1}`}>
            <div className="pb-3 text-sm font-semibold text-gray-700">{chart.title}</div>
            <div style={{ height: containerHeight }} className="w-full">
              <ResponsiveContainer width="100%" height="100%">
                {chart.type === "line" ? (
                  <LineChart data={rows} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                    <XAxis
                      dataKey={chart.x}
                      tick={{ fill: "#6b7280", fontSize: 12 }}
                      interval={0}
                      angle={layout.angle}
                      textAnchor={layout.angle === 0 ? "middle" : "end"}
                      height={layout.axisHeight}
                    />
                    <YAxis tick={{ fill: "#6b7280", fontSize: 12 }} tickFormatter={shortNum} />
                    <Tooltip formatter={(v) => formatRuntimeValue(v, inferFormat(table, chart.y))} />
                    <Line type="monotone" dataKey={chart.y} stroke="#3b82f6" strokeWidth={2} dot={{ r: 3 }} />
                  </LineChart>
                ) : chart.type === "pie" ? (
                  <PieChart>
                    <Tooltip formatter={(v) => formatRuntimeValue(v, inferFormat(table, chart.y))} />
                    <Pie data={rows} dataKey={chart.y} nameKey={chart.x} outerRadius={110}>
                      {rows.map((_, rowIdx) => (
                        <Cell key={rowIdx} fill={PIE_COLORS[rowIdx % PIE_COLORS.length]} />
                      ))}
                    </Pie>
                  </PieChart>
                ) : (
                  <BarChart data={rows} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                    <XAxis
                      dataKey={chart.x}
                      tick={{ fill: "#6b7280", fontSize: 12 }}
                      interval={0}
                      angle={layout.angle}
                      textAnchor={layout.angle === 0 ? "middle" : "end"}
                      height={layout.axisHeight}
                    />
                    <YAxis tick={{ fill: "#6b7280", fontSize: 12 }} tickFormatter={shortNum} />
                    <Tooltip formatter={(v) => formatRuntimeValue(v, inferFormat(table, chart.y))} />
                    <Bar dataKey={chart.y} fill="#3b82f6" radius={[4, 4, 0, 0]} />
                  </BarChart>
                )}
              </ResponsiveContainer>
            </div>
          </Card>
        );
      })}

      {tables.map((table) => (
        <Card key={table.id} title={table.title}>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50">
                <tr>
                  {table.columns.map((column) => (
                    <th key={column.key} className="px-4 py-2 text-left font-medium text-gray-500">
                      {column.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {table.rows.map((row, idx) => (
                  <tr key={idx} className="hover:bg-gray-50">
                    {table.columns.map((column) => (
                      <td key={column.key} className="px-4 py-1.5 text-gray-900">
                        {formatRuntimeValue(row[column.key], column.type)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ))}
    </div>
  );
}

function labelFor(groupBy: string): string {
  return groupBy
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function buildChartTableIndex<T extends { id: string; title?: string }>(
  tables: T[],
): Map<string, T> {
  // Charts can reference a table by either its id or its title. Build a
  // lookup map keyed by both so the specialist model — which naturally
  // reaches for "<table title>" — gets a match.
  const map = new Map<string, T>();
  for (const table of tables) {
    map.set(table.id, table);
    if (table.title) map.set(table.title, table);
  }
  return map;
}

function describeRuntimeOutputIssues(output: RuntimeReportOutput | null): string[] {
  if (!output) return [];
  const tables = (output.tables ?? []).map((table, idx) => ({
    ...table,
    id: table.id || `table_${idx + 1}`,
  }));
  const chartTables = buildChartTableIndex(tables);
  const issues: string[] = [];

  for (const chart of output.charts ?? []) {
    const table = chart.dataTable ? chartTables.get(chart.dataTable) : undefined;
    const rows = chart.data ?? table?.rows ?? [];
    if (!rows.length) {
      issues.push(`Chart "${chart.title}" has no usable data.`);
      continue;
    }
    if (typeof chart.y !== "string" || !chart.y) {
      issues.push(`Chart "${chart.title}" has an invalid y field; expected a single string field name.`);
    } else if (!rows.some((row) => row[chart.y] != null)) {
      issues.push(`Chart "${chart.title}" does not have any values for y="${chart.y}".`);
    }
    if (!chart.x || !rows.some((row) => row[chart.x] != null)) {
      issues.push(`Chart "${chart.title}" does not have any values for x="${chart.x}".`);
    }
    if (chart.dataTable && !table) {
      issues.push(`Chart "${chart.title}" references missing table "${chart.dataTable}".`);
    }
  }

  return issues;
}

function buildLegacyRuntimeSpec(args: {
  metric: string;
  groupBy: string;
  fromDate?: string;
  toDate?: string;
  company?: string;
}) {
  const title = `${legacyMetricLabel(args.metric)} by ${labelFor(args.groupBy)}`;
  const timeBucket = args.groupBy === "month" ? "helpers.monthKey" : args.groupBy === "quarter" ? "helpers.quarterKey" : "helpers.yearKey";

  if (args.metric === "sales_revenue") {
    return buildGroupedAmountSpec({
      title,
      dataset: args.groupBy === "item" ? "sales_invoice_lines" : "sales_invoices",
      requestName: "rows",
      keyField: args.groupBy === "item" ? "item_code" : args.groupBy === "customer" ? "customer" : "posting_date",
      valueField: args.groupBy === "item" ? "net_amount" : "grand_total",
      groupBy: args.groupBy,
      timeBucket,
      filters: {
        company: args.company,
        posting_date: args.fromDate || args.toDate ? { from: args.fromDate, to: args.toDate } : undefined,
        is_return: 0,
      },
      fields: args.groupBy === "item"
        ? ["posting_date", "item_code", "net_amount"]
        : ["posting_date", "customer", "grand_total"],
      chartType: args.groupBy === "customer" || args.groupBy === "item" ? "bar" : "line",
    });
  }

  if (args.metric === "sales_returns") {
    return buildGroupedAmountSpec({
      title,
      dataset: "sales_invoices",
      requestName: "rows",
      keyField: args.groupBy === "customer" ? "customer" : "posting_date",
      valueField: "grand_total",
      groupBy: args.groupBy,
      timeBucket,
      filters: {
        company: args.company,
        posting_date: args.fromDate || args.toDate ? { from: args.fromDate, to: args.toDate } : undefined,
        is_return: 1,
      },
      fields: ["posting_date", "customer", "grand_total"],
      chartType: args.groupBy === "customer" ? "bar" : "line",
    });
  }

  if (args.metric === "purchases") {
    return buildGroupedAmountSpec({
      title,
      dataset: args.groupBy === "item" ? "purchase_invoice_lines" : "purchase_invoices",
      requestName: "rows",
      keyField: args.groupBy === "item" ? "item_code" : args.groupBy === "supplier" ? "supplier" : "posting_date",
      valueField: args.groupBy === "item" ? "net_amount" : "grand_total",
      groupBy: args.groupBy,
      timeBucket,
      filters: {
        company: args.company,
        posting_date: args.fromDate || args.toDate ? { from: args.fromDate, to: args.toDate } : undefined,
        is_return: 0,
      },
      fields: args.groupBy === "item"
        ? ["posting_date", "item_code", "net_amount"]
        : ["posting_date", "supplier", "grand_total"],
      chartType: args.groupBy === "supplier" || args.groupBy === "item" ? "bar" : "line",
    });
  }

  if (args.metric === "payments_received" || args.metric === "payments_made") {
    const isReceived = args.metric === "payments_received";
    return buildGroupedAmountSpec({
      title,
      dataset: "payments",
      requestName: "rows",
      keyField: args.groupBy === (isReceived ? "customer" : "supplier") ? "party" : "posting_date",
      valueField: isReceived ? "received_amount" : "paid_amount",
      groupBy: args.groupBy,
      timeBucket,
      filters: {
        company: args.company,
        posting_date: args.fromDate || args.toDate ? { from: args.fromDate, to: args.toDate } : undefined,
        payment_type: isReceived ? "Receive" : "Pay",
        party_type: isReceived ? "Customer" : "Supplier",
      },
      fields: ["posting_date", "party", isReceived ? "received_amount" : "paid_amount"],
      chartType: args.groupBy === (isReceived ? "customer" : "supplier") ? "bar" : "line",
    });
  }

  if (args.metric === "outstanding_ar" || args.metric === "outstanding_ap") {
    const isAr = args.metric === "outstanding_ar";
    const dataset = isAr ? "ar_open_items" : "ap_open_items";
    const partyField = isAr ? "customer" : "supplier";
    const amountField = "outstanding_amount";
    return buildGroupedAmountSpec({
      title,
      dataset,
      requestName: "rows",
      keyField: partyField,
      valueField: amountField,
      groupBy: partyField,
      timeBucket,
      filters: {
        company: args.company,
        posting_date: args.fromDate || args.toDate ? { from: args.fromDate, to: args.toDate } : undefined,
      },
      fields: ["posting_date", partyField, amountField],
      chartType: "bar",
    });
  }

  if (args.metric === "stock_value") {
    return buildGroupedAmountSpec({
      title,
      dataset: "stock_balances",
      requestName: "rows",
      keyField: args.groupBy === "warehouse" ? "warehouse" : "item_code",
      valueField: "stock_value",
      groupBy: args.groupBy,
      timeBucket,
      filters: {
        company: args.company,
      },
      fields: [args.groupBy === "warehouse" ? "warehouse" : "item_code", "stock_value"],
      chartType: "bar",
    });
  }

  return JSON.parse(EXAMPLE_CUSTOM_REPORT);
}

function buildGroupedAmountSpec(args: {
  title: string;
  dataset: string;
  requestName: string;
  keyField: string;
  valueField: string;
  groupBy: string;
  timeBucket: string;
  filters: Record<string, unknown>;
  fields: string[];
  chartType: "bar" | "line";
}) {
  const filters = Object.fromEntries(Object.entries(args.filters).filter(([, value]) => value !== undefined));
  const bucketExpr = ["month", "quarter", "year"].includes(args.groupBy)
    ? `${args.timeBucket}(row.${args.keyField})`
    : `row.${args.keyField} || "—"`;
  return {
    title: args.title,
    data_requests: [
      {
        name: args.requestName,
        dataset: args.dataset,
        fields: args.fields,
        filters,
        limit: 5000,
      },
    ],
    transform_js: `const grouped = helpers.group(
  datasets.${args.requestName}.map((row) => ({
    bucket: ${bucketExpr},
    amount: helpers.flt(row.${args.valueField}),
  })),
  ["bucket"],
  { value: ["sum", "amount"] },
);

const ordered = helpers.sortBy(grouped, ${["month", "quarter", "year"].includes(args.groupBy) ? `"bucket", "asc"` : `"value", "desc"`});

return {
  title: ${JSON.stringify(args.title)},
  kpis: [
    { label: "Rows", value: ordered.length, format: "number" },
    { label: "Total", value: helpers.sum(ordered, "value"), format: "currency" }
  ],
  tables: [
    {
      id: "main",
      title: ${JSON.stringify(args.title)},
      columns: [
        { key: "bucket", label: "Bucket", type: "string" },
        { key: "value", label: "Value", type: "currency" }
      ],
      rows: ordered
    }
  ],
  charts: [
    {
      id: "main_chart",
      title: ${JSON.stringify(args.title)},
      type: ${JSON.stringify(args.chartType)},
      dataTable: "main",
      x: "bucket",
      y: "value"
    }
  ]
};`,
  };
}

function legacyMetricLabel(metric: string) {
  const labels: Record<string, string> = {
    sales_revenue: "Sales Revenue",
    sales_returns: "Sales Returns",
    purchases: "Purchases",
    payments_received: "Payments Received",
    payments_made: "Payments Made",
    outstanding_ar: "Outstanding AR",
    outstanding_ap: "Outstanding AP",
    stock_value: "Stock Value",
  };
  return labels[metric] || "Analytics";
}

function inferFormat(table: RuntimeReportTable | undefined, key: string) {
  return table?.columns.find((column) => column.key === key)?.type || table?.columns.find((column) => column.key === key)?.format;
}

function formatRuntimeValue(value: unknown, type?: string) {
  if (type === "currency") return formatCurrency(flt(value));
  if (type === "percent") return `${(flt(value) * 100).toFixed(1)}%`;
  if (type === "number") return formatNumber(flt(value));
  if (type === "date") return formatDate(String(value ?? ""));
  if (typeof value === "number") return formatNumber(value);
  return String(value ?? "");
}

function shortNum(n: number): string {
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(0)}k`;
  return String(n);
}

const PIE_COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"];

// -----------------------------------------------------------------------------
// X-axis auto-fit
//
// Use Canvas 2d's measureText to get the actual rendered pixel width of each
// tick label at the same font used by recharts ticks. From there we pick an
// angle and axis height that accommodates the longest label without the
// letters getting clipped against the card edge.
// -----------------------------------------------------------------------------

const AXIS_TICK_FONT = '12px -apple-system, system-ui, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';

let _measureCanvas: HTMLCanvasElement | null = null;
function measureTextWidth(text: string, font: string = AXIS_TICK_FONT): number {
  if (typeof document === "undefined") return text.length * 7;
  if (!_measureCanvas) _measureCanvas = document.createElement("canvas");
  const ctx = _measureCanvas.getContext("2d");
  if (!ctx) return text.length * 7;
  ctx.font = font;
  return ctx.measureText(text).width;
}

function computeAxisLayout(labels: string[]): { angle: number; axisHeight: number; maxWidth: number } {
  if (!labels.length) return { angle: 0, axisHeight: 30, maxWidth: 0 };
  const widths = labels.map((label) => measureTextWidth(label));
  const maxWidth = Math.max(1, ...widths);
  let angle = 0;
  if (maxWidth > 55) angle = -30;
  if (maxWidth > 110) angle = -45;
  if (maxWidth > 200) angle = -60;
  if (maxWidth > 300) angle = -90;
  const radians = (Math.abs(angle) * Math.PI) / 180;
  const projected =
    angle === 0 ? 20 : Math.abs(angle) === 90 ? maxWidth : Math.ceil(maxWidth * Math.sin(radians));
  // Pad by ~18px for tick marks, font descenders, and a little breathing room.
  const axisHeight = Math.min(280, Math.max(30, projected + 18));
  return { angle, axisHeight, maxWidth };
}
