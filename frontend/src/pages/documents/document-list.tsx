import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useLocation } from "react-router-dom";
import { setListContext } from "@/lib/doc-list-context";
import { useTranslation } from "react-i18next";
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
  type ColumnDef,
} from "@tanstack/react-table";
import { useDocumentList } from "@/hooks/use-document-list";
import { useBaseCurrency } from "@/hooks/use-base-currency";
import { useUrlState, useUrlPatch } from "@/hooks/use-url-state";
import { getDoctypeConfig } from "@/lib/doctypes";
import { linkRefHref } from "@/pages/documents/document-form";
import { StatusBadge } from "@/components/document/status-badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { DateRangePresets } from "@/components/ui/date-range-presets";
import { formatCurrency, formatDate } from "@/lib/utils";

const STATUS_OPTIONS = ["All", "Draft", "Submitted", "Cancelled"];

const CURRENCY_FIELDS = new Set([
  "grand_total",
  "outstanding_amount",
  "paid_amount",
  "total_debit",
  "total_amount",
  "net_total",
]);

const DATE_FIELDS = new Set([
  "transaction_date",
  "posting_date",
  "due_date",
  "delivery_date",
  "valid_till",
  "date",
]);

// Columns that reference a master record — rendered as clickable links to the master page.
const MASTER_REF_FIELDS: Record<string, string> = {
  customer: "customer",
  supplier: "supplier",
  item_code: "item",
  item: "item",
  warehouse: "warehouse",
  company: "company",
  account: "account",
  cost_center: "cost-center",
};

// `party` references different master types depending on `party_type` in the row.
function partyMasterType(partyType: unknown): string | null {
  if (typeof partyType !== "string") return null;
  const t = partyType.toLowerCase();
  if (t === "customer") return "customer";
  if (t === "supplier") return "supplier";
  return null;
}

const PAGE_SIZE_OPTIONS = ["25", "50", "100", "200"];

// Pagination controls, rendered both above and below the table so long lists
// don't force a scroll to the bottom to page. The current page is a typeable
// field (commit on Enter/blur, clamped to 1..totalPages).
function ListPager({
  page,
  totalPages,
  pageSize,
  total,
  rangeStart,
  rangeEnd,
  setPage,
  setPageSize,
}: {
  page: number;
  totalPages: number;
  pageSize: number;
  total: number;
  rangeStart: number;
  rangeEnd: number;
  setPage: (p: number) => void;
  setPageSize: (n: number) => void;
}) {
  const { t } = useTranslation();
  const [pageInput, setPageInput] = useState(String(page + 1));
  useEffect(() => setPageInput(String(page + 1)), [page]);

  const commit = () => {
    const n = parseInt(pageInput, 10);
    if (!isNaN(n)) setPage(Math.min(totalPages, Math.max(1, n)) - 1);
    else setPageInput(String(page + 1));
  };

  return (
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
        <span className="flex items-center gap-1 text-xs">
          {t("common.page")}
          <input
            aria-label={t("common.page")}
            inputMode="numeric"
            value={pageInput}
            onChange={(e) => setPageInput(e.target.value.replace(/[^0-9]/g, ""))}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
            }}
            className="h-7 w-12 rounded-md bg-surface px-1 text-center text-sm font-medium text-fg ring-1 ring-line focus:outline-none focus:ring-2 focus:ring-brand/30"
          />
          {t("common.of")} <span className="font-medium text-fg">{totalPages}</span>
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
  );
}

