import { useState, useMemo } from "react";
import { Link } from "react-router-dom";
import { useApAging } from "@/hooks/use-report";
import { useUrlState, useUrlPatch } from "@/hooks/use-url-state";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { LinkField } from "@/components/document/link-field";
import { SingleDatePresets } from "@/components/ui/date-range-presets";
import { formatCurrency } from "@/lib/utils";

export default function ApAgingPage() {
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

  const { data, isLoading, refetch } = useApAging(filters);

  const handleApply = () => {
    patchUrl({ company: company || null, as_of_date: asOfDate || null });
    refetch();
  };

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
      ) : !data || !data.rows || data.rows.length === 0 ? (
        <p className="py-8 text-center text-gray-400">No outstanding payables</p>
      ) : (
        <Card>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-3 py-3 text-left font-medium text-gray-500">Invoice</th>
                  <th className="px-3 py-3 text-left font-medium text-gray-500">Supplier</th>
                  <th className="px-3 py-3 text-left font-medium text-gray-500">Due Date</th>
                  <th className="px-3 py-3 text-right font-medium text-gray-500">Outstanding</th>
                  <th className="px-3 py-3 text-right font-medium text-gray-500">Current</th>
                  <th className="px-3 py-3 text-right font-medium text-gray-500">1-30</th>
                  <th className="px-3 py-3 text-right font-medium text-gray-500">31-60</th>
                  <th className="px-3 py-3 text-right font-medium text-gray-500">61-90</th>
                  <th className="px-3 py-3 text-right font-medium text-gray-500">90+</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {data.rows.map((row: any, idx: number) => (
                  <tr key={idx} className="hover:bg-gray-50">
                    <td className="px-3 py-2">
                      <Link
                        to={`/app/purchase-invoice/${encodeURIComponent(row.invoice)}`}
                        className="font-medium text-blue-600 hover:text-blue-800"
                      >
                        {row.invoice}
                      </Link>
                    </td>
                    <td className="px-3 py-2 text-gray-900">{row.supplier_name}</td>
                    <td className="px-3 py-2 text-gray-600">{row.due_date}</td>
                    <td className="px-3 py-2 text-right font-medium">{formatCurrency(row.outstanding)}</td>
                    <td className="px-3 py-2 text-right">{row.current ? formatCurrency(row.current) : ""}</td>
                    <td className="px-3 py-2 text-right">{row.b1_30 ? formatCurrency(row.b1_30) : ""}</td>
                    <td className="px-3 py-2 text-right">{row.b31_60 ? formatCurrency(row.b31_60) : ""}</td>
                    <td className="px-3 py-2 text-right">{row.b61_90 ? formatCurrency(row.b61_90) : ""}</td>
                    <td className="px-3 py-2 text-right text-red-600">{row.b90_plus ? formatCurrency(row.b90_plus) : ""}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="border-t-2 border-gray-300 bg-gray-50 font-semibold">
                <tr>
                  <td className="px-3 py-3" colSpan={3}>Total</td>
                  <td className="px-3 py-3 text-right">{formatCurrency(data.totals.outstanding)}</td>
                  <td className="px-3 py-3 text-right">{formatCurrency(data.totals.current)}</td>
                  <td className="px-3 py-3 text-right">{formatCurrency(data.totals.b1_30)}</td>
                  <td className="px-3 py-3 text-right">{formatCurrency(data.totals.b31_60)}</td>
                  <td className="px-3 py-3 text-right">{formatCurrency(data.totals.b61_90)}</td>
                  <td className="px-3 py-3 text-right text-red-600">{formatCurrency(data.totals.b90_plus)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
