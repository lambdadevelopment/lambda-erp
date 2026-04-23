type RuntimeDataset = {
  name: string;
  dataset: string;
  rows: Array<Record<string, unknown>>;
  fields: Record<string, string>;
};

type RuntimeMessage = {
  datasets: RuntimeDataset[];
  params?: Record<string, unknown>;
  transformJs: string;
};

function flt(value: unknown, precision?: number) {
  const n = Number(value ?? 0) || 0;
  return precision == null ? n : Number(n.toFixed(precision));
}

const helpers = {
  flt,
  sum<T extends Record<string, unknown>>(rows: T[], fieldOrFn?: string | ((row: T) => unknown)) {
    return rows.reduce((acc, row) => {
      if (typeof fieldOrFn === "function") return acc + flt(fieldOrFn(row));
      if (typeof fieldOrFn === "string") return acc + flt(row[fieldOrFn]);
      return acc + flt(row as unknown);
    }, 0);
  },
  count<T>(rows: T[]) {
    return rows.length;
  },
  sortBy<T extends Record<string, unknown>>(rows: T[], field: string, dir: "asc" | "desc" | boolean = "asc") {
    const normalizedDir = dir === true ? "desc" : dir === false ? "asc" : dir;
    const factor = normalizedDir === "desc" ? -1 : 1;
    return [...rows].sort((a, b) => {
      const av = a[field];
      const bv = b[field];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (av < bv) return -1 * factor;
      if (av > bv) return 1 * factor;
      return 0;
    });
  },
  topN<T extends Record<string, unknown>>(rows: T[], fieldOrN: string | number, n = 10) {
    if (typeof fieldOrN === "number") return rows.slice(0, fieldOrN);
    return helpers.sortBy(rows, fieldOrN, "desc").slice(0, n);
  },
  group<T extends Record<string, unknown>>(
    rows: T[],
    keysOrFn: string[] | ((row: T) => unknown),
    measures?: Record<string, ["sum" | "count", string?]>,
  ) {
    if (typeof keysOrFn === "function") {
      const map = new Map<string, { key: unknown; rows: T[] }>();
      for (const row of rows) {
        const key = keysOrFn(row);
        const mapKey = JSON.stringify(key);
        if (!map.has(mapKey)) {
          map.set(mapKey, { key, rows: [] });
        }
        map.get(mapKey)!.rows.push(row);
      }
      return Array.from(map.values());
    }

    const keys = keysOrFn;
    const map = new Map<string, Record<string, unknown>>();
    for (const row of rows) {
      const keyValues = keys.map((key) => row[key]);
      const mapKey = JSON.stringify(keyValues);
      if (!map.has(mapKey)) {
        const seed: Record<string, unknown> = {};
        keys.forEach((key, idx) => {
          seed[key] = keyValues[idx];
        });
        for (const [alias, [op]] of Object.entries(measures || {})) {
          seed[alias] = op === "count" ? 0 : 0;
        }
        map.set(mapKey, seed);
      }
      const bucket = map.get(mapKey)!;
      for (const [alias, [op, field]] of Object.entries(measures || {})) {
        if (op === "count") {
          bucket[alias] = flt(bucket[alias]) + 1;
        } else {
          bucket[alias] = flt(bucket[alias]) + flt(row[field || alias]);
        }
      }
    }
    return Array.from(map.values());
  },
  leftJoin<
    TLeft extends Record<string, unknown>,
    TRight extends Record<string, unknown>,
  >(left: TLeft[], right: TRight[], leftKey: string, rightKey = leftKey) {
    const index = new Map(right.map((row) => [row[rightKey], row]));
    return left.map((row) => ({
      ...row,
      ...(index.get(row[leftKey]) || {}),
    }));
  },
  monthKey(value: unknown) {
    return String(value ?? "").slice(0, 7);
  },
  quarterKey(value: unknown) {
    const s = String(value ?? "");
    const year = s.slice(0, 4);
    const month = Number(s.slice(5, 7));
    if (!year || !month) return "";
    return `${year}-Q${Math.floor((month + 2) / 3)}`;
  },
  yearKey(value: unknown) {
    return String(value ?? "").slice(0, 4);
  },
  pivot(
    rows: Array<Record<string, unknown>>,
    indexField: string,
    columnField: string,
    valueField: string,
  ) {
    const buckets = new Map<string, Record<string, unknown>>();
    for (const row of rows) {
      const index = String(row[indexField] ?? "—");
      const column = String(row[columnField] ?? "—");
      if (!buckets.has(index)) {
        buckets.set(index, { [indexField]: index });
      }
      const bucket = buckets.get(index)!;
      bucket[column] = flt(bucket[column]) + flt(row[valueField]);
    }
    return Array.from(buckets.values());
  },
};

function validateOutput(output: any) {
  if (!output || typeof output !== "object") {
    throw new Error("Transform must return an object");
  }
  if (output.tables && !Array.isArray(output.tables)) {
    throw new Error("tables must be an array");
  }
  if (output.charts && !Array.isArray(output.charts)) {
    throw new Error("charts must be an array");
  }
  if (output.kpis && !Array.isArray(output.kpis)) {
    throw new Error("kpis must be an array");
  }
  return output;
}

self.onmessage = (event: MessageEvent<RuntimeMessage>) => {
  try {
    const datasetMap = Object.fromEntries(event.data.datasets.map((d) => [d.name, d.rows]));
    const scopeDecl = event.data.datasets
      .map((d) => d.name)
      .filter((name) => /^[$A-Z_][0-9A-Z_$]*$/i.test(name))
      .map((name) => `const ${name} = datasets[${JSON.stringify(name)}];`)
      .join("\n");
    const transform = new Function(
      "datasets",
      "params",
      "helpers",
      `"use strict";\n${scopeDecl}\n${event.data.transformJs}`,
    );
    const output = transform(datasetMap, event.data.params || {}, helpers);
    self.postMessage({ ok: true, output: validateOutput(output) });
  } catch (error) {
    self.postMessage({
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    });
  }
};
