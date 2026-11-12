import { useId, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/api/client";
import { Input } from "@/components/ui/input";
import { formatNumber } from "@/lib/utils";

interface Option {
  value: string;
  label: string;
}

/** A text input for a line's batch or storage location that proposes what
 *  exists: the batches of the line's item in its warehouse, earliest expiry
 *  first with the quantity there, or the warehouse's storage locations with
 *  how much of the item lies in each. Typing something else stays possible,
 *  because a receipt names a batch that is new. */
export function StockSuggestInput({
  kind,
  value,
  onChange,
  itemCode,
  warehouse,
  batchNo,
}: {
  kind: "batch" | "location";
  value: string;
  onChange: (value: string) => void;
  itemCode?: string;
  warehouse?: string;
  batchNo?: string;
}) {
  const { t } = useTranslation();
  const listId = useId();

  const { data: stock } = useQuery({
    queryKey: ["stock-suggest", itemCode, warehouse],
    queryFn: () => api.stockDetail({ item_code: itemCode as string, ...(warehouse ? { warehouse } : {}) }),
    enabled: !!itemCode,
    staleTime: 30_000,
  });
  const { data: locations } = useQuery({
    queryKey: ["stock-suggest-locations", warehouse],
    queryFn: () => api.listMasters("storage-location", { warehouse, limit: 500 }),
    enabled: kind === "location" && !!warehouse,
    staleTime: 60_000,
  });

  const options = useMemo<Option[]>(() => {
    const rows: any[] = stock?.rows ?? [];
    if (kind === "batch") {
      const byBatch = new Map<string, { qty: number; expiry: string | null; warehouses: Set<string> }>();
      for (const row of rows) {
        if (!row.batch_no) continue;
        const entry = byBatch.get(row.batch_no) ?? { qty: 0, expiry: row.expiry_date ?? null, warehouses: new Set() };
        entry.qty += row.actual_qty ?? 0;
        entry.warehouses.add(row.warehouse);
        byBatch.set(row.batch_no, entry);
      }
      return [...byBatch.entries()]
        .sort(([a, x], [b, y]) =>
          (x.expiry ?? "9999").localeCompare(y.expiry ?? "9999") || a.localeCompare(b))
        .map(([batch, entry]) => ({
          value: batch,
          label: [
            entry.expiry ? `${t("fields.Expiry Date", { defaultValue: "Expiry Date" })} ${entry.expiry}` : null,
            `${formatNumber(entry.qty)}`,
            warehouse ? null : [...entry.warehouses].join(", "),
          ].filter(Boolean).join(" · "),
        }));
    }
    const here = new Map<string, number>();
    for (const row of rows) {
      if (!row.location) continue;
      if (batchNo && row.batch_no !== batchNo) continue;
      here.set(row.location, (here.get(row.location) ?? 0) + (row.actual_qty ?? 0));
    }
    const known: string[] = ((locations as any)?.rows ?? [])
      .filter((row: any) => !row.disabled || here.has(row.location))
      .map((row: any) => row.location);
    const all = [...new Set([...known, ...here.keys()])].sort((a, b) => a.localeCompare(b));
    return all.map((location) => ({
      value: location,
      label: here.has(location) ? formatNumber(here.get(location) as number) : "",
    }));
  }, [kind, stock, locations, warehouse, batchNo, t]);

  return (
    <>
      <Input
        type="text"
        value={value ?? ""}
        list={options.length ? listId : undefined}
        autoComplete="off"
        onChange={(e) => onChange(e.target.value)}
      />
      {options.length > 0 && (
        <datalist id={listId}>
          {options.map((option) => (
            <option key={option.value} value={option.value} label={option.label}>
              {option.label}
            </option>
          ))}
        </datalist>
      )}
    </>
  );
}
