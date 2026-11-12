import { useState, useMemo } from "react";
import { Link } from "react-router-dom";
import { usePageTitle } from "@/lib/use-page-title";
import { useTranslation } from "react-i18next";
import { useStockDetail, useBatchMovements, useBatchRecipients, useBatchChanges } from "@/hooks/use-report";
import { useUrlState, useUrlPatch } from "@/hooks/use-url-state";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { LinkField } from "@/components/document/link-field";
import { formatNumber } from "@/lib/utils";
import { getStockColumns } from "@/lib/stock-columns";

const slug = (doctype: string) => doctype.toLowerCase().replace(/ /g, "-");
const today = () => new Date().toISOString().slice(0, 10);

/** Where stock is, in one view: item × warehouse × storage location × batch,
 *  with expiry. A batch label is not unique across items, so a search by
 *  label can list several items. */
export default function BatchStockPage() {
  const { t } = useTranslation();
  usePageTitle(t("titles.stock-detail", { defaultValue: "Stock by Location & Batch" }));
  const [urlItemCode] = useUrlState<string>("item_code", "");
  const [urlWarehouse] = useUrlState<string>("warehouse", "");
  const [urlBatch] = useUrlState<string>("batch_no", "");
  const [urlPrefix] = useUrlState<string>("item_prefix", "");
  const [urlExpiring] = useUrlState<string>("expiring_before", "");
  const [urlLocation] = useUrlState<string>("location", "");
  const [urlCustomer] = useUrlState<string>("consignment_customer", "");
  const patchUrl = useUrlPatch();

  const [itemCode, setItemCode] = useState(urlItemCode);
  const [warehouse, setWarehouse] = useState(urlWarehouse);
  const [batch, setBatch] = useState(urlBatch);
  const [prefix, setPrefix] = useState(urlPrefix);
  const [expiring, setExpiring] = useState(urlExpiring);
  const [location, setLocation] = useState(urlLocation);
  const [customer, setCustomer] = useState(urlCustomer);
  const [selected, setSelected] = useState<{ batch_no: string; item_code: string } | null>(null);

  const filters = useMemo(() => {
    const f: Record<string, string> = {};
    if (urlItemCode) f.item_code = urlItemCode;
    if (urlWarehouse) f.warehouse = urlWarehouse;
    if (urlBatch) f.batch_no = urlBatch;
    if (urlPrefix) f.item_prefix = urlPrefix;
    if (urlExpiring) f.expiring_before = urlExpiring;
    if (urlLocation) f.location = urlLocation;
    if (urlCustomer) f.consignment_customer = urlCustomer;
    return f;
  }, [urlItemCode, urlWarehouse, urlBatch, urlPrefix, urlExpiring, urlLocation, urlCustomer]);

  const { data, isLoading, refetch } = useStockDetail(filters);
  const movements = useBatchMovements(selected);
  const recipients = useBatchRecipients(selected);
  const changes = useBatchChanges(selected);

  const handleApply = () => {
    patchUrl({
      item_code: itemCode || null,
      warehouse: warehouse || null,
      batch_no: batch.trim() || null,
      item_prefix: prefix.trim() || null,
      expiring_before: expiring || null,
      location: location.trim() || null,
      consignment_customer: customer || null,
    });
    setSelected(null);
    refetch();
  };

  const th = "px-4 py-3 text-left font-medium text-gray-500";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-4">
        <Input
          label={t("fields.Batch", { defaultValue: "Batch" })}
          value={batch}
          onChange={(e) => setBatch(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleApply()}
        />
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
        <LinkField
          label={t("fields.Consignment Customer", { defaultValue: "Consignment Customer" })}
          value={customer}
          onChange={setCustomer}
          linkDoctype="customer"
          readOnly={false}
        />
        <Input
          label={t("fields.Storage Location", { defaultValue: "Storage Location" })}
          value={location}
          onChange={(e) => setLocation(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleApply()}
        />
        <Input
          type="date"
          label={t("fields.Expiring before", { defaultValue: "Expiring before" })}
          value={expiring}
          onChange={(e) => setExpiring(e.target.value)}
        />
        <Button onClick={handleApply}>{t("common.apply")}</Button>
      </div>

      {isLoading ? (
        <p className="text-gray-500">{t("common.loading")}</p>
      ) : !data || !data.rows || data.rows.length === 0 ? (
        <p className="py-8 text-center text-gray-400">
          {t("reports.noStockData", { defaultValue: "No stock data found" })}
        </p>
      ) : (
        <Card>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className={th}>{t("fields.Item Code", { defaultValue: "Item Code" })}</th>
                  <th className={th}>{t("fields.Item Name", { defaultValue: "Item Name" })}</th>
                  <th className={th}>{t("fields.Warehouse", { defaultValue: "Warehouse" })}</th>
                  <th className={th}>{t("fields.Consignment Customer", { defaultValue: "Consignment Customer" })}</th>
                  <th className={th}>{t("fields.Storage Location", { defaultValue: "Storage Location" })}</th>
                  <th className={th}>{t("fields.Batch", { defaultValue: "Batch" })}</th>
                  <th className={th}>{t("fields.Expiry Date", { defaultValue: "Expiry Date" })}</th>
                  {getStockColumns().map((column) => (
                    <th key={column.key} className={th}>{t(`fields.${column.label}`, { defaultValue: column.label })}</th>
                  ))}
                  <th className="px-4 py-3 text-right font-medium text-gray-500">
                    {t("fields.Qty", { defaultValue: "Qty" })}
                  </th>
                  <th className="px-4 py-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {data.rows.map((row: any, idx: number) => {
                  const expired = row.expiry_date && row.expiry_date < today();
                  return (
                    <tr key={idx} className="hover:bg-gray-50">
                      <td className="px-4 py-2 font-medium text-gray-900">{row.item_code}</td>
                      <td className="px-4 py-2 text-gray-700">{row.item_name}</td>
                      <td className="px-4 py-2 text-gray-700">{row.warehouse}</td>
                      <td className="px-4 py-2 text-gray-700">{row.consignment_customer || ""}</td>
                      <td className="px-4 py-2 text-gray-700">{row.location || ""}</td>
                      <td className="px-4 py-2 text-gray-700">{row.batch_no || ""}</td>
                      <td className={`px-4 py-2 ${expired ? "font-medium text-red-600" : "text-gray-700"}`}>
                        {row.expiry_date || ""}
                        {expired ? ` (${t("reports.expired", { defaultValue: "expired" })})` : ""}
                      </td>
                      {getStockColumns().map((column) => (
                        <td key={column.key} className="px-4 py-2 text-gray-700">
                          {column.render ? column.render(row) : (row[column.key] ?? "")}
                        </td>
                      ))}
                      <td className="px-4 py-2 text-right">{formatNumber(row.actual_qty)}</td>
                      <td className="px-4 py-2 text-right">
                        {row.batch_no ? (
                          <button
                            type="button"
                            className="text-sm text-blue-600 hover:underline"
                            onClick={() => setSelected({ batch_no: row.batch_no, item_code: row.item_code })}
                          >
                            {t("reports.showMovements", { defaultValue: "Movements" })}
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {selected && (recipients.data?.rows?.length ?? 0) > 0 ? (
        <Card>
          <div className="border-b border-gray-200 px-4 py-3 text-sm font-medium text-gray-900">
            {t("reports.batchRecipients", {
              defaultValue: "Who received batch {{batch}}",
              batch: selected.batch_no,
            })}
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className={th}>{t("fields.Customer", { defaultValue: "Customer" })}</th>
                  <th className="px-4 py-3 text-right font-medium text-gray-500">{t("fields.Delivered", { defaultValue: "Delivered" })}</th>
                  <th className="px-4 py-3 text-right font-medium text-gray-500">{t("fields.Returned", { defaultValue: "Returned" })}</th>
                  <th className="px-4 py-3 text-right font-medium text-gray-500">{t("fields.Still With Them", { defaultValue: "Still With Them" })}</th>
                  <th className={th}>{t("fields.Last Delivery", { defaultValue: "Last Delivery" })}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {(recipients.data?.rows ?? []).map((row: any) => (
                  <tr key={`${row.item_code}-${row.customer}`} className="hover:bg-gray-50">
                    <td className="px-4 py-2 text-gray-900">{row.customer_name} <span className="text-gray-400">{row.customer}</span></td>
                    <td className="px-4 py-2 text-right">{formatNumber(row.delivered_qty)}</td>
                    <td className="px-4 py-2 text-right">{formatNumber(row.returned_qty)}</td>
                    <td className="px-4 py-2 text-right font-medium">{formatNumber(row.net_qty)}</td>
                    <td className="px-4 py-2 text-gray-700">{row.last_date}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}

      {selected && (changes.data?.rows?.length ?? 0) > 0 ? (
        <Card>
          <div className="border-b border-gray-200 px-4 py-3 text-sm font-medium text-gray-900">
            {t("reports.batchChanges", { defaultValue: "Changes to batch {{batch}}", batch: selected.batch_no })}
          </div>
          <ul className="divide-y divide-gray-100 text-sm">
            {(changes.data?.rows ?? []).map((row: any, idx: number) => (
              <li key={idx} className="px-4 py-2 text-gray-700">
                <span className="text-gray-500">{String(row.changed_at ?? "").slice(0, 16).replace("T", " ")}</span>{" "}
                <span className="font-mono text-xs">{row.field}</span>: {row.old_value ?? "–"} → {row.new_value ?? "–"}
                <span className="text-gray-500"> · {row.changed_by ?? "system"} · {row.source}</span>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      {selected ? (
        <Card>
          <div className="border-b border-gray-200 px-4 py-3 text-sm font-medium text-gray-900">
            {t("reports.batchMovements", {
              defaultValue: "Movements of batch {{batch}} ({{item}})",
              batch: selected.batch_no,
              item: selected.item_code,
            })}
          </div>
          {movements.isLoading ? (
            <p className="px-4 py-3 text-gray-500">{t("common.loading")}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200 text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    <th className={th}>{t("fields.Posting Date", { defaultValue: "Posting Date" })}</th>
                    <th className={th}>{t("fields.Document", { defaultValue: "Document" })}</th>
                    <th className={th}>{t("fields.Party", { defaultValue: "Party" })}</th>
                    <th className={th}>{t("fields.Warehouse", { defaultValue: "Warehouse" })}</th>
                    <th className={th}>{t("fields.Storage Location", { defaultValue: "Storage Location" })}</th>
                    <th className="px-4 py-3 text-right font-medium text-gray-500">
                      {t("fields.Qty", { defaultValue: "Qty" })}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {(movements.data?.rows ?? []).map((row: any) => (
                    <tr key={row.name} className="hover:bg-gray-50">
                      <td className="px-4 py-2 text-gray-700">{row.posting_date}</td>
                      <td className="px-4 py-2">
                        <Link className="text-blue-600 hover:underline" to={`/app/${slug(row.voucher_type)}/${row.voucher_no}`}>
                          {row.voucher_type} {row.voucher_no}
                        </Link>
                      </td>
                      <td className="px-4 py-2 text-gray-700">{row.party || ""}</td>
                      <td className="px-4 py-2 text-gray-700">{row.warehouse}</td>
                      <td className="px-4 py-2 text-gray-700">{row.location || ""}</td>
                      <td className="px-4 py-2 text-right">{formatNumber(row.actual_qty)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      ) : null}
    </div>
  );
}
