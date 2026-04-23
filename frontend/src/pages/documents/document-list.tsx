import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
  type ColumnDef,
} from "@tanstack/react-table";
import { useDocumentList } from "@/hooks/use-document-list";
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

export default function DocumentListPage() {
  const { doctype } = useParams<{ doctype: string }>();
  const config = getDoctypeConfig(doctype ?? "");

  // All user-facing filter state lives in the URL. The param names are the
  // short human-readable form (`from` / `to` / `per_page`); the backend still
  // wants `from_date` / `to_date` / `limit` / `offset`, translated below.
  const [status] = useUrlState<string>("status", "All");
  const [fromDate] = useUrlState<string>("from", "");
  const [toDate] = useUrlState<string>("to", "");
  const [pageSize] = useUrlState<number>("per_page", 50);
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
  const setPageSize = (n: number) => patchUrl({ per_page: n, page: null });

  const filters = useMemo(() => {
    const f: Record<string, string | number | undefined> = {};
    if (status !== "All") f.status = status;
    if (fromDate) f.from_date = fromDate;
    if (toDate) f.to_date = toDate;
    f.limit = pageSize;
    f.offset = page * pageSize;
    return f;
  }, [status, fromDate, toDate, pageSize, page]);

  const { data, isLoading } = useDocumentList(doctype ?? "", filters);
  const rows = data?.rows ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const rangeStart = total === 0 ? 0 : page * pageSize + 1;
  const rangeEnd = Math.min(total, (page + 1) * pageSize);

  const columns = useMemo<ColumnDef<any, any>[]>(() => {
    if (!config) return [];
    const helper = createColumnHelper<any>();

    return config.listColumns.map((col) => {
      if (col === "name") {
        return helper.accessor("name", {
          header: "Name",
          cell: (info) => (
            <Link
              to={`/app/${config.slug}/${info.getValue()}`}
              className="font-medium text-blue-600 hover:text-blue-800"
            >
              {info.getValue()}
            </Link>
          ),
        });
      }

      if (col === "status") {
        return helper.accessor("status", {
          header: "Status",
          cell: (info) => <StatusBadge status={info.getValue()} />,
        });
      }

      if (CURRENCY_FIELDS.has(col)) {
        return helper.accessor(col, {
          header: col
            .split("_")
            .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
            .join(" "),
          cell: (info) => formatCurrency(info.getValue()),
        });
      }

      if (col === "party") {
        return helper.accessor("party", {
          header: "Party",
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
                className="text-sky-600 hover:text-sky-800 hover:underline"
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
          header: col
            .split("_")
            .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
            .join(" "),
          cell: (info) => {
            const value = info.getValue();
            if (!value) return "-";
            const href = linkRefHref(masterType, String(value));
            if (!href) return String(value);
            return (
              <Link
                to={href}
                onClick={(e) => e.stopPropagation()}
                className="text-sky-600 hover:text-sky-800 hover:underline"
              >
                {value}
              </Link>
            );
          },
        });
      }

      if (DATE_FIELDS.has(col)) {
        return helper.accessor(col, {
          header: col
            .split("_")
            .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
            .join(" "),
          cell: (info) => formatDate(info.getValue()),
        });
      }

      return helper.accessor(col, {
        header: col
          .split("_")
          .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
          .join(" "),
      });
    });
  }, [config]);

  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  if (!config) {
    return (
      <p className="text-gray-500">
        Unknown document type: {doctype}
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-end">
        <Link to={`/app/${config.slug}/new`}>
          <Button>New</Button>
        </Link>
      </div>

      <div className="flex flex-wrap items-end gap-4">
        <Select
          label="Status"
          options={STATUS_OPTIONS}
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        />
        <Input
          label="From Date"
          type="date"
          value={fromDate}
          onChange={(e) => setFromDate(e.target.value)}
        />
        <Input
          label="To Date"
          type="date"
          value={toDate}
          onChange={(e) => setToDate(e.target.value)}
        />
      </div>
      <DateRangePresets onSelect={(from, to) => patchUrl({ from, to, page: null })} />

      {isLoading ? (
        <p className="text-gray-500">Loading...</p>
      ) : rows.length === 0 ? (
        <p className="py-8 text-center text-gray-400">No documents found</p>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50">
                {table.getHeaderGroups().map((headerGroup) => (
                  <tr key={headerGroup.id}>
                    {headerGroup.headers.map((header) => (
                      <th
                        key={header.id}
                        className="px-4 py-3 text-left font-medium text-gray-500"
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
              <tbody className="divide-y divide-gray-100">
                {table.getRowModel().rows.map((row) => (
                  <tr key={row.id} className="hover:bg-gray-50">
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="px-4 py-2">
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

          <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-gray-600">
            <div>
              Showing <span className="font-medium text-gray-900">{rangeStart}–{rangeEnd}</span>{" "}
              of <span className="font-medium text-gray-900">{total}</span>
            </div>
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1.5">
                <span className="text-xs text-gray-500">Per page</span>
                <select
                  value={pageSize}
                  onChange={(e) => setPageSize(Number(e.target.value))}
                  className="rounded border border-gray-300 bg-white px-2 py-1 text-sm"
                >
                  {PAGE_SIZE_OPTIONS.map((n) => (
                    <option key={n} value={n}>{n}</option>
                  ))}
                </select>
              </label>
              <button
                onClick={() => setPage(Math.max(0, page - 1))}
                disabled={page === 0}
                className="rounded border border-gray-300 bg-white px-3 py-1 text-sm text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Prev
              </button>
              <span className="text-xs">
                Page <span className="font-medium text-gray-900">{page + 1}</span> of{" "}
                <span className="font-medium text-gray-900">{totalPages}</span>
              </span>
              <button
                onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
                disabled={page >= totalPages - 1}
                className="rounded border border-gray-300 bg-white px-3 py-1 text-sm text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