export default function DocumentListPage() {
  const { t } = useTranslation();
  // Rows without a per-document currency (Stock Entry, …) hold base-currency
  // values — format them with the company currency, not a hardcoded USD.
  const baseCurrency = useBaseCurrency();
  const { doctype } = useParams<{ doctype: string }>();
  const config = getDoctypeConfig(doctype ?? "");
  const location = useLocation();

  // Config-driven filter dropdowns (opt-in via config.listFilters). Their values
  // live in the URL like every other filter; read them generically since the
  // field names are dynamic. Each names a select field whose options drive the
  // dropdown; the selection is sent as a plain column filter.
  const configFilters = config?.listFilters ?? [];
  const filterValues = useMemo(() => {
    const params = new URLSearchParams(location.search);
    const out: Record<string, string> = {};
    for (const f of configFilters) out[f] = params.get(f) ?? "";
    return out;
  }, [location.search, configFilters]);

  // All user-facing filter state lives in the URL. The param names are the
  // short human-readable form (`from` / `to` / `per_page`); the backend still
  // wants `from_date` / `to_date` / `limit` / `offset`, translated below.
  const [status] = useUrlState<string>("status", "All");
  const [fromDate] = useUrlState<string>("from", "");
  const [toDate] = useUrlState<string>("to", "");
  const [showDiscarded] = useUrlState<string>("discarded", "");
  const [pageSize] = useUrlState<number>("per_page", 50);
  // Free-text search (opt-in via config.searchFields). The committed query lives
  // in the URL (`q`); a local input debounces into it so we don't refetch on
  // every keystroke.
  const searchFields = config?.searchFields ?? [];
  const [urlQ] = useUrlState<string>("q", "");
  const [searchInput, setSearchInput] = useState(urlQ);
  // URL is human-friendly 1-indexed; internal state stays 0-indexed for
  // offset calculation. setPage accepts the 0-indexed value.
  const [urlPage] = useUrlState<number>("page", 1);
  const page = urlPage - 1;
  const patchUrl = useUrlPatch();

  // All writes go through one atomic patch so multiple params update together
  // — calling two individual setters in the same handler races (react-router
  // caches the previous searchParams, so the last call wins).
  const setPage = (p: number) => patchUrl({ page: p === 0 ? null : p + 1 });
  const setStatus = (s: string) => patchUrl({ status: s === "All" ? null : s, page: null });
  const setFromDate = (s: string) => patchUrl({ from: s || null, page: null });
  const setToDate = (s: string) => patchUrl({ to: s || null, page: null });
  const setShowDiscarded = (on: boolean) => patchUrl({ discarded: on ? "1" : null, page: null });
  const setPageSize = (n: number) => patchUrl({ per_page: n, page: null });

  // Keep the box in sync when `q` changes from outside typing (back/forward, a
  // cleared filter), then debounce local edits back into the URL after a pause.
  useEffect(() => { setSearchInput(urlQ); }, [urlQ]);
  useEffect(() => {
    if (searchInput === urlQ) return; // idle — nothing to commit, no timer
    const t = setTimeout(() => patchUrl({ q: searchInput || null, page: null }), 300);
    return () => clearTimeout(t);
  }, [searchInput, urlQ]); // eslint-disable-line react-hooks/exhaustive-deps

  const filters = useMemo(() => {
    const f: Record<string, string | number | undefined> = {};
    // The default status dropdown is hidden when a doctype opts into its own
    // filters, so only apply it in that default case.
    if (configFilters.length === 0 && status !== "All") f.status = status;
    if (fromDate) f.from_date = fromDate;
    if (toDate) f.to_date = toDate;
    // Tell the backend which column the date range filters on. Without this the
    // server only knows the built-in core doctypes; plugin doctypes' date
    // pickers would be silently ignored.
    if (config?.dateField) f.date_field = config.dateField;
    for (const key of configFilters) {
      if (filterValues[key]) f[key] = filterValues[key];
    }
    // Free-text search across the config's searchFields (the committed URL `q`).
    if (searchFields.length > 0 && urlQ) {
      f.search = urlQ;
      f.search_fields = searchFields.join(",");
    }
    if (showDiscarded) f.include_discarded = "true";
    f.limit = pageSize;
    f.offset = page * pageSize;
    return f;
  }, [status, fromDate, toDate, showDiscarded, pageSize, page, config?.dateField, configFilters, filterValues, searchFields, urlQ]);

  const { data, isLoading } = useDocumentList(doctype ?? "", filters);
  const rows = data?.rows ?? [];
  const total = data?.total ?? 0;

  // Remember this list's filters + URL so a detail page's prev/next (DocPager)
  // follows it, and "back to list" returns here. Pagination is dropped — it's
  // not a record filter.
  useEffect(() => {
    const { limit, offset, ...ctxFilters } = filters;
    setListContext(doctype ?? "", { filters: ctxFilters, search: location.search });
  }, [doctype, filters, location.search]);
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const rangeStart = total === 0 ? 0 : page * pageSize + 1;
  const rangeEnd = Math.min(total, (page + 1) * pageSize);

  const columns = useMemo<ColumnDef<any, any>[]>(() => {
    if (!config) return [];
    const helper = createColumnHelper<any>();
    // Title-case a column slug, then translate it (English title-case is the
    // fallback when a field has no translation yet).
    const headerLabel = (c: string) => {
      const titleCased = c
        .split("_")
        .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
        .join(" ");
      return t(`fields.${titleCased}`, { defaultValue: titleCased });
    };

    return config.listColumns.map((col) => {
      if (col === "name") {
        return helper.accessor("name", {
          header: t("fields.Name", { defaultValue: "Name" }),
          cell: (info) => (
            <Link
              to={`/app/${config.slug}/${info.getValue()}`}
              className="font-medium text-brand transition-colors hover:text-brand/80"
            >
              {info.getValue()}
            </Link>
          ),
        });
      }

      if (col === "status") {
        return helper.accessor("status", {
          header: t("fields.Status", { defaultValue: "Status" }),
          cell: (info) => <StatusBadge status={info.getValue()} />,
        });
      }

      if (CURRENCY_FIELDS.has(col)) {
        return helper.accessor(col, {
          header: headerLabel(col),
          cell: (info) => formatCurrency(info.getValue(), (info.row.original as any)?.currency || baseCurrency),
        });
      }

      if (col === "party") {
        return helper.accessor("party", {
          header: t("fields.Party", { defaultValue: "Party" }),
          cell: (info) => {
            const value = info.getValue();
            if (!value) return "-";
            const masterType = partyMasterType(info.row.original.party_type);
            const href = masterType ? linkRefHref(masterType, String(value)) : null;
            if (!href) return String(value);
            return (
              <Link
                to={href}
                onClick={(e) => e.stopPropagation()}
                className="text-brand transition-colors hover:text-brand/80 hover:underline"
              >
                {value}
              </Link>
            );
          },
        });
      }

      if (MASTER_REF_FIELDS[col]) {
        const masterType = MASTER_REF_FIELDS[col];
        return helper.accessor(col, {
          header: headerLabel(col),
          cell: (info) => {
            const value = info.getValue();
            if (!value) return "-";
            const href = linkRefHref(masterType, String(value));
            if (!href) return String(value);
            return (
              <Link
                to={href}
                onClick={(e) => e.stopPropagation()}
                className="text-brand transition-colors hover:text-brand/80 hover:underline"
              >
                {value}
              </Link>
            );
          },
        });
      }

      if (DATE_FIELDS.has(col)) {
        return helper.accessor(col, {
          header: headerLabel(col),
          cell: (info) => formatDate(info.getValue()),
        });
      }

      return helper.accessor(col, {
        header: headerLabel(col),
      });
    });
  }, [config, t, baseCurrency]);

  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  if (!config) {
    return (
      <p className="text-fg-muted">
        Unknown document type: {doctype}
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-end">
        <Link to={`/app/${config.slug}/new`}>
          <Button>{t("common.new")}</Button>
        </Link>
      </div>

      <div className="flex flex-wrap items-end gap-4">
        {searchFields.length > 0 && (
          <Input
            label={t("common.search", { defaultValue: "Search" })}
            type="search"
            placeholder={t("common.searchPlaceholder", { defaultValue: "Search…" })}
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            className="min-w-[16rem]"
          />
        )}
        {configFilters.length === 0 ? (
          <Select
            label={t("fields.Status", { defaultValue: "Status" })}
            options={STATUS_OPTIONS.map((s) => ({
              value: s,
              label: s === "All" ? t("common.all") : t(`status.${s}`, { defaultValue: s }),
            }))}
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          />
        ) : (
          configFilters.map((key) => {
            const field = config.fields.find((f) => f.name === key);
            const opts = (field?.options ?? []).map((o) =>
              typeof o === "string" ? { value: o, label: o } : o,
            );
            return (
              <Select
                key={key}
                label={t(`fields.${field?.label ?? key}`, { defaultValue: field?.label ?? key })}
                options={[{ value: "", label: t("common.all") }, ...opts]}
                value={filterValues[key]}
                onChange={(e) => patchUrl({ [key]: e.target.value || null, page: null })}
              />
            );
          })
        )}
        <Input
          label={t("fields.From Date", { defaultValue: "From Date" })}
          type="date"
          value={fromDate}
          onChange={(e) => setFromDate(e.target.value)}
        />
        <Input
          label={t("fields.To Date", { defaultValue: "To Date" })}
          type="date"
          value={toDate}
          onChange={(e) => setToDate(e.target.value)}
        />
        <label className="flex h-10 items-center gap-2 text-sm text-fg-muted">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-line text-brand focus:ring-brand/30"
            checked={!!showDiscarded}
            onChange={(e) => setShowDiscarded(e.target.checked)}
          />
          {t("common.showDiscarded")}
        </label>
      </div>
      <DateRangePresets onSelect={(from, to) => patchUrl({ from, to, page: null })} />

      {isLoading ? (
        <p className="text-fg-muted">{t("common.loading")}</p>
      ) : rows.length === 0 ? (
        <p className="py-8 text-center text-fg-muted">{t("reports.noDocuments")}</p>
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
                  <tr key={row.id} className="transition-colors hover:bg-surface-subtle">
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="px-4 py-2.5 text-fg">
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
