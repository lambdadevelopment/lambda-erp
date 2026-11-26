// What happened to each line of a submitted sales or purchase order: ordered,
// delivered (received), billed, closed and still open. Lines with something
// open can be closed with a reason when the customer cancels the rest or the
// supplier will never send it (close_order_lines); a closing can be undone
// (reopen_order_lines). See docs/order-progress-and-backlog.md.
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/api/client";
import { formatNumber } from "@/lib/utils";

interface OrderLine {
  name: string;
  idx?: number;
  item_code?: string;
  item_name?: string;
  qty?: number;
  delivered_qty?: number;
  received_qty?: number;
  billed_qty?: number;
  closed_qty?: number;
  close_reason?: string | null;
}

const ORDER_TYPES: Record<string, { doctype: string; progress: "delivered_qty" | "received_qty" }> = {
  "sales-order": { doctype: "Sales Order", progress: "delivered_qty" },
  "purchase-order": { doctype: "Purchase Order", progress: "received_qty" },
};

const n = (value: unknown) => Number(value ?? 0) || 0;
const fmt = (value: number) => formatNumber(value, Number.isInteger(value) ? 0 : 2);

export function OrderLineProgress({ slug, name, lines }: { slug: string; name: string; lines: OrderLine[] }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const kind = ORDER_TYPES[slug];
  const [selected, setSelected] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["document", slug, name] });
    queryClient.invalidateQueries({ queryKey: ["documents", slug] });
    queryClient.invalidateQueries({ queryKey: ["report"] });
  };
  const fail = (err: unknown) => setError(err instanceof ApiError ? err.message : String(err));
  const close = useMutation({
    mutationFn: () => api.runAction("close_order_lines", {
      doctype: kind.doctype, order: name, lines: selected, reason: reason.trim(),
    }),
    onSuccess: () => { setSelected([]); setReason(""); setError(null); refresh(); },
    onError: fail,
  });
  const reopen = useMutation({
    mutationFn: (line: string) => api.runAction("reopen_order_lines", { doctype: kind.doctype, order: name, lines: [line] }),
    onSuccess: () => { setError(null); refresh(); },
    onError: fail,
  });
  if (!kind) return null;

  const rows = lines.map((line) => {
    const ordered = n(line.qty);
    const done = n(line[kind.progress]);
    const billed = n(line.billed_qty);
    const closed = n(line.closed_qty);
    const open = Math.max(0, ordered - closed - done);
    const closable = Math.max(0, ordered - closed - Math.max(done, billed));
    return { line, ordered, done, billed, closed, open, closable };
  });
  if (!rows.length) return null;
  const toggle = (line: string) =>
    setSelected((current) => (current.includes(line) ? current.filter((x) => x !== line) : [...current, line]));
  const doneLabel = kind.progress === "delivered_qty"
    ? t("orderProgress.delivered", { defaultValue: "Delivered" })
    : t("orderProgress.received", { defaultValue: "Received" });

  return (
    <Card title={t("orderProgress.title", { defaultValue: "Quantities per line" })}>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-fg-muted">
              <th className="w-8 px-2 py-2" />
              <th className="px-2 py-2">#</th>
              <th className="px-2 py-2">{t("orderProgress.item", { defaultValue: "Item" })}</th>
              <th className="px-2 py-2 text-right">{t("orderProgress.ordered", { defaultValue: "Ordered" })}</th>
              <th className="px-2 py-2 text-right">{doneLabel}</th>
              <th className="px-2 py-2 text-right">{t("orderProgress.billed", { defaultValue: "Billed" })}</th>
              <th className="px-2 py-2 text-right">{t("orderProgress.closed", { defaultValue: "Closed" })}</th>
              <th className="px-2 py-2 text-right">{t("orderProgress.open", { defaultValue: "Open" })}</th>
              <th className="px-2 py-2" />
            </tr>
          </thead>
          <tbody>
            {rows.map(({ line, ordered, done, billed, closed, open, closable }) => (
              <tr key={line.name} className="border-b border-line/60 last:border-0">
                <td className="px-2 py-2">
                  {closable > 0 && (
                    <input
                      type="checkbox"
                      aria-label={t("orderProgress.select", { defaultValue: "Select line" })}
                      checked={selected.includes(line.name)}
                      onChange={() => toggle(line.name)}
                    />
                  )}
                </td>
                <td className="px-2 py-2 text-fg-muted">{line.idx}</td>
                <td className="px-2 py-2">
                  <div className="font-medium">{line.item_code}</div>
                  {line.item_name && line.item_name !== line.item_code && (
                    <div className="text-xs text-fg-muted">{line.item_name}</div>
                  )}
                  {closed > 0 && line.close_reason && (
                    <div className="text-xs text-fg-muted">
                      {t("orderProgress.closedBecause", { defaultValue: "Closed:" })} {line.close_reason}
                    </div>
                  )}
                </td>
                <td className="px-2 py-2 text-right">{fmt(ordered)}</td>
                <td className="px-2 py-2 text-right">{fmt(done)}</td>
                <td className="px-2 py-2 text-right">{fmt(billed)}</td>
                <td className="px-2 py-2 text-right">{closed > 0 ? fmt(closed) : "–"}</td>
                <td className={`px-2 py-2 text-right ${open > 0 ? "font-semibold" : "text-fg-muted"}`}>{fmt(open)}</td>
                <td className="px-2 py-2 text-right">
                  {closed > 0 && (
                    <Button variant="ghost" disabled={reopen.isPending} onClick={() => reopen.mutate(line.name)}>
                      {t("orderProgress.reopen", { defaultValue: "Reopen" })}
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selected.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <input
            className="min-w-[16rem] flex-1 rounded-lg bg-surface px-3 py-2 text-sm text-fg ring-1 ring-line focus:outline-none focus:ring-2 focus:ring-brand/30"
            placeholder={t("orderProgress.reasonPlaceholder", {
              defaultValue: "Reason, e.g. customer no longer needs the rest",
            })}
            value={reason}
            maxLength={500}
            onChange={(event) => setReason(event.target.value)}
          />
          <Button variant="danger" disabled={!reason.trim() || close.isPending} onClick={() => close.mutate()}>
            {t("orderProgress.closeOpen", { defaultValue: "Close open quantity", count: selected.length })}
          </Button>
        </div>
      )}
      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
    </Card>
  );
}
