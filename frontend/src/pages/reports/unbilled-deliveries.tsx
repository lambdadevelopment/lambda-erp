import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { usePageTitle } from "@/lib/use-page-title";
import { useUrlPatch, useUrlState } from "@/hooks/use-url-state";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { LinkField } from "@/components/document/link-field";
import { api, ApiError } from "@/api/client";
import { formatNumber } from "@/lib/utils";

interface UnbilledLine {
  delivery_note: string;
  dn_detail: string;
  posting_date: string;
  customer: string;
  customer_name?: string;
  item_code: string;
  item_name?: string;
  qty: number;
  billed_qty: number;
  unbilled_qty: number;
  rate: number;
  unbilled_amount: number;
  sales_order?: string | null;
}

const inputClass =
  "rounded-lg bg-surface px-3 py-2 text-sm text-fg ring-1 ring-line focus:outline-none focus:ring-2 focus:ring-brand/30";

/** Delivered and not yet invoiced, per delivery note line. Pick a customer
 *  (and a period), then bill the selected lines on ONE invoice: the monthly /
 *  collective invoice. Creating it makes a draft; nothing is posted yet. */
export function UnbilledDeliveriesPage() {
  const { t } = useTranslation();
  usePageTitle(t("titles.unbilled-deliveries", { defaultValue: "Unbilled Deliveries" }));
  const navigate = useNavigate();
  const [urlCustomer] = useUrlState<string>("customer", "");
  const [urlFrom] = useUrlState<string>("from_date", "");
  const [urlTo] = useUrlState<string>("to_date", "");
  const patchUrl = useUrlPatch();
  const [customer, setCustomer] = useState(urlCustomer);
  const [fromDate, setFromDate] = useState(urlFrom);
  const [toDate, setToDate] = useState(urlTo);
  const [excluded, setExcluded] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const filters = useMemo(() => {
    const f: Record<string, string> = {};
    if (urlCustomer) f.customer = urlCustomer;
    if (urlFrom) f.from_date = urlFrom;
    if (urlTo) f.to_date = urlTo;
    return f;
  }, [urlCustomer, urlFrom, urlTo]);
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["report", "unbilled-deliveries", filters],
    queryFn: () => api.unbilledDeliveries(filters) as Promise<{ rows: UnbilledLine[] }>,
  });
  const rows = data?.rows ?? [];
  const apply = () => {
    setExcluded([]);
    patchUrl({ customer: customer || null, from_date: fromDate || null, to_date: toDate || null });
    refetch();
  };

  const chosen = rows.filter((row) => !excluded.includes(row.dn_detail));
  const customers = new Set(chosen.map((row) => row.customer));
  const total = chosen.reduce((sum, row) => sum + Number(row.unbilled_amount || 0), 0);
  const create = useMutation({
    mutationFn: () => api.runAction("prepare_collective_invoice", {
      customer: urlCustomer, lines: chosen.map((row) => row.dn_detail),
    }),
    onSuccess: (doc: any) => navigate(`/app/sales-invoice/${encodeURIComponent(doc.name)}`),
    onError: (err) => setError(err instanceof ApiError ? err.message : String(err)),
  });
  const toggle = (line: string) =>
    setExcluded((current) => (current.includes(line) ? current.filter((x) => x !== line) : [...current, line]));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-4">
        <LinkField label={t("fields.Customer", { defaultValue: "Customer" })} value={customer} onChange={setCustomer}
          linkDoctype="customer" readOnly={false} />
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-fg-muted">{t("reports.fromDate", { defaultValue: "From" })}</span>
          <input type="date" className={inputClass} value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-fg-muted">{t("reports.toDate", { defaultValue: "To" })}</span>
          <input type="date" className={inputClass} value={toDate} onChange={(e) => setToDate(e.target.value)} />
        </label>
        <Button onClick={apply}>{t("common.apply")}</Button>
      </div>
      {isLoading ? (
        <p className="text-gray-500">{t("common.loading")}</p>
      ) : !rows.length ? (
        <p className="py-8 text-center text-gray-400">
          {t("reports.nothingUnbilled", { defaultValue: "Every delivery is invoiced" })}
        </p>
      ) : (
        <Card>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50">
                <tr className="text-gray-500">
                  <th className="w-8 px-3 py-3" />
                  <th className="px-3 py-3 text-left font-medium">{t("fields.Delivery Note", { defaultValue: "Delivery Note" })}</th>
                  <th className="px-3 py-3 text-left font-medium">{t("fields.Date", { defaultValue: "Date" })}</th>
                  <th className="px-3 py-3 text-left font-medium">{t("fields.Customer", { defaultValue: "Customer" })}</th>
                  <th className="px-3 py-3 text-left font-medium">{t("fields.Sales Order", { defaultValue: "Sales Order" })}</th>
                  <th className="px-3 py-3 text-left font-medium">{t("fields.Item Code", { defaultValue: "Item Code" })}</th>
                  <th className="px-3 py-3 text-right font-medium">{t("orderProgress.delivered", { defaultValue: "Delivered" })}</th>
                  <th className="px-3 py-3 text-right font-medium">{t("orderProgress.billed", { defaultValue: "Billed" })}</th>
                  <th className="px-3 py-3 text-right font-medium">{t("reports.toBill", { defaultValue: "To bill" })}</th>
                  <th className="px-3 py-3 text-right font-medium">{t("fields.Rate", { defaultValue: "Rate" })}</th>
                  <th className="px-3 py-3 text-right font-medium">{t("fields.Amount", { defaultValue: "Amount" })}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {rows.map((row) => (
                  <tr key={row.dn_detail} className="hover:bg-gray-50">
                    <td className="px-3 py-2">
                      {urlCustomer && (
                        <input type="checkbox" checked={!excluded.includes(row.dn_detail)} onChange={() => toggle(row.dn_detail)} />
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <Link className="text-blue-600 hover:underline" to={`/app/delivery-note/${row.delivery_note}`}>{row.delivery_note}</Link>
                    </td>
                    <td className="px-3 py-2 text-gray-700">{String(row.posting_date ?? "").slice(0, 10)}</td>
                    <td className="px-3 py-2 text-gray-700">{row.customer_name || row.customer}</td>
                    <td className="px-3 py-2">
                      {row.sales_order && (
                        <Link className="text-blue-600 hover:underline" to={`/app/sales-order/${row.sales_order}`}>{row.sales_order}</Link>
                      )}
                    </td>
                    <td className="px-3 py-2 text-gray-700">{row.item_code}</td>
                    <td className="px-3 py-2 text-right">{formatNumber(row.qty)}</td>
                    <td className="px-3 py-2 text-right">{formatNumber(row.billed_qty)}</td>
                    <td className="px-3 py-2 text-right font-medium">{formatNumber(row.unbilled_qty)}</td>
                    <td className="px-3 py-2 text-right">{formatNumber(row.rate)}</td>
                    <td className="px-3 py-2 text-right">{formatNumber(row.unbilled_amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-4 flex flex-wrap items-center justify-end gap-3">
            {urlCustomer ? (
              <>
                <span className="text-sm text-fg-muted">
                  {t("reports.collectiveSelection", {
                    defaultValue: "{{count}} lines, net {{total}}",
                    count: chosen.length, total: formatNumber(total),
                  })}
                </span>
                <Button disabled={!chosen.length || customers.size !== 1 || create.isPending} onClick={() => create.mutate()}>
                  {t("reports.createCollectiveInvoice", { defaultValue: "Create invoice for selected lines" })}
                </Button>
              </>
            ) : (
              <span className="text-sm text-fg-muted">
                {t("reports.pickCustomerToBill", { defaultValue: "Choose a customer to bill these deliveries on one invoice." })}
              </span>
            )}
          </div>
          {error && <p className="mt-3 text-right text-sm text-red-600">{error}</p>}
        </Card>
      )}
    </div>
  );
}
