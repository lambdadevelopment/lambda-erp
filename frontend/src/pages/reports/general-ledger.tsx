import { useState, useMemo } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useGeneralLedger } from "@/hooks/use-report";
import { useUrlState, useUrlPatch } from "@/hooks/use-url-state";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { LinkField } from "@/components/document/link-field";
import { DateRangePresets } from "@/components/ui/date-range-presets";
import { formatCurrency as fmtCurrency, formatDate } from "@/lib/utils";
import { useBaseCurrency } from "@/hooks/use-base-currency";

/** Convert a voucher type like "Sales Invoice" to a URL slug "sales-invoice". */
function toSlug(voucherType: string): string {
  return voucherType.toLowerCase().replace(/\s+/g, "-");
}

const PAGE_SIZE_OPTIONS = ["25", "50", "100", "200"];

export default function GeneralLedgerPage() {
  const { t } = useTranslation();
  // URL-backed filter and pagination state. Param names match the backend
  // where it's natural (account, party) and use human-friendly short forms
  // for pagination and dates (page, per_page, from, to).
  const [urlAccount] = useUrlState<string>("account", "");
  const [urlParty] = useUrlState<string>("party", "");
  const [urlFromDate] = useUrlState<string>("from", "");
  const [urlToDate] = useUrlState<string>("to", "");
  const [pageSize] = useUrlState<number>("per_page", 50);
  const [urlPage] = useUrlState<number>("page", 1);
  const page = urlPage - 1;
  const patchUrl = useUrlPatch();
  const setPage = (p: number) => patchUrl({ page: p === 0 ? null : p + 1 });
  const setPageSize = (n: number) => patchUrl({ per_page: n, page: null });

  // The input fields let the user type/pick without firing the query on
  // every keystroke — they only flush on Apply / preset click. Seed from the
  // URL so a cold open with filters pre-populates the inputs too.
  const [account, setAccount] = useState(urlAccount);
  const [party, setParty] = useState(urlParty);
  const [fromDate, setFromDate] = useState(urlFromDate);
  const [toDate, setToDate] = useState(urlToDate);
  const baseCurrency = useBaseCurrency();
  const formatCurrency = (v: number | null | undefined) => fmtCurrency(v, baseCurrency);

  const filters = useMemo(() => {
    const f: Record<string, string> = {};
    if (urlAccount) f.account = urlAccount;
    if (urlParty) f.party = urlParty;
    if (urlFromDate) f.from_date = urlFromDate;
    if (urlToDate) f.to_date = urlToDate;
    f.limit = String(pageSize);
    f.offset = String(page * pageSize);
    return f;
  }, [urlAccount, urlParty, urlFromDate, urlToDate, pageSize, page]);

  const { data, isLoading, refetch } = useGeneralLedger(filters);

  const rows = data?.rows ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const rangeStart = total === 0 ? 0 : page * pageSize + 1;
  const rangeEnd = Math.min(total, (page + 1) * pageSize);

  const handleApply = () => {
    patchUrl({
      account: account || null,
      party: party || null,
      from: fromDate || null,
      to: toDate || null,
      page: null,
    });
    refetch();
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-4">
        <LinkField
          label={t("fields.Account", { defaultValue: "Account" })}
          value={account}
          onChange={setAccount}
          linkDoctype="account"
          readOnly={false}
        />
        <LinkField
          label={t("fields.Party", { defaultValue: "Party" })}
          value={party}
          onChange={setParty}
          linkDoctype="customer"
          readOnly={false}
        />
        <Input
          label={t("fields.From Date", { defaultValue: "From Date" })}
          type="date"
          value={fromDate}
          onChange={(e) => setFromDate(e.target.value)}
        />
        <Input
          label={t("fields.To Date", { defaultValue: "To Date" })}
          type="date"
          value={toDate}
          onChange={(e) => setToDate(e.target.value)}
        />
        <Button onClick={handleApply}>{t("common.apply")}</Button>
      </div>
      <DateRangePresets onSelect={(from, to) => {
        setFromDate(from);
        setToDate(to);
        patchUrl({ from, to, page: null });
      }} />

      {isLoading ? (
        <p className="text-gray-500">{t("common.loading")}</p>
      ) : rows.length === 0 ? (
        <p className="py-8 text-center text-gray-400">{t("reports.noEntries")}</p>
      ) : (
        <>
          <Card>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200 text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left font-medium text-gray-500">
                      {t("fields.Date", { defaultValue: "Date" })}
                    </th>
                    <th className="px-4 py-3 text-left font-medium text-gray-500">
                      {t("fields.Account", { defaultValue: "Account" })}
                    </th>
                    <th className="px-4 py-3 text-left font-medium text-gray-500">
                      {t("fields.Party", { defaultValue: "Party" })}
                    </th>
                    <th className="px-4 py-3 text-right font-medium text-gray-500">
                      {t("fields.Debit", { defaultValue: "Debit" })}
                    </th>
                    <th className="px-4 py-3 text-right font-medium text-gray-500">
                      {t("fields.Credit", { defaultValue: "Credit" })}
                    </th>
                    <th className="px-4 py-3 text-right font-medium text-gray-500">
                      {t("fields.Balance", { defaultValue: "Balance" })}
                    </th>
                    <th className="px-4 py-3 text-left font-medium text-gray-500">
                      {t("fields.Voucher Type", { defaultValue: "Voucher Type" })}
                    </th>
                    <th className="px-4 py-3 text-left font-medium text-gray-500">
                      {t("fields.Voucher No", { defaultValue: "Voucher No" })}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {rows.map((row: any, idx: number) => (
                    <tr key={idx} className="hover:bg-gray-50">
                      <td className="px-4 py-2 text-gray-500">
                        {formatDate(row.posting_date ?? row.date)}
                      </td>
                      <td className="px-4 py-2 text-gray-900">{row.account}</td>
                      <td className="px-4 py-2 text-gray-700">
                        {row.party ?? "-"}
                      </td>
                      <td className="px-4 py-2 text-right">
                        {formatCurrency(row.debit)}
                      </td>
                      <td className="px-4 py-2 text-right">
                        {formatCurrency(row.credit)}
                      </td>
                      <td className="px-4 py-2 text-right font-medium">
                        {formatCurrency(row.balance)}
                      </td>
                      <td className="px-4 py-2 text-gray-700">
                        {row.voucher_type ?? "-"}
                      </td>
                      <td className="px-4 py-2">
                        {row.voucher_type && row.voucher_no ? (
                          <Link
                            to={`/app/${toSlug(row.voucher_type)}/${row.voucher_no}`}
                            className="font-medium text-blue-600 hover:text-blue-800"
                          >
                            {row.voucher_no}
                          </Link>
                        ) : (
                          row.voucher_no ?? "-"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-gray-600">
            <div>
              {t("common.showing")} <span className="font-medium text-gray-900">{rangeStart}–{rangeEnd}</span>{" "}
              {t("common.of")} <span className="font-medium text-gray-900">{total}</span>
            </div>
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1.5">
                <span className="text-xs text-gray-500">{t("common.perPage")}</span>
                <select
                  value={pageSize}
                  onChange={(e) => setPageSize(Number(e.target.value))}
                  className="rounded border border-gray-300 bg-white px-2 py-1 text-sm"
                >
                  {PAGE_SIZE_OPTIONS.map((n) => (
                    <option key={n} value={n}>{n}</option>
                  ))}
                </select>
              </label>
              <button
                onClick={() => setPage(Math.max(0, page - 1))}
                disabled={page === 0}
                className="rounded border border-gray-300 bg-white px-3 py-1 text-sm text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {t("common.prev")}
              </button>
              <span className="text-xs">
                {t("common.page")} <span className="font-medium text-gray-900">{page + 1}</span> {t("common.of")}{" "}
                <span className="font-medium text-gray-900">{totalPages}</span>
              </span>
              <button
                onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
                disabled={page >= totalPages - 1}
                className="rounded border border-gray-300 bg-white px-3 py-1 text-sm text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {t("common.next")}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
