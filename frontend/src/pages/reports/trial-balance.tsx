import { useState, useMemo } from "react";
import { useTrialBalance } from "@/hooks/use-report";
import { useUrlState, useUrlPatch } from "@/hooks/use-url-state";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { LinkField } from "@/components/document/link-field";
import { DateRangePresets } from "@/components/ui/date-range-presets";
import { formatCurrency } from "@/lib/utils";

export default function TrialBalancePage() {
  const [urlCompany] = useUrlState<string>("company", "");
  const [urlFromDate] = useUrlState<string>("from", "");
  const [urlToDate] = useUrlState<string>("to", "");
  const patchUrl = useUrlPatch();

  const [company, setCompany] = useState(urlCompany);
  const [fromDate, setFromDate] = useState(urlFromDate);
  const [toDate, setToDate] = useState(urlToDate);

  const filters = useMemo(() => {
    const f: Record<string, string> = {};
    if (urlCompany) f.company = urlCompany;
    if (urlFromDate) f.from_date = urlFromDate;
    if (urlToDate) f.to_date = urlToDate;
    return f;
  }, [urlCompany, urlFromDate, urlToDate]);

  const { data, isLoading, refetch } = useTrialBalance(filters);

  const handleApply = () => {
    patchUrl({
      company: company || null,
      from: fromDate || null,
      to: toDate || null,
    });
    refetch();
  };

  const isBalanced = data ? data.difference === 0 : false;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-4">
        <LinkField
          label="Company"
          value={company}
          onChange={setCompany}
          linkDoctype="company"
          readOnly={false}
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
        <Button onClick={handleApply}>Apply</Button>
      </div>
      <DateRangePresets onSelect={(from, to) => {
        setFromDate(from);
        setToDate(to);
        patchUrl({ from, to });
      }} />

      {isLoading ? (
        <p className="text-fg-muted">Loading...</p>
      ) : !data || !data.rows || data.rows.length === 0 ? (
        <p className="py-8 text-center text-fg-muted">No data found</p>
      ) : (
        <Card>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-line text-sm">
              <thead className="bg-surface-subtle">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-fg-muted">
                    Account
                  </th>
                  <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wide text-fg-muted">
                    Debit
                  </th>
                  <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wide text-fg-muted">
                    Credit
                  </th>
                  <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wide text-fg-muted">
                    Balance
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {data.rows.map((row: any, idx: number) => (
                  <tr key={idx} className="transition-colors hover:bg-surface-subtle">
                    <td className="px-4 py-2 text-fg">{row.account}</td>
                    <td className="px-4 py-2 text-right tabular-nums text-fg">
                      {formatCurrency(row.debit)}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums text-fg">
                      {formatCurrency(row.credit)}
                    </td>
                    <td className="px-4 py-2 text-right font-medium tabular-nums text-fg">
                      {formatCurrency(row.balance)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="border-t border-line bg-surface-subtle font-semibold">
                <tr>
                  <td className="px-4 py-3 text-fg">Total</td>
                  <td className="px-4 py-3 text-right tabular-nums text-fg">
                    {formatCurrency(data.total_debit)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-fg">
                    {formatCurrency(data.total_credit)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-fg">
                    {formatCurrency(data.difference)}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>

          <div className="mt-4 text-center">
            {isBalanced ? (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-4 py-1.5 text-sm font-semibold text-emerald-700 ring-1 ring-emerald-200">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                BALANCED
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-rose-50 px-4 py-1.5 text-sm font-semibold text-rose-700 ring-1 ring-rose-200">
                <span className="h-1.5 w-1.5 rounded-full bg-rose-500" />
                IMBALANCED
              </span>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
