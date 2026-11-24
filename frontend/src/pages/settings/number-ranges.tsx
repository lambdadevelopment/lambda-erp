// Number ranges: which number the next document of each type gets, and (for
// admins) where a range continues, e.g. invoices from SINV-0321 on. A range can
// move up freely and back down only as far as the highest number already used
// (api/number_series.py). The chat, REST and MCP clients reach the same actions.
// Since 1.4.30.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "@/api/client";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/contexts/auth-context";

interface NumberSeries {
  doctype: string;
  doctype_name: string;
  prefix: string;
  last_number: number;
  next_number: number;
  next_name: string;
  highest_used: number;
  lowest_allowed_next: number;
}

const KEY = ["number-series"];

function formatName(prefix: string, number: number) {
  return `${prefix}-${String(number).padStart(4, "0")}`;
}

function SeriesRow({ row, canEdit }: { row: NumberSeries; canEdit: boolean }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [value, setValue] = useState(String(row.next_number));
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const number = Number(value);
  const valid = Number.isInteger(number) && number >= row.lowest_allowed_next;
  const changed = valid && number !== row.next_number;
  const save = useMutation({
    mutationFn: () => api.runAction("set_next_number", { doctype: row.doctype, next_number: number }),
    onSuccess: (fresh: NumberSeries) => {
      setError(null);
      setSaved(true);
      setValue(String(fresh.next_number));
      qc.invalidateQueries({ queryKey: KEY });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
  });
  const typeName = t(`doctypes.${row.doctype}.other`, { defaultValue: row.doctype_name });
  return (
    <tr className="border-t border-line align-top">
      <td className="py-2 pr-4 text-sm text-fg">{typeName}</td>
      <td className="py-2 pr-4 font-mono text-xs text-fg-muted">{row.prefix}</td>
      <td className="py-2 pr-4 font-mono text-xs text-fg-muted">
        {row.highest_used > 0 ? formatName(row.prefix, row.highest_used) : "–"}
      </td>
      <td className="py-2">
        {canEdit ? (
          <form
            className="flex flex-wrap items-center gap-2"
            onSubmit={(e) => { e.preventDefault(); if (changed) save.mutate(); }}
          >
            <input
              type="number"
              inputMode="numeric"
              min={row.lowest_allowed_next}
              step={1}
              value={value}
              aria-label={t("numberRanges.nextFor", { type: typeName })}
              onChange={(e) => { setValue(e.target.value); setSaved(false); setError(null); }}
              className="w-28 rounded-md border border-line bg-surface px-2 py-1 text-sm text-fg focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/20"
            />
            <span className="font-mono text-xs text-fg-muted">
              {valid ? formatName(row.prefix, number) : t("numberRanges.atLeast", { number: row.lowest_allowed_next })}
            </span>
            {changed && (
              <Button size="sm" type="submit" disabled={save.isPending}>
                {t("common.save")}
              </Button>
            )}
            {saved && !changed && <span className="text-xs text-emerald-600">{t("common.saved")}</span>}
            {error && <span className="w-full text-xs text-rose-600">{error}</span>}
          </form>
        ) : (
          <span className="font-mono text-xs text-fg">{row.next_name}</span>
        )}
      </td>
    </tr>
  );
}

export default function NumberRanges() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const canEdit = user?.role === "admin";
  const { data, isLoading, error } = useQuery({
    queryKey: KEY,
    queryFn: () => api.runAction("get_number_series", {}) as Promise<{ series: NumberSeries[] }>,
  });
  return (
    <div className="max-w-4xl space-y-4">
      <p className="text-sm text-fg-muted">{t("numberRanges.intro")}</p>
      <p className="text-sm text-fg-muted">{t("numberRanges.chatHint")}</p>
      <Card>
        {isLoading && <p className="text-sm text-fg-muted">{t("common.loading", { defaultValue: "Loading…" })}</p>}
        {error && <p className="text-sm text-rose-600">{String((error as Error).message)}</p>}
        {data && (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="text-xs uppercase tracking-wide text-fg-muted">
                  <th className="pb-2 pr-4 font-medium">{t("numberRanges.type")}</th>
                  <th className="pb-2 pr-4 font-medium">{t("numberRanges.prefix")}</th>
                  <th className="pb-2 pr-4 font-medium">{t("numberRanges.highest")}</th>
                  <th className="pb-2 font-medium">{t("numberRanges.next")}</th>
                </tr>
              </thead>
              <tbody>
                {data.series.map((row) => (
                  <SeriesRow key={`${row.doctype}:${row.next_number}`} row={row} canEdit={canEdit} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
