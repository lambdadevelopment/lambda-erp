import { useMemo } from "react";
import { Link, useParams, useNavigate, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useUrlState, useUrlPatch } from "@/hooks/use-url-state";
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
  type ColumnDef,
} from "@tanstack/react-table";
import { api } from "@/api/client";
import { Button } from "@/components/ui/button";

const TYPE_LABELS: Record<string, string> = {
  customer: "Customer",
  supplier: "Supplier",
  item: "Item",
  warehouse: "Warehouse",
  company: "Company",
};

const PAGE_SIZE_OPTIONS = ["25", "50", "100", "200"];

export default function MasterListPage() {
  const { type } = useParams<{ type: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const label = TYPE_LABELS[type ?? ""] ?? type ?? "";
  const notice = (location.state as { notice?: string } | null)?.notice;

  const [pageSize] = useUrlState<number>("per_page", 50);
  // URL page is 1-indexed; internal 0-indexed for offset math.
  const [urlPage] = useUrlState<number>("page", 1);
  const page = urlPage - 1;
  const patchUrl = useUrlPatch();
  const setPage = (p: number) => patchUrl({ page: p === 0 ? null : p + 1 });
  const setPageSize = (n: number) => patchUrl({ per_page: n, page: null });

  const { data, isLoading } = useQuery({
    queryKey: ["masters", type, page, pageSize],
    queryFn: () => api.listMasters(type!, {
      include_disabled: 1,
      limit: pageSize,
      offset: page * pageSize,
    }),
    enabled: !!type,
  });

  const rows = data?.rows ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const rangeStart = total === 0 ? 0 : page * pageSize + 1;
  const rangeEnd = Math.min(total, (page + 1) * pageSize);

  const columns = useMemo<ColumnDef<any, any>[]>(() => {
    if (!rows || rows.length === 0) return [];
    const helper = createColumnHelper<any>();
    const keys = Object.keys(rows[0]);

    return keys.map((key) =>
      helper.accessor(key, {
        header: key
          .split("_")
          .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
          .join(" "),
        cell: (info) => {
          const val = info.getValue();
          if (key === "name") {
            const isDisabled = info.row.original.disabled === 1;
            return (
              <Link
                to={`/masters/${type}/${val}`}
                className={isDisabled ? "font-medium text-gray-500" : "font-medium text-blue-600 hover:text-blue-800"}
              >
                {val}
              </Link>
            );
          }
          if (key === "disabled") {
            return val === 1 ? "Disabled" : "Active";
          }
          return val ?? "-";
        },
      }),
    );
  }, [rows, type]);

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
      <div className="flex items-center justify-end">
        <Link to={`/masters/${type}/new`}>
          <Button>New</Button>
        </Link>
      </div>

      {isLoading ? (
        <p className="text-gray-500">Loading...</p>
      ) : rows.length === 0 ? (
        <p className="py-8 text-center text-gray-400">No records found</p>
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
                  <tr
                    key={row.id}
                    className={row.original.disabled === 1 ? "cursor-pointer bg-gray-50 text-gray-500 hover:bg-gray-100" : "cursor-pointer hover:bg-gray-50"}
                    onClick={() =>
                      navigate(`/masters/${type}/${row.original.name}`)
                    }
                  >
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
