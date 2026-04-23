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
        <p className="text-gray-500">Loading...</p>
      ) : !data || !data.rows || data.rows.length === 0 ? (
        <p className="py-8 text-center text-gray-400">No data found</p>
      ) : (
        <Card>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left font-medium text-gray-500">
                    Account
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-gray-500">
                    Debit
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-gray-500">
                    Credit
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-gray-500">
                    Balance
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {data.rows.map((row: any, idx: number) => (
                  <tr key={idx} className="hover:bg-gray-50">
                    <td className="px-4 py-2 text-gray-900">{row.account}</td>
                    <td className="px-4 py-2 text-right">
                      {formatCurrency(row.debit)}
                    </td>
                    <td className="px-4 py-2 text-right">
                      {formatCurrency(row.credit)}
                    </td>
                    <td className="px-4 py-2 text-right font-medium">
                      {formatCurrency(row.balance)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="border-t-2 border-gray-300 bg-gray-50 font-semibold">
                <tr>
                  <td className="px-4 py-3 text-gray-900">Total</td>
                  <td className="px-4 py-3 text-right">
                    {formatCurrency(data.total_debit)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {formatCurrency(data.total_credit)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {formatCurrency(data.difference)}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>

          <div className="mt-4 text-center">
            {isBalanced ? (
              <span className="inline-flex items-center rounded-full bg-green-100 px-4 py-1.5 text-sm font-semibold text-green-800">
                BALANCED
              </span>
            ) : (
              <span className="inline-flex items-center rounded-full bg-red-100 px-4 py-1.5 text-sm font-semibold text-red-800">
                IMBALANCED
              </span>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
