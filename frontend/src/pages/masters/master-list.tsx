import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useNavigate, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronUp, ChevronsUpDown, SlidersHorizontal } from "lucide-react";
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
import { ListPager } from "@/components/list-pager";
import { Button } from "@/components/ui/button";
import { formatDate, formatDateTime } from "@/lib/utils";

const TYPE_LABELS: Record<string, string> = {
  customer: "Customer",
  supplier: "Supplier",
  item: "Item",
  warehouse: "Warehouse",
  company: "Company",
};

const humanizeCol = (key: string) =>
  key.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");

const isDateColumn = (col: string) =>
  col === "modified" || col === "creation" || col.endsWith("_date") ||
  col.endsWith("_at") || col.endsWith("_datetime");

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

  const [pageSize] = useUrlState<number>("per_page", 50);
  // URL page is 1-indexed; internal 0-indexed for offset math.
  const [urlPage] = useUrlState<number>("page", 1);
  const [showDisabled] = useUrlState<string>("include_disabled", "");
  const page = urlPage - 1;
  const patchUrl = useUrlPatch();
  const setPage = (p: number) => patchUrl({ page: p === 0 ? null : p + 1 });
  const setPageSize = (n: number) => patchUrl({ per_page: n, page: null });
  const setShowDisabled = (on: boolean) =>
    patchUrl({ include_disabled: on ? "1" : null, page: null });

  // Match document lists: the URL is immediate/shareable state and the user's
  // last choice becomes this master type's cross-device default.
  const { settings, setSetting } = useMySettings();
  const [urlOrderBy] = useUrlState<string>("order_by", "");
  const [urlOrder] = useUrlState<string>("order", "");
  const [savedSortCol, savedSortDir] = (settings[`sort.master:${type}`] || "").split(":");
  const activeSortCol = urlOrderBy || savedSortCol || "";
  const activeSortDir = (urlOrderBy ? urlOrder : savedSortDir) || "asc";
  const toggleSort = (col: string) => {
    const next = activeSortCol === col
      ? (activeSortDir === "desc" ? "asc" : "desc")
      : (isDateColumn(col) ? "desc" : "asc");
    patchUrl({ order_by: col, order: next, page: null });
    setSetting(`sort.master:${type}`, `${col}:${next}`);
  };

  // Free-text search: local input, debounced into the URL (?q=), which resets
  // the page. The committed value drives the query.
  const [urlQ] = useUrlState<string>("q", "");
  const [searchInput, setSearchInput] = useState(urlQ);
  useEffect(() => { setSearchInput(urlQ); }, [urlQ]);
  useEffect(() => {
    if (searchInput === urlQ) return;
    const t = setTimeout(() => patchUrl({ q: searchInput || null, page: null }), 300);
    return () => clearTimeout(t);
  }, [searchInput, urlQ]); // eslint-disable-line react-hooks/exhaustive-deps

  const filterDefs = config?.listFilters ?? MASTER_FILTERS[type ?? ""] ?? [];
  // Each configured filter's committed value lives in the URL (?field=value).
  const filterValues = useMemo(() => {
    const p = new URLSearchParams(location.search);
    const out: Record<string, string> = {};
    for (const f of filterDefs) { const v = p.get(f.field); if (v) out[f.field] = v; }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.search, type]);

  const listParams = useMemo(() => ({
      limit: pageSize,
      offset: page * pageSize,
      ...(showDisabled ? { include_disabled: 1 } : {}),
      ...(urlQ ? { search: urlQ } : {}),
      ...(config?.searchFields?.length ? { search_fields: config.searchFields.join(",") } : {}),
      ...(config?.columnOptions?.length ? { fields: config.columnOptions.join(",") } : {}),
      ...(activeSortCol ? { order_by: activeSortCol, order: activeSortDir } : {}),
      ...filterValues,
    }), [page, pageSize, showDisabled, urlQ, filterValues, config, activeSortCol, activeSortDir]);

  const { data, isLoading } = useQuery({
    queryKey: ["masters", type, listParams],
    queryFn: () => api.listMasters(type!, listParams),
    enabled: !!type,
  });

  // Detail prev/next and back-to-list now inherit master search, filters, and
  // sorting too. Projection/pagination do not affect membership or order.
  useEffect(() => {
    if (!type) return;
    const { limit, offset, fields, ...ctxFilters } = listParams;
    setListContext(`master:${type}`, { filters: ctxFilters, search: location.search });
  }, [type, listParams, location.search]);

  const rows = data?.rows ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const rangeStart = total === 0 ? 0 : page * pageSize + 1;
  const rangeEnd = Math.min(total, (page + 1) * pageSize);

  // Column selector (server-persisted per master type, cross-device). Registered
  // masters declare lightweight list columns; legacy built-ins discover their
  // choices from the returned row. Saved choices are filtered to columns that
  // still exist.
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
          const field = config?.fields.find((candidate) => candidate.name === key);
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
          if (field?.type === "date") return formatDate(val);
          if (field?.type === "datetime") return formatDateTime(val);
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
        <label className="flex h-8 items-center gap-2 text-sm text-fg-muted">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-line text-brand focus:ring-brand/30"
            checked={!!showDisabled}
            onChange={(event) => setShowDisabled(event.target.checked)}
          />
          {t("common.showDisabled")}
        </label>
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
          <ListPager
            page={page}
            totalPages={totalPages}
            pageSize={pageSize}
            total={total}
            rangeStart={rangeStart}
            rangeEnd={rangeEnd}
            setPage={setPage}
            setPageSize={setPageSize}
          />
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
                        {header.isPlaceholder ? null : (
                          <button
                            type="button"
                            onClick={() => toggleSort(header.column.id)}
                            title={t("common.sortByColumn", { defaultValue: "Sort by this column" })}
                            className="group inline-flex items-center gap-1 uppercase tracking-wide transition-colors hover:text-fg"
                          >
                            {flexRender(header.column.columnDef.header, header.getContext())}
                            {activeSortCol === header.column.id
                              ? (activeSortDir === "asc"
                                  ? <ChevronUp className="h-3.5 w-3.5" />
                                  : <ChevronDown className="h-3.5 w-3.5" />)
                              : <ChevronsUpDown className="h-3.5 w-3.5 opacity-0 transition-opacity group-hover:opacity-40" />}
                          </button>
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

          <ListPager
            page={page}
            totalPages={totalPages}
            pageSize={pageSize}
            total={total}
            rangeStart={rangeStart}
            rangeEnd={rangeEnd}
            setPage={setPage}
            setPageSize={setPageSize}
          />
        </>
      )}
    </div>
  );
}
