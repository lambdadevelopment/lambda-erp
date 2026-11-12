import { useState, useMemo, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { usePageTitle } from "@/lib/use-page-title";
import { useTranslation } from "react-i18next";
import { useSalesBacklog, usePurchaseBacklog } from "@/hooks/use-report";
import { useUrlState, useUrlPatch } from "@/hooks/use-url-state";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { LinkField } from "@/components/document/link-field";
import { formatNumber } from "@/lib/utils";

interface Column {
  key: string;
  label: string;
  numeric?: boolean;
  render?: (row: any) => ReactNode;
}

function BacklogTable({ rows, columns }: { rows: any[]; columns: Column[] }) {
  const { t } = useTranslation();
  return (
    <Card>
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50">
            <tr>
              {columns.map((c) => (
                <th key={c.key} className={`px-4 py-3 font-medium text-gray-500 ${c.numeric ? "text-right" : "text-left"}`}>
                  {t(`fields.${c.label}`, { defaultValue: c.label })}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map((row, idx) => (
              <tr key={idx} className="hover:bg-gray-50">
                {columns.map((c) => (
                  <td key={c.key} className={`px-4 py-2 ${c.numeric ? "text-right" : "text-gray-700"}`}>
                    {c.render ? c.render(row) : c.numeric ? formatNumber(row[c.key]) : row[c.key] ?? ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

const docLink = (slug: string, key: string) => (row: any) =>
  <Link className="text-blue-600 hover:underline" to={`/app/${slug}/${row[key]}`}>{row[key]}</Link>;

/** Open sales order lines: what is still to deliver, what can go out now and
 *  what is on its way. Reading it books nothing. */
export function SalesBacklogPage() {
  const { t } = useTranslation();
  usePageTitle(t("titles.sales-backlog", { defaultValue: "Sales Backlog" }));
  const [urlCustomer] = useUrlState<string>("customer", "");
  const [urlItem] = useUrlState<string>("item_code", "");
  const [urlDeliverable] = useUrlState<string>("only_deliverable", "");
  const patchUrl = useUrlPatch();
  const [customer, setCustomer] = useState(urlCustomer);
  const [item, setItem] = useState(urlItem);
  const [deliverable, setDeliverable] = useState(urlDeliverable === "true");

  const filters = useMemo(() => {
    const f: Record<string, string> = {};
    if (urlCustomer) f.customer = urlCustomer;
    if (urlItem) f.item_code = urlItem;
    if (urlDeliverable === "true") f.only_deliverable = "true";
    return f;
  }, [urlCustomer, urlItem, urlDeliverable]);
  const { data, isLoading, refetch } = useSalesBacklog(filters);
  const apply = () => {
    patchUrl({ customer: customer || null, item_code: item || null, only_deliverable: deliverable ? "true" : null });
    refetch();
  };

  const columns: Column[] = [
    { key: "sales_order", label: "Sales Order", render: docLink("sales-order", "sales_order") },
    { key: "customer_name", label: "Customer" },
    { key: "delivery_date", label: "Delivery Date" },
    { key: "item_code", label: "Item Code" },
    { key: "warehouse", label: "Warehouse" },
    { key: "ordered_qty", label: "Ordered", numeric: true },
    { key: "delivered_qty", label: "Delivered", numeric: true },
    { key: "open_qty", label: "Open", numeric: true },
    { key: "reserved_qty", label: "Reserved", numeric: true },
    { key: "deliverable_qty", label: "Deliverable Now", numeric: true,
      render: (row) => <span className={row.deliverable_qty > 0 ? "font-medium text-green-700" : ""}>{formatNumber(row.deliverable_qty)}</span> },
    { key: "on_purchase_order_qty", label: "On Purchase Order", numeric: true },
    { key: "next_expected_date", label: "Expected" },
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-4">
        <LinkField label={t("fields.Customer", { defaultValue: "Customer" })} value={customer} onChange={setCustomer} linkDoctype="customer" readOnly={false} />
        <LinkField label={t("fields.Item Code", { defaultValue: "Item Code" })} value={item} onChange={setItem} linkDoctype="item" readOnly={false} />
        <label className="flex items-center gap-2 pb-2 text-sm">
          <input type="checkbox" checked={deliverable} onChange={(e) => setDeliverable(e.target.checked)} />
          {t("reports.onlyDeliverable", { defaultValue: "Only what can be delivered now" })}
        </label>
        <Button onClick={apply}>{t("common.apply")}</Button>
      </div>
      {isLoading ? (
        <p className="text-gray-500">{t("common.loading")}</p>
      ) : !data?.rows?.length ? (
        <p className="py-8 text-center text-gray-400">{t("reports.noBacklog", { defaultValue: "Nothing open" })}</p>
      ) : (
        <BacklogTable rows={data.rows} columns={columns} />
      )}
    </div>
  );
}

/** Open purchase order lines with the supplier's confirmed date. */
export function PurchaseBacklogPage() {
  const { t } = useTranslation();
  usePageTitle(t("titles.purchase-backlog", { defaultValue: "Purchase Backlog" }));
  const [urlSupplier] = useUrlState<string>("supplier", "");
  const [urlItem] = useUrlState<string>("item_code", "");
  const [urlOverdue] = useUrlState<string>("overdue_only", "");
  const patchUrl = useUrlPatch();
  const [supplier, setSupplier] = useState(urlSupplier);
  const [item, setItem] = useState(urlItem);
  const [overdue, setOverdue] = useState(urlOverdue === "true");

  const filters = useMemo(() => {
    const f: Record<string, string> = {};
    if (urlSupplier) f.supplier = urlSupplier;
    if (urlItem) f.item_code = urlItem;
    if (urlOverdue === "true") f.overdue_only = "true";
    return f;
  }, [urlSupplier, urlItem, urlOverdue]);
  const { data, isLoading, refetch } = usePurchaseBacklog(filters);
  const apply = () => {
    patchUrl({ supplier: supplier || null, item_code: item || null, overdue_only: overdue ? "true" : null });
    refetch();
  };

  const columns: Column[] = [
    { key: "purchase_order", label: "Purchase Order", render: docLink("purchase-order", "purchase_order") },
    { key: "supplier_name", label: "Supplier" },
    { key: "item_code", label: "Item Code" },
    { key: "warehouse", label: "Warehouse" },
    { key: "qty", label: "Ordered", numeric: true },
    { key: "received_qty", label: "Received", numeric: true },
    { key: "open_qty", label: "Open", numeric: true },
    { key: "expected_date", label: "Expected",
      render: (row) => (
        <span className={row.overdue ? "font-medium text-red-600" : ""}>
          {row.expected_date ?? ""}{row.confirmed ? "" : row.expected_date ? " *" : ""}
        </span>
      ) },
    { key: "confirmation_reference", label: "Confirmation Ref." },
    { key: "days_overdue", label: "Days Overdue", numeric: true,
      render: (row) => (row.overdue ? formatNumber(row.days_overdue) : "") },
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-4">
        <LinkField label={t("fields.Supplier", { defaultValue: "Supplier" })} value={supplier} onChange={setSupplier} linkDoctype="supplier" readOnly={false} />
        <LinkField label={t("fields.Item Code", { defaultValue: "Item Code" })} value={item} onChange={setItem} linkDoctype="item" readOnly={false} />
        <label className="flex items-center gap-2 pb-2 text-sm">
          <input type="checkbox" checked={overdue} onChange={(e) => setOverdue(e.target.checked)} />
          {t("reports.onlyOverdue", { defaultValue: "Only overdue" })}
        </label>
        <Button onClick={apply}>{t("common.apply")}</Button>
      </div>
      {isLoading ? (
        <p className="text-gray-500">{t("common.loading")}</p>
      ) : !data?.rows?.length ? (
        <p className="py-8 text-center text-gray-400">{t("reports.noBacklog", { defaultValue: "Nothing open" })}</p>
      ) : (
        <>
          <BacklogTable rows={data.rows} columns={columns} />
          <p className="text-xs text-gray-500">
            {t("reports.unconfirmedNote", { defaultValue: "* not confirmed by the supplier: the order's schedule date" })}
          </p>
        </>
      )}
    </div>
  );
}
