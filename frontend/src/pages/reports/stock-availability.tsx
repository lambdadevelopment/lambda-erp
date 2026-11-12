import { useState, useMemo } from "react";
import { usePageTitle } from "@/lib/use-page-title";
import { useTranslation } from "react-i18next";
import { useStockAvailability } from "@/hooks/use-report";
import { useUrlState, useUrlPatch } from "@/hooks/use-url-state";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { LinkField } from "@/components/document/link-field";
import { formatNumber } from "@/lib/utils";

/** In stock, blocked, reserved and available per item and warehouse, next to
 *  what customers have ordered and what is on order from suppliers. */
export default function StockAvailabilityPage() {
  const { t } = useTranslation();
  usePageTitle(t("titles.stock-availability", { defaultValue: "Stock Availability" }));
  const [urlItemCode] = useUrlState<string>("item_code", "");
  const [urlWarehouse] = useUrlState<string>("warehouse", "");
  const [urlPrefix] = useUrlState<string>("item_prefix", "");
  const patchUrl = useUrlPatch();

  const [itemCode, setItemCode] = useState(urlItemCode);
  const [warehouse, setWarehouse] = useState(urlWarehouse);
  const [prefix, setPrefix] = useState(urlPrefix);

  const filters = useMemo(() => {
    const f: Record<string, string> = {};
    if (urlItemCode) f.item_code = urlItemCode;
    if (urlWarehouse) f.warehouse = urlWarehouse;
    if (urlPrefix) f.item_prefix = urlPrefix;
    return f;
  }, [urlItemCode, urlWarehouse, urlPrefix]);

  const { data, isLoading, refetch } = useStockAvailability(filters);

  const handleApply = () => {
    patchUrl({ item_code: itemCode || null, warehouse: warehouse || null, item_prefix: prefix.trim() || null });
    refetch();
  };

  const left = "px-4 py-3 text-left font-medium text-gray-500";
  const right = "px-4 py-3 text-right font-medium text-gray-500";
  const columns: Array<[string, string]> = [
    ["physical_qty", "In Stock"],
    ["blocked_qty", "Blocked"],
    ["reserved_qty", "Reserved"],
    ["available_qty", "Available"],
    ["open_sales_order_qty", "Open Sales Orders"],
    ["on_purchase_order_qty", "On Purchase Order"],
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-4">
        <LinkField
          label={t("fields.Item Code", { defaultValue: "Item Code" })}
          value={itemCode}
          onChange={setItemCode}
          linkDoctype="item"
          readOnly={false}
        />
        <Input
          label={t("fields.Item starts with", { defaultValue: "Item starts with" })}
          value={prefix}
          onChange={(e) => setPrefix(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleApply()}
        />
        <LinkField
          label={t("fields.Warehouse", { defaultValue: "Warehouse" })}
          value={warehouse}
          onChange={setWarehouse}
          linkDoctype="warehouse"
          readOnly={false}
        />
        <Button onClick={handleApply}>{t("common.apply")}</Button>
      </div>

      {isLoading ? (
        <p className="text-gray-500">{t("common.loading")}</p>
      ) : !data || !data.rows || data.rows.length === 0 ? (
        <p className="py-8 text-center text-gray-400">{t("reports.noStockData")}</p>
      ) : (
        <Card>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className={left}>{t("fields.Item Code", { defaultValue: "Item Code" })}</th>
                  <th className={left}>{t("fields.Item Name", { defaultValue: "Item Name" })}</th>
                  <th className={left}>{t("fields.Warehouse", { defaultValue: "Warehouse" })}</th>
                  {columns.map(([key, label]) => (
                    <th key={key} className={right}>{t(`fields.${label}`, { defaultValue: label })}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {data.rows.map((row: any, idx: number) => (
                  <tr key={idx} className="hover:bg-gray-50">
                    <td className="px-4 py-2 font-medium text-gray-900">{row.item_code}</td>
                    <td className="px-4 py-2 text-gray-700">{row.item_name}</td>
                    <td className="px-4 py-2 text-gray-700">{row.warehouse}</td>
                    {columns.map(([key]) => (
                      <td
                        key={key}
                        className={`px-4 py-2 text-right ${key === "available_qty" ? "font-medium" : ""} ${
                          key === "blocked_qty" && row[key] > 0 ? "text-red-600" : ""
                        }`}
                      >
                        {formatNumber(row[key])}
                      </td>
                    ))}
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
