// "Addresses of this customer" on a draft sales document: the customer's saved
// addresses and the ones earlier documents used that are not on record (typed
// for one order), each with buttons to use it as delivery or invoice address.
// A saved address is linked; an earlier one-off is copied as the printed text.
// Backed by the list_customer_addresses action (lambda_erp/addresses.py).
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { api } from "@/api/client";

interface SavedAddress {
  address: string;
  label?: string | null;
  text: string;
  for_invoices: boolean;
  for_deliveries: boolean;
  default_invoice: boolean;
  default_delivery: boolean;
  disabled: boolean;
  instructions?: string | null;
}

interface UsedAddress {
  text: string;
  times: number;
  last_used?: string | null;
  last_document?: { doctype: string; name: string } | null;
}

interface AddressBook {
  saved: SavedAddress[];
  used_delivery_addresses: UsedAddress[];
  used_invoice_addresses: UsedAddress[];
}

type Kind = "shipping" | "billing";

export function CustomerAddresses({ customer, current, onUse }: {
  customer: string;
  current: { shipping?: string | null; billing?: string | null };
  onUse: (kind: Kind, link: string | null, text: string) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const book = useQuery({
    queryKey: ["customer-addresses", customer],
    queryFn: () => api.runAction("list_customer_addresses", { customer }) as Promise<AddressBook>,
    enabled: open && !!customer,
  });
  const lines = (text: string) => text.split("\n").filter(Boolean);
  const same = (a?: string | null, b?: string | null) =>
    (a ?? "").replace(/\s+/g, " ").trim().toLowerCase() === (b ?? "").replace(/\s+/g, " ").trim().toLowerCase();

  const useButtons = (link: string | null, text: string, delivery: boolean, invoice: boolean) => (
    <div className="mt-2 flex flex-wrap gap-2">
      {delivery && (
        <Button variant="secondary" disabled={same(current.shipping, text)} onClick={() => onUse("shipping", link, text)}>
          {same(current.shipping, text)
            ? t("customerAddresses.isDelivery", { defaultValue: "Current delivery address" })
            : t("customerAddresses.useDelivery", { defaultValue: "Use as delivery address" })}
        </Button>
      )}
      {invoice && (
        <Button variant="ghost" disabled={same(current.billing, text)} onClick={() => onUse("billing", link, text)}>
          {same(current.billing, text)
            ? t("customerAddresses.isInvoice", { defaultValue: "Current invoice address" })
            : t("customerAddresses.useInvoice", { defaultValue: "Use as invoice address" })}
        </Button>
      )}
    </div>
  );

  return (
    <Card title={t("customerAddresses.title", { defaultValue: "Addresses of this customer" })}>
      {!open ? (
        <Button variant="secondary" onClick={() => setOpen(true)}>
          {t("customerAddresses.show", { defaultValue: "Show saved and earlier addresses" })}
        </Button>
      ) : book.isLoading ? (
        <p className="text-sm text-gray-500">{t("common.loading")}</p>
      ) : !book.data ? (
        <p className="text-sm text-red-600">{String(book.error ?? "")}</p>
      ) : (
        <div className="space-y-5 text-sm">
          <section>
            <h4 className="mb-2 font-medium">{t("customerAddresses.saved", { defaultValue: "Saved addresses" })}</h4>
            {!book.data.saved.length && (
              <p className="text-gray-500">{t("customerAddresses.noneSaved", { defaultValue: "No saved addresses." })}</p>
            )}
            <div className="grid gap-3 md:grid-cols-2">
              {book.data.saved.filter((a) => !a.disabled).map((a) => (
                <div key={a.address} className="rounded-lg p-3 ring-1 ring-line">
                  <div className="font-medium">
                    {a.label || a.address}
                    {a.default_delivery && <span className="ml-2 text-xs text-fg-muted">{t("customerAddresses.defaultDelivery", { defaultValue: "default delivery" })}</span>}
                    {a.default_invoice && <span className="ml-2 text-xs text-fg-muted">{t("customerAddresses.defaultInvoice", { defaultValue: "default invoice" })}</span>}
                  </div>
                  {lines(a.text).map((line, i) => <div key={i} className="text-gray-700">{line}</div>)}
                  {a.instructions && <div className="mt-1 text-xs text-fg-muted">{a.instructions}</div>}
                  {useButtons(a.address, a.text, a.for_deliveries, a.for_invoices)}
                </div>
              ))}
            </div>
          </section>
          {([["used_delivery_addresses", "customerAddresses.usedDelivery", "Delivery addresses used before (not saved)"],
             ["used_invoice_addresses", "customerAddresses.usedInvoice", "Invoice addresses used before (not saved)"]] as const)
            .map(([key, label, fallback]) => !!book.data![key].length && (
              <section key={key}>
                <h4 className="mb-2 font-medium">{t(label, { defaultValue: fallback })}</h4>
                <div className="grid gap-3 md:grid-cols-2">
                  {book.data![key].map((a, idx) => (
                    <div key={idx} className="rounded-lg p-3 ring-1 ring-line">
                      {lines(a.text).map((line, i) => <div key={i} className="text-gray-700">{line}</div>)}
                      <div className="mt-1 text-xs text-fg-muted">
                        {t("customerAddresses.usedTimes", {
                          defaultValue: "{{count}}× used, last {{date}} ({{document}})",
                          count: a.times, date: a.last_used ?? "–", document: a.last_document?.name ?? "",
                        })}
                      </div>
                      {useButtons(null, a.text, key === "used_delivery_addresses", key === "used_invoice_addresses")}
                    </div>
                  ))}
                </div>
              </section>
            ))}
          <p className="text-xs text-fg-muted">
            {t("customerAddresses.hint", {
              defaultValue: "A new one-off address: type it into “Delivery Address (as printed)”. Department and delivery / STOP code belong in the address text so they print on the delivery note.",
            })}
          </p>
        </div>
      )}
    </Card>
  );
}
