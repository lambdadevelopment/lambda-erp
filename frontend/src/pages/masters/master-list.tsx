import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useNavigate, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { SlidersHorizontal } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useMySettings } from "@/hooks/use-my-settings";
import { useUrlState, useUrlPatch } from "@/hooks/use-url-state";
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
  type ColumnDef,
} from "@tanstack/react-table";
import { api } from "@/api/client";
import { usePageTitle } from "@/lib/use-page-title";
import { setListContext } from "@/lib/doc-list-context";
import { getMasterConfig, type MasterFilterDef } from "@/lib/masters";
import { Button } from "@/components/ui/button";

const TYPE_LABELS: Record<string, string> = {
  customer: "Customer",
  supplier: "Supplier",
  item: "Item",
  warehouse: "Warehouse",
  company: "Company",
};

const PAGE_SIZE_OPTIONS = ["25", "50", "100", "200"];

const humanizeCol = (key: string) =>
  key.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");

// Per-master field filters shown as dropdowns (options fetched from the backend).
// Fields must be real columns of the master; the server validates and 400s
// otherwise, so keep these to columns that exist.
// `label` is a fields.* i18n key (falls back to itself if untranslated).
const MASTER_FILTERS: Record<string, MasterFilterDef[]> = {
  customer: [{ field: "customer_group", label: "Customer Group" }, { field: "territory", label: "Territory" }],
  supplier: [{ field: "supplier_group", label: "Supplier Group" }],
  item: [{ field: "item_group", label: "Item Group" }, { field: "stock_uom", label: "Stock UOM" }],
  warehouse: [{ field: "parent_warehouse", label: "Parent Warehouse" }],
  account: [{ field: "root_type", label: "Root Type" }, { field: "account_type", label: "Account Type" }],
};

function MasterFilterSelect({
  type, def, value, onChange,
}: {
  type: string;
  def: MasterFilterDef;
  value: string;
  onChange: (v: string) => void;
}) {
  const { t } = useTranslation();
  const { data } = useQuery({
    queryKey: ["master-filter-values", type, def.field],
    queryFn: () => api.masterFilterValues(type, def.field),
    enabled: !!type,
  });
  const values = data?.values ?? [];
  const label = t(`fields.${def.label}`, { defaultValue: def.label });
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-8 rounded-md bg-surface px-2 text-sm text-fg ring-1 ring-line focus:outline-none focus:ring-2 focus:ring-brand/30"
    >
      <option value="">{label}: {t("common.all")}</option>
      {values.map((v) => (
        <option key={String(v)} value={String(v)}>{String(v)}</option>
      ))}
    </select>
  );
}

