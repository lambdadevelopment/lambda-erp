export type RuntimeDataset = {
  name: string;
  rows: Array<Record<string, unknown>>;
};

type ValueFormat = "currency" | "percent" | "number" | "date" | "string";
type AggregateOp = "sum" | "count" | "avg" | "min" | "max";

export type DeclarativeReportDefinition = {
  version: 1;
  summary?: string;
  kpis?: Array<{
    label: string;
    source: string;
    op: AggregateOp;
    field?: string;
    format?: Exclude<ValueFormat, "string">;
  }>;
  tables: Array<{
    id: string;
    title: string;
    source: string;
    dimensions?: Array<{
      field: string;
      key?: string;
      bucket?: "month" | "quarter" | "year";
      fallback?: string;
    }>;
    measures?: Array<{
      key: string;
      label?: string;
      op: AggregateOp;
      field?: string;
      type?: "currency" | "percent" | "number";
    }>;
    columns: Array<{ key: string; label: string; type?: ValueFormat }>;
    sort?: Array<{ field: string; direction?: "asc" | "desc" }>;
    limit?: number;
  }>;
  charts?: Array<{
    id?: string;
    title: string;
    type: "bar" | "line" | "pie";
    data_table: string;
    x: string;
    y?: string;
    series?: Array<{ key: string; label?: string }>;
  }>;
};

export type DeclarativeReportSpec = {
  title: string;
  description?: string;
  data_requests: Array<{
    name?: string;
    dataset: string;
    fields?: string[];
    filters?: Record<string, unknown>;
    limit?: number;
  }>;
  report: DeclarativeReportDefinition;
};

export type RuntimeReportOutput = {
  title?: string;
  summary?: string;
  kpis?: Array<{ label: string; value: number | string; format?: Exclude<ValueFormat, "string"> }>;
  tables?: Array<{
    id?: string;
    title: string;
    columns: Array<{ key: string; label: string; type?: ValueFormat; format?: string }>;
    rows: Array<Record<string, unknown>>;
  }>;
  charts?: Array<{
    id?: string;
    title: string;
    type: "bar" | "line" | "pie";
    dataTable?: string;
    data?: Array<Record<string, unknown>>;
    x: string;
    y?: string;
    series?: Array<{ key: string; label?: string }>;
  }>;
};

function numberValue(value: unknown): number {
  const number = Number(value ?? 0);
  return Number.isFinite(number) ? number : 0;
}

function bucketValue(value: unknown, bucket?: "month" | "quarter" | "year"): unknown {
  if (!bucket) return value;
  const text = String(value ?? "");
  if (bucket === "month") return text.slice(0, 7);
  if (bucket === "year") return text.slice(0, 4);
  const year = text.slice(0, 4);
  const month = Number(text.slice(5, 7));
  return year && month ? `${year}-Q${Math.floor((month + 2) / 3)}` : "";
}

function aggregate(rows: Array<Record<string, unknown>>, op: AggregateOp, field?: string): number {
  if (op === "count") return field ? rows.filter((row) => row[field] != null).length : rows.length;
  const values = field
    ? rows.filter((row) => row[field] != null).map((row) => numberValue(row[field]))
    : [];
  if (!values.length) return 0;
  if (op === "sum") return values.reduce((sum, value) => sum + value, 0);
  if (op === "avg") return values.reduce((sum, value) => sum + value, 0) / values.length;
  if (op === "min") return Math.min(...values);
  return Math.max(...values);
}

function compareValues(left: unknown, right: unknown): number {
  if (left == null && right == null) return 0;
  if (left == null) return 1;
  if (right == null) return -1;
  if (typeof left === "number" && typeof right === "number") return left - right;
  return String(left).localeCompare(String(right), undefined, { numeric: true });
}

function buildTable(
  definition: DeclarativeReportDefinition["tables"][number],
  sources: Map<string, Array<Record<string, unknown>>>,
) {
  const sourceRows = sources.get(definition.source);
  if (!sourceRows) throw new Error(`Unknown report source: ${definition.source}`);
  const dimensions = definition.dimensions ?? [];
  const measures = definition.measures ?? [];
  let rows: Array<Record<string, unknown>>;

  if (!dimensions.length && !measures.length) {
    rows = sourceRows.map((row) => ({ ...row }));
  } else {
    const groups = new Map<string, { values: Record<string, unknown>; rows: Array<Record<string, unknown>> }>();
    for (const sourceRow of sourceRows) {
      const values: Record<string, unknown> = {};
      for (const dimension of dimensions) {
        const key = dimension.key || dimension.field;
        const raw = bucketValue(sourceRow[dimension.field], dimension.bucket);
        values[key] = raw == null || raw === "" ? (dimension.fallback ?? raw) : raw;
      }
      const groupKey = JSON.stringify(dimensions.map((dimension) => values[dimension.key || dimension.field]));
      const group = groups.get(groupKey) ?? { values, rows: [] };
      group.rows.push(sourceRow);
      groups.set(groupKey, group);
    }
    // An aggregate-only report over an empty dataset still has one zero row.
    if (!dimensions.length && !groups.size) groups.set("[]", { values: {}, rows: [] });
    rows = Array.from(groups.values()).map((group) => {
      const row = { ...group.values };
      for (const measure of measures) {
        row[measure.key] = aggregate(group.rows, measure.op, measure.field);
      }
      return row;
    });
  }

  for (const sort of [...(definition.sort ?? [])].reverse()) {
    const factor = sort.direction === "desc" ? -1 : 1;
    rows.sort((left, right) => compareValues(left[sort.field], right[sort.field]) * factor);
  }
  if (definition.limit) rows = rows.slice(0, definition.limit);
  return {
    id: definition.id,
    title: definition.title,
    columns: definition.columns,
    rows,
  };
}

/** Execute a bounded data-description language. No report-provided string is evaluated as code. */
export function executeDeclarativeReport(
  spec: DeclarativeReportSpec,
  datasets: RuntimeDataset[],
): RuntimeReportOutput {
  if (!spec.report || spec.report.version !== 1 || !Array.isArray(spec.report.tables)) {
    throw new Error("Unsupported custom report definition. Create a new version 1 report.");
  }
  const sources = new Map(datasets.map((dataset) => [dataset.name, dataset.rows]));
  const tables = spec.report.tables.map((definition) => {
    if (sources.has(definition.id)) throw new Error(`Duplicate report source/table id: ${definition.id}`);
    const table = buildTable(definition, sources);
    sources.set(definition.id, table.rows);
    return table;
  });
  const tableIds = new Set(tables.map((table) => table.id));
  const charts = (spec.report.charts ?? []).map((chart) => {
    if (!tableIds.has(chart.data_table)) {
      throw new Error(`Chart "${chart.title}" references unknown table "${chart.data_table}".`);
    }
    return { ...chart, dataTable: chart.data_table };
  });
  return {
    title: spec.title,
    summary: spec.report.summary,
    kpis: (spec.report.kpis ?? []).map((kpi) => {
      const rows = sources.get(kpi.source);
      if (!rows) throw new Error(`Unknown KPI source: ${kpi.source}`);
      return {
        label: kpi.label,
        value: aggregate(rows, kpi.op, kpi.field),
        format: kpi.format,
      };
    }),
    tables,
    charts,
  };
}
