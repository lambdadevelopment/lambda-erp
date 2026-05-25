import { useState, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useProfitAndLoss } from "@/hooks/use-report";
import { useUrlState, useUrlPatch } from "@/hooks/use-url-state";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { LinkField } from "@/components/document/link-field";
import { DateRangePresets } from "@/components/ui/date-range-presets";
import { formatCurrency as fmtCurrency } from "@/lib/utils";
import { useBaseCurrency } from "@/hooks/use-base-currency";

export default function ProfitLossPage() {
  const { t } = useTranslation();
  const [urlCompany] = useUrlState<string>("company", "");
  const [urlFromDate] = useUrlState<string>("from", "");
  const [urlToDate] = useUrlState<string>("to", "");
  const patchUrl = useUrlPatch();

  const [company, setCompany] = useState(urlCompany);
  const baseCurrency = useBaseCurrency(company);
  const formatCurrency = (v: number | null | undefined) => fmtCurrency(v, baseCurrency);
  const [fromDate, setFromDate] = useState(urlFromDate);
  const [toDate, setToDate] = useState(urlToDate);

  const filters = useMemo(() => {
    const f: Record<string, string> = {};
    if (urlCompany) f.company = urlCompany;
    if (urlFromDate) f.from_date = urlFromDate;
    if (urlToDate) f.to_date = urlToDate;
    return f;
  }, [urlCompany, urlFromDate, urlToDate]);

  const { data, isLoading, refetch } = useProfitAndLoss(filters);

  const handleApply = () => {
    patchUrl({
      company: company || null,
      from: fromDate || null,
      to: toDate || null,
    });
    refetch();
  };

  const hasData = data && ((data.income && data.income.length > 0) || (data.expense && data.expense.length > 0));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-4">
        <LinkField label={t("fields.Company", { defaultValue: "Company" })} value={company} onChange={setCompany} linkDoctype="company" readOnly={false} />
        <Input label={t("fields.From Date", { defaultValue: "From Date" })} type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
        <Input label={t("fields.To Date", { defaultValue: "To Date" })} type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} />
        <Button onClick={handleApply}>{t("common.apply")}</Button>
      </div>
      <DateRangePresets onSelect={(from, to) => {
        setFromDate(from);
        setToDate(to);
        patchUrl({ from, to });
      }} />

      {isLoading ? (
        <p className="text-gray-500">{t("common.loading")}</p>
      ) : !hasData ? (
        <p className="py-8 text-center text-gray-400">{t("reports.noData")}</p>
      ) : (
        <div className="space-y-4">
          <Card>
            <h3 className="mb-3 text-base font-semibold text-gray-800">{t("reports.income")}</h3>
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left font-medium text-gray-500">{t("fields.Account", { defaultValue: "Account" })}</th>
                  <th className="px-4 py-3 text-right font-medium text-gray-500">{t("fields.Amount", { defaultValue: "Amount" })}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {(data.income || []).map((row: any, idx: number) => (
                  <tr key={idx} className="hover:bg-gray-50">
                    <td className="px-4 py-2 text-gray-900">{row.account}</td>
                    <td className="px-4 py-2 text-right">{formatCurrency(row.amount)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="border-t-2 border-gray-300 bg-gray-50 font-semibold">
                <tr>
                  <td className="px-4 py-3 text-gray-900">{t("reports.totalIncome")}</td>
                  <td className="px-4 py-3 text-right">{formatCurrency(data.total_income)}</td>
                </tr>
              </tfoot>
            </table>
          </Card>

          <Card>
            <h3 className="mb-3 text-base font-semibold text-gray-800">{t("reports.expenses")}</h3>
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left font-medium text-gray-500">{t("fields.Account", { defaultValue: "Account" })}</th>
                  <th className="px-4 py-3 text-right font-medium text-gray-500">{t("fields.Amount", { defaultValue: "Amount" })}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {(data.expense || []).map((row: any, idx: number) => (
                  <tr key={idx} className="hover:bg-gray-50">
                    <td className="px-4 py-2 text-gray-900">{row.account}</td>
                    <td className="px-4 py-2 text-right">{formatCurrency(row.amount)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="border-t-2 border-gray-300 bg-gray-50 font-semibold">
                <tr>
                  <td className="px-4 py-3 text-gray-900">{t("reports.totalExpenses")}</td>
                  <td className="px-4 py-3 text-right">{formatCurrency(data.total_expense)}</td>
                </tr>
              </tfoot>
            </table>
          </Card>

          <div className="text-center">
            <span className={`inline-flex items-center rounded-full px-5 py-2 text-sm font-semibold ${data.net_profit >= 0 ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}`}>
              {data.net_profit >= 0 ? t("reports.netProfit") : t("reports.netLoss")}: {formatCurrency(Math.abs(data.net_profit))}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