export default function MasterListPage() {
  const { type } = useParams<{ type: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const config = getMasterConfig(type ?? "");
  const label = config?.label ?? TYPE_LABELS[type ?? ""] ?? type ?? "";
  usePageTitle(label || null);
  const { t } = useTranslation();
  const notice = (location.state as { notice?: string } | null)?.notice;

  // Stash the list URL so the detail page's DocPager can come "back" to this
  // exact page (Esc/u). Masters have no filter UI, so filters stays empty.
  useEffect(() => {
    if (type) setListContext(`master:${type}`, { filters: {}, search: location.search });
  }, [type, location.search]);

  const [pageSize] = useUrlState<number>("per_page", 50);
  // URL page is 1-indexed; internal 0-indexed for offset math.
  const [urlPage] = useUrlState<number>("page", 1);
  const page = urlPage - 1;
  const patchUrl = useUrlPatch();
  const setPage = (p: number) => patchUrl({ page: p === 0 ? null : p + 1 });
  const setPageSize = (n: number) => patchUrl({ per_page: n, page: null });

  // Free-text search: local input, debounced into the URL (?q=), which resets
  // the page. The committed value drives the query.
  const [urlQ] = useUrlState<string>("q", "");
  const [searchInput, setSearchInput] = useState(urlQ);
  useEffect(() => { setSearchInput(urlQ); }, [urlQ]);
  useEffect(() => {
    const t = setTimeout(() => patchUrl({ q: searchInput || null, page: null }), 300);
    return () => clearTimeout(t);
  }, [searchInput]); // eslint-disable-line react-hooks/exhaustive-deps

  const filterDefs = config?.listFilters ?? MASTER_FILTERS[type ?? ""] ?? [];
  // Each configured filter's committed value lives in the URL (?field=value).
  const filterValues = useMemo(() => {
    const p = new URLSearchParams(location.search);
    const out: Record<string, string> = {};
    for (const f of filterDefs) { const v = p.get(f.field); if (v) out[f.field] = v; }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.search, type]);

  const { data, isLoading } = useQuery({
    queryKey: ["masters", type, page, pageSize, urlQ, filterValues],
    queryFn: () => api.listMasters(type!, {
      include_disabled: 1,
      limit: pageSize,
      offset: page * pageSize,
      ...(urlQ ? { search: urlQ } : {}),
      ...(config?.searchFields?.length ? { search_fields: config.searchFields.join(",") } : {}),
      ...(config?.columnOptions?.length ? { fields: config.columnOptions.join(",") } : {}),
      ...filterValues,
    }),
    enabled: !!type,
  });

  const rows = data?.rows ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const rangeStart = total === 0 ? 0 : page * pageSize + 1;
  const rangeEnd = Math.min(total, (page + 1) * pageSize);

  // Column selector (server-persisted per master type, cross-device). Masters
  // fetch every column and are fast on the `name` PK, so this hides client-side.
  // No per-master config, so the default is "show all"; a saved choice is filtered
  // to columns that still exist.
  const { settings, setSetting } = useMySettings();
  const allColumns = useMemo(
    () => config?.columnOptions ?? (rows.length ? Object.keys(rows[0]) : []),
    [config, rows],
  );
  const savedCols = useMemo(
    () => (settings[`columns.master:${type}`] || "").split(",").map((s) => s.trim()).filter(Boolean),
    [settings, type],
  );
  const chosenCols = savedCols.filter((c) => allColumns.includes(c));
  const configuredDefaults = (config?.listColumns ?? []).filter((c) => allColumns.includes(c));
  const effectiveCols = chosenCols.length
    ? chosenCols
    : configuredDefaults.length
      ? configuredDefaults
      : allColumns;
  const toggleColumn = (col: string) => {
    const next = effectiveCols.includes(col)
      ? effectiveCols.filter((c) => c !== col)
      : [...effectiveCols, col];
    if (next.length === 0) return; // never leave an empty table
    setSetting(`columns.master:${type}`, next.join(","));
  };
  const [pickerOpen, setPickerOpen] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!pickerOpen) return;
    const onDown = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) setPickerOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [pickerOpen]);

  const columns = useMemo<ColumnDef<any, any>[]>(() => {
    if (!rows || rows.length === 0) return [];
    const helper = createColumnHelper<any>();

    return effectiveCols.map((key) =>
      helper.accessor(key, {
        header: t(`fields.${config?.fields.find((field) => field.name === key)?.label ?? humanizeCol(key)}`, {
          defaultValue: config?.fields.find((field) => field.name === key)?.label ?? humanizeCol(key),
        }),
        cell: (info) => {
          const val = info.getValue();
          if (key === "name") {
            const isDisabled = info.row.original.disabled === 1;
            return (
              <Link
                to={`/masters/${type}/${val}`}
                className={isDisabled ? "font-medium text-fg-muted" : "font-medium text-brand transition-colors hover:text-brand/80"}
              >
                {val}
              </Link>
            );
          }
          if (key === "disabled") {
            return val === 1 ? t("common.disabled") : t("common.active");
          }
          const href = config?.columnLinks?.[key]?.(val, info.row.original);
          if (href && val) {
            return (
              <Link
                to={href}
                className="text-sky-600 hover:text-sky-800 hover:underline"
                onClick={(event) => event.stopPropagation()}
              >
                {String(val)}
              </Link>
            );
          }
          return val ?? "-";
        },
      }),
    );
  }, [rows, type, effectiveCols.join(","), t, config]);

  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <div className="space-y-4">
      {notice && (
        <div className="rounded-md bg-amber-50 px-4 py-3 text-sm text-amber-800">
          {notice}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          placeholder={t("common.searchPlaceholder", { defaultValue: "Search…" })}
          className="h-8 w-56 rounded-md bg-surface px-3 text-sm text-fg ring-1 ring-line focus:outline-none focus:ring-2 focus:ring-brand/30"
        />
        {filterDefs.map((f) => (
          <MasterFilterSelect
            key={f.field}
            type={type!}
            def={f}
            value={filterValues[f.field] ?? ""}
            onChange={(v) => patchUrl({ [f.field]: v || null, page: null })}
          />
        ))}
        <div className="ml-auto flex items-center gap-2">
          {allColumns.length > 0 && (
            <div className="relative" ref={pickerRef}>
              <Button variant="secondary" onClick={() => setPickerOpen((o) => !o)}>
                <span className="inline-flex items-center gap-1.5">
                  <SlidersHorizontal className="h-4 w-4" />
                  {t("common.columns", { defaultValue: "Columns" })}
                </span>
              </Button>
              {pickerOpen && (
                <div className="absolute right-0 z-20 mt-2 max-h-80 w-60 overflow-y-auto rounded-lg border border-line bg-surface p-2 shadow-card">
                  <div className="px-2 py-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
                    {t("common.columns", { defaultValue: "Columns" })}
                  </div>
                  {allColumns.map((c) => {
                    const checked = effectiveCols.includes(c);
                    const lastOne = checked && effectiveCols.length === 1;
                    return (
                      <label
                        key={c}
                        className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm text-fg hover:bg-surface-subtle"
                      >
                        <input
                          type="checkbox"
                          className="h-4 w-4 rounded border-line text-brand focus:ring-brand/30 disabled:opacity-50"
                          checked={checked}
                          disabled={lastOne}
                          onChange={() => toggleColumn(c)}
                        />
                        {t(`fields.${config?.fields.find((field) => field.name === c)?.label ?? humanizeCol(c)}`, {
                          defaultValue: config?.fields.find((field) => field.name === c)?.label ?? humanizeCol(c),
                        })}
                      </label>
                    );
                  })}
                </div>
              )}
            </div>
          )}
          {config?.allowCreate !== false && (
            <Link to={`/masters/${type}/new`}>
              <Button>{t("common.new")}</Button>
            </Link>
          )}
        </div>
      </div>

      {isLoading ? (
        <p className="text-fg-muted">{t("common.loading")}</p>
      ) : rows.length === 0 ? (
        <p className="py-8 text-center text-fg-muted">{t("common.noRecords", { defaultValue: "No records found" })}</p>
      ) : (
        <>
          <div className="overflow-x-auto rounded-xl bg-surface ring-1 ring-line shadow-card">
            <table className="min-w-full divide-y divide-line text-sm">
              <thead className="bg-surface-subtle">
                {table.getHeaderGroups().map((headerGroup) => (
                  <tr key={headerGroup.id}>
                    {headerGroup.headers.map((header) => (
                      <th
                        key={header.id}
                        className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-fg-muted"
                      >
                        {header.isPlaceholder
                          ? null
                          : flexRender(
                              header.column.columnDef.header,
                              header.getContext(),
                            )}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody className="divide-y divide-line">
                {table.getRowModel().rows.map((row) => (
                  <tr
                    key={row.id}
                    className={
                      row.original.disabled === 1
                        ? "cursor-pointer bg-surface-subtle text-fg-muted transition-colors hover:bg-surface-subtle/80"
                        : "cursor-pointer text-fg transition-colors hover:bg-surface-subtle"
                    }
                    onClick={() =>
                      navigate(`/masters/${type}/${row.original.name}`)
                    }
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="px-4 py-2.5">
                        {flexRender(
                          cell.column.columnDef.cell,
                          cell.getContext(),
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-fg-muted">
            <div>
              {t("common.showing")} <span className="font-medium text-fg">{rangeStart}–{rangeEnd}</span>{" "}
              {t("common.of")} <span className="font-medium text-fg">{total}</span>
            </div>
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1.5">
                <span className="text-xs text-fg-muted">{t("common.perPage")}</span>
                <select
                  value={pageSize}
                  onChange={(e) => setPageSize(Number(e.target.value))}
                  className="h-8 rounded-md bg-surface px-2 text-sm text-fg ring-1 ring-line transition-all focus:outline-none focus:ring-2 focus:ring-brand/30"
                >
                  {PAGE_SIZE_OPTIONS.map((n) => (
                    <option key={n} value={n}>{n}</option>
                  ))}
                </select>
              </label>
              <button
                onClick={() => setPage(Math.max(0, page - 1))}
                disabled={page === 0}
                className="rounded-md bg-surface px-3 py-1 text-sm text-fg ring-1 ring-line transition-colors hover:bg-surface-subtle disabled:cursor-not-allowed disabled:opacity-40"
              >
                {t("common.prev")}
              </button>
              <span className="text-xs">
                {t("common.page")} <span className="font-medium text-fg">{page + 1}</span> {t("common.of")}{" "}
                <span className="font-medium text-fg">{totalPages}</span>
              </span>
              <button
                onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
                disabled={page >= totalPages - 1}
                className="rounded-md bg-surface px-3 py-1 text-sm text-fg ring-1 ring-line transition-colors hover:bg-surface-subtle disabled:cursor-not-allowed disabled:opacity-40"
              >
                {t("common.next")}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
