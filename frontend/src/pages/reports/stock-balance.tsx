import { useState, useMemo } from "react";
import { useStockBalance } from "@/hooks/use-report";
import { useUrlState, useUrlPatch } from "@/hooks/use-url-state";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { LinkField } from "@/components/document/link-field";
import { formatCurrency, formatNumber } from "@/lib/utils";

export default function StockBalancePage() {
  const [urlItemCode] = useUrlState<string>("item_code", "");
  const [urlWarehouse] = useUrlState<string>("warehouse", "");
  const patchUrl = useUrlPatch();

  const [itemCode, setItemCode] = useState(urlItemCode);
  const [warehouse, setWarehouse] = useState(urlWarehouse);

  const filters = useMemo(() => {
    const f: Record<string, string> = {};
    if (urlItemCode) f.item_code = urlItemCode;
    if (urlWarehouse) f.warehouse = urlWarehouse;
    return f;
  }, [urlItemCode, urlWarehouse]);

  const { data, isLoading, refetch } = useStockBalance(filters);

  const handleApply = () => {
    patchUrl({
      item_code: itemCode || null,
      warehouse: warehouse || null,
    });
    refetch();
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-4">
        <LinkField
          label="Item Code"
          value={itemCode}
          onChange={setItemCode}
          linkDoctype="item"
          readOnly={false}
        />
        <LinkField
          label="Warehouse"
          value={warehouse}
          onChange={setWarehouse}
          linkDoctype="warehouse"
          readOnly={false}
        />
        <Button onClick={handleApply}>Apply</Button>
      </div>

      {isLoading ? (
        <p className="text-gray-500">Loading...</p>
      ) : !data || !data.rows || data.rows.length === 0 ? (
        <p className="py-8 text-center text-gray-400">No stock data found</p>
      ) : (
        <Card>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left font-medium text-gray-500">
                    Item Code
                  </th>
                  <th className="px-4 py-3 text-left font-medium text-gray-500">
                    Item Name
                  </th>
                  <th className="px-4 py-3 text-left font-medium text-gray-500">
                    Warehouse
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-gray-500">
                    Qty
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-gray-500">
                    Valuation Rate
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-gray-500">
                    Stock Value
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {data.rows.map((row: any, idx: number) => (
                  <tr key={idx} className="hover:bg-gray-50">
                    <td className="px-4 py-2 font-medium text-gray-900">
                      {row.item_code}
                    </td>
                    <td className="px-4 py-2 text-gray-700">
                      {row.item_name}
                    </td>
                    <td className="px-4 py-2 text-gray-700">
                      {row.warehouse}
                    </td>
                    <td className="px-4 py-2 text-right">
                      {formatNumber(row.actual_qty)}
                    </td>
                    <td className="px-4 py-2 text-right">
                      {formatCurrency(row.valuation_rate)}
                    </td>
                    <td className="px-4 py-2 text-right font-medium">
                      {formatCurrency(row.stock_value)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
