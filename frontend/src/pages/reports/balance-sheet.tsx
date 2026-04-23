import { useState, useMemo } from "react";
import { useBalanceSheet } from "@/hooks/use-report";
import { useUrlState, useUrlPatch } from "@/hooks/use-url-state";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { LinkField } from "@/components/document/link-field";
import { SingleDatePresets } from "@/components/ui/date-range-presets";
import { formatCurrency } from "@/lib/utils";

function SectionTable({ title, rows, total, totalLabel }: { title: string; rows: any[]; total: number; totalLabel: string }) {
  return (
    <Card>
      <h3 className="mb-3 text-base font-semibold text-gray-800">{title}</h3>
      <table className="min-w-full divide-y divide-gray-200 text-sm">
        <thead className="bg-gray-50">
          <tr>
            <th className="px-4 py-3 text-left font-medium text-gray-500">Account</th>
            <th className="px-4 py-3 text-right font-medium text-gray-500">Balance</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {rows.map((row: any, idx: number) => (
            <tr key={idx} className="hover:bg-gray-50">
              <td className="px-4 py-2 text-gray-900">{row.account}</td>
              <td className="px-4 py-2 text-right">{formatCurrency(row.balance)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot className="border-t-2 border-gray-300 bg-gray-50 font-semibold">
          <tr>
            <td className="px-4 py-3 text-gray-900">{totalLabel}</td>
            <td className="px-4 py-3 text-right">{formatCurrency(total)}</td>
          </tr>
        </tfoot>
      </table>
    </Card>
  );
}

export default function BalanceSheetPage() {
  const [urlCompany] = useUrlState<string>("company", "");
  const [urlAsOfDate] = useUrlState<string>("as_of_date", "");
  const patchUrl = useUrlPatch();

  const [company, setCompany] = useState(urlCompany);
  const [asOfDate, setAsOfDate] = useState(urlAsOfDate);

  const filters = useMemo(() => {
    const f: Record<string, string> = {};
    if (urlCompany) f.company = urlCompany;
    if (urlAsOfDate) f.as_of_date = urlAsOfDate;
    return f;
  }, [urlCompany, urlAsOfDate]);

  const { data, isLoading, refetch } = useBalanceSheet(filters);

  const handleApply = () => {
    patchUrl({ company: company || null, as_of_date: asOfDate || null });
    refetch();
  };

  const hasData = data && ((data.assets?.length > 0) || (data.liabilities?.length > 0) || (data.equity?.length > 0));
  const isBalanced = data ? Math.abs(data.total_assets - data.total_liabilities_and_equity) < 0.01 : false;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-4">
        <LinkField label="Company" value={company} onChange={setCompany} linkDoctype="company" readOnly={false} />
        <Input label="As of Date" type="date" value={asOfDate} onChange={(e) => setAsOfDate(e.target.value)} />
        <Button onClick={handleApply}>Apply</Button>
      </div>
      <SingleDatePresets onSelect={(date) => {
        setAsOfDate(date);
        patchUrl({ as_of_date: date });
      }} />

      {isLoading ? (
        <p className="text-gray-500">Loading...</p>
      ) : !hasData ? (
        <p className="py-8 text-center text-gray-400">No data found</p>
      ) : (
        <div className="space-y-4">
          <SectionTable title="Assets" rows={data.assets || []} total={data.total_assets} totalLabel="Total Assets" />
          <SectionTable title="Liabilities" rows={data.liabilities || []} total={data.total_liabilities} totalLabel="Total Liabilities" />
          <SectionTable title="Equity" rows={data.equity || []} total={data.total_equity} totalLabel="Total Equity" />

          <Card>
            <div className="flex items-center justify-between">
              <div className="text-sm">
                <div className="text-gray-500">Total Assets: <span className="font-semibold text-gray-900">{formatCurrency(data.total_assets)}</span></div>
                <div className="text-gray-500">Total Liabilities + Equity: <span className="font-semibold text-gray-900">{formatCurrency(data.total_liabilities_and_equity)}</span></div>
              </div>
              {isBalanced ? (
                <span className="inline-flex items-center rounded-full bg-green-100 px-4 py-1.5 text-sm font-semibold text-green-800">BALANCED</span>
              ) : (
                <span className="inline-flex items-center rounded-full bg-red-100 px-4 py-1.5 text-sm font-semibold text-red-800">IMBALANCED</span>
              )}
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
