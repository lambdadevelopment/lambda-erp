import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, Loader2, RefreshCw, Undo2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  api,
  type BankInvoiceSuggestion,
  type BankReconciliationSuggestions,
  type BankReconciliationTransaction,
  type BankVoucherSuggestion,
} from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { useConfirm } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { usePageTitle } from "@/lib/use-page-title";


type LedgerAccount = {
  name: string;
  account_name: string;
  company: string;
  root_type: string;
  account_type?: string | null;
  account_currency?: string | null;
  is_group?: number;
  disabled?: number;
};


const suggestionKey = (item: BankInvoiceSuggestion) =>
  `${item.reference_doctype}:${item.reference_name}`;


function voucherPath(type: string, name: string) {
  return `/app/${type.toLowerCase().replace(/\s+/g, "-")}/${encodeURIComponent(name)}`;
}


export default function BankReconciliation() {
  const { t, i18n } = useTranslation();
  const confirm = useConfirm();
  usePageTitle(t("bankReconciliation.title"));
  const [status, setStatus] = useState("Unreconciled");
  const [rows, setRows] = useState<BankReconciliationTransaction[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<BankReconciliationSuggestions | null>(null);
  const [accounts, setAccounts] = useState<LedgerAccount[]>([]);
  const [allocations, setAllocations] = useState<Record<string, number>>({});
  const [journalAccount, setJournalAccount] = useState("");
  const [journalRemarks, setJournalRemarks] = useState("");
  const [conversionRate, setConversionRate] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const money = (value: number, currency: string) => {
    try {
      return new Intl.NumberFormat(i18n.language, { style: "currency", currency }).format(value);
    } catch {
      return `${currency} ${value.toFixed(2)}`;
    }
  };

  const loadQueue = async (preferred?: string | null) => {
    const response = await api.listBankReconciliationTransactions(status, 200);
    setRows(response.rows);
    const next = preferred && response.rows.some((row) => row.name === preferred)
      ? preferred
      : response.rows[0]?.name || null;
    setSelected(next);
    if (!next) setSuggestions(null);
  };

  useEffect(() => {
    setError("");
    loadQueue().catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [status]);

  useEffect(() => {
    api.listMasters("account", {
      limit: 500,
      fields: "name,account_name,company,root_type,account_type,account_currency,is_group,disabled",
    }).then((response) => setAccounts(response.rows as LedgerAccount[]))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setSuggestions(null);
    setAllocations({});
    setJournalAccount("");
    setJournalRemarks("");
    setConversionRate("");
    setError("");
    api.getBankReconciliationSuggestions(selected)
      .then(setSuggestions)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [selected]);

  const transaction = suggestions?.transaction;
  const selectedInvoices = useMemo(() => {
    if (!suggestions) return [];
    return suggestions.invoices.filter((item) => allocations[suggestionKey(item)] != null);
  }, [suggestions, allocations]);
  const allocatedTotal = selectedInvoices.reduce(
    (sum, item) => sum + Number(allocations[suggestionKey(item)] || 0), 0,
  );
  const firstInvoice = selectedInvoices[0];
  const compatibleInvoice = (item: BankInvoiceSuggestion) => !firstInvoice
    || (item.party_type === firstInvoice.party_type
      && item.party === firstInvoice.party
      && item.payment_type === firstInvoice.payment_type);

  const journalAccounts = useMemo(() => {
    if (!transaction) return [];
    const blockedTypes = new Set(["Bank", "Cash", "Receivable", "Payable"]);
    return accounts.filter((account) =>
      account.company === transaction.company
      && account.name !== transaction.bank_account
      && !Number(account.is_group)
      && !Number(account.disabled)
      && !blockedTypes.has(account.account_type || ""),
    );
  }, [accounts, transaction]);

  const afterMutation = async (message: string) => {
    setNotice(message);
    await loadQueue(selected);
  };

  const runExisting = async (candidate: BankVoucherSuggestion) => {
    if (!transaction) return;
    const group = candidate.bank_transactions || [];
    const grouped = candidate.kind === "existing_voucher_group" && group.length > 1;
    const ok = await confirm({
      title: t("bankReconciliation.confirmExistingTitle"),
      body: grouped ? (
        <div className="space-y-2">
          <p>{t("bankReconciliation.confirmGroupBody", {
            count: group.length,
            amount: money(candidate.amount, candidate.currency),
            voucher: `${candidate.voucher_type} ${candidate.voucher_no}`,
          })}</p>
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {group.map((member) => (
              <li key={member.name}>
                {member.name} · {member.deposit ? "+" : "−"}{money(member.amount, candidate.currency)}
              </li>
            ))}
          </ul>
        </div>
      ) : t("bankReconciliation.confirmExistingBody", {
          transaction: transaction.name,
          voucher: `${candidate.voucher_type} ${candidate.voucher_no}`,
        }),
      confirmLabel: t("bankReconciliation.match"),
    });
    if (!ok) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await api.reconcileBankExistingVoucher({
        bank_transaction: transaction.name,
        bank_transactions: grouped ? group.map((member) => member.name) : undefined,
        voucher_type: candidate.voucher_type,
        voucher_no: candidate.voucher_no,
        confirmed: true,
      });
      await afterMutation(grouped
        ? t("bankReconciliation.groupReconciledNotice", { count: group.length })
        : t("bankReconciliation.reconciledNotice"));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally { setBusy(false); }
  };

  const runPayment = async () => {
    if (!transaction || selectedInvoices.length === 0) return;
    const remainder = Math.max(0, transaction.amount - allocatedTotal);
    const ok = await confirm({
      title: t("bankReconciliation.confirmPaymentTitle"),
      body: (
        <div className="space-y-2">
          <p>{t("bankReconciliation.confirmPaymentBody", {
            transaction: transaction.name,
            amount: money(transaction.amount, transaction.currency),
            count: selectedInvoices.length,
          })}</p>
          {remainder > 0.009 && (
            <p className="font-medium text-amber-700">
              {t("bankReconciliation.onAccountWarning", { amount: money(remainder, transaction.currency) })}
            </p>
          )}
        </div>
      ),
      confirmLabel: t("bankReconciliation.postAndReconcile"),
    });
    if (!ok) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await api.reconcileBankPayment({
        bank_transaction: transaction.name,
        allocations: selectedInvoices.map((item) => ({
          reference_doctype: item.reference_doctype,
          reference_name: item.reference_name,
          allocated_amount: Number(allocations[suggestionKey(item)]),
        })),
        conversion_rate: conversionRate ? Number(conversionRate) : undefined,
        confirmed: true,
      });
      await afterMutation(t("bankReconciliation.reconciledNotice"));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally { setBusy(false); }
  };

  const runJournal = async () => {
    if (!transaction || !journalAccount) return;
    const ok = await confirm({
      title: t("bankReconciliation.confirmJournalTitle"),
      body: t("bankReconciliation.confirmJournalBody", {
        transaction: transaction.name,
        amount: money(transaction.amount, transaction.currency),
        account: journalAccount,
      }),
      confirmLabel: t("bankReconciliation.postAndReconcile"),
    });
    if (!ok) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await api.reconcileBankJournal({
        bank_transaction: transaction.name,
        counterparty_account: journalAccount,
        remarks: journalRemarks || undefined,
        conversion_rate: conversionRate ? Number(conversionRate) : undefined,
        confirmed: true,
      });
      await afterMutation(t("bankReconciliation.reconciledNotice"));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally { setBusy(false); }
  };

  const runUndo = async () => {
    if (!transaction || !suggestions?.active_reconciliation) return;
    const created = suggestions.active_reconciliation.mode !== "Existing Voucher";
    const activeGroup = suggestions.active_reconciliation.bank_transactions || [transaction.name];
    const ok = await confirm({
      title: t("bankReconciliation.undoTitle"),
      body: created
        ? t("bankReconciliation.undoCreatedBody")
        : activeGroup.length > 1
          ? t("bankReconciliation.undoExistingGroupBody", {
              count: activeGroup.length,
              transactions: activeGroup.join(", "),
            })
          : t("bankReconciliation.undoExistingBody"),
      confirmLabel: t("bankReconciliation.undo"),
      danger: true,
    });
    if (!ok) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await api.undoBankReconciliation(transaction.name);
      await afterMutation(t("bankReconciliation.undoneNotice"));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally { setBusy(false); }
  };

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-4 sm:p-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-fg">{t("bankReconciliation.title")}</h1>
          <p className="mt-1 text-sm text-fg-muted">{t("bankReconciliation.subtitle")}</p>
        </div>
        <div className="flex items-end gap-2">
          <Select
            label={t("bankReconciliation.status")}
            value={status}
            options={[
              { value: "Unreconciled", label: t("bankReconciliation.unreconciled") },
              { value: "Reconciled", label: t("bankReconciliation.reconciled") },
              { value: "All", label: t("common.all", { defaultValue: "All" }) },
            ]}
            onChange={(event) => setStatus(event.target.value)}
          />
          <Button variant="secondary" onClick={() => loadQueue(selected)} disabled={busy}>
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {error && <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
      {notice && <div className="flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800"><CheckCircle2 className="h-4 w-4" />{notice}</div>}

      <div className="grid gap-6 lg:grid-cols-[minmax(280px,0.8fr)_minmax(0,1.7fr)]">
        <Card className="self-start">
          <h2 className="mb-3 text-base font-semibold text-fg">{t("bankReconciliation.transactions")}</h2>
          <div className="max-h-[70vh] space-y-1 overflow-y-auto pr-1">
            {rows.map((row) => (
              <button
                key={row.name}
                type="button"
                onClick={() => setSelected(row.name)}
                className={cn(
                  "w-full rounded-lg px-3 py-2.5 text-left transition",
                  selected === row.name ? "bg-brand/10 ring-1 ring-brand/20" : "hover:bg-surface-subtle",
                )}
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs text-fg-muted">{row.posting_date}</span>
                  <span className={cn("text-sm font-semibold tabular-nums", row.deposit ? "text-emerald-700" : "text-fg")}>{row.deposit ? "+" : "−"}{money(row.amount, row.currency)}</span>
                </div>
                <p className="mt-1 truncate text-sm font-medium text-fg">{row.counterparty_name || row.description || row.name}</p>
                <p className="truncate text-xs text-fg-muted">{row.remittance_information || row.reference_number || row.name}</p>
              </button>
            ))}
            {!rows.length && <p className="py-6 text-center text-sm text-fg-muted">{t("bankReconciliation.empty")}</p>}
          </div>
        </Card>

        {!selected ? null : !suggestions ? (
          <Card className="flex min-h-48 items-center justify-center"><Loader2 className="h-5 w-5 animate-spin text-brand" /></Card>
        ) : (
          <div className="space-y-6">
            <Card>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="font-semibold text-fg">{transaction?.name}</h2>
                  <p className="mt-1 text-sm text-fg-muted">{transaction?.counterparty_name || "—"} · {transaction?.posting_date}</p>
                </div>
                {transaction && <span className="text-lg font-semibold tabular-nums">{transaction.deposit ? "+" : "−"}{money(transaction.amount, transaction.currency)}</span>}
              </div>
              <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
                <div><dt className="text-fg-muted">{t("bankReconciliation.remittance")}</dt><dd>{transaction?.remittance_information || transaction?.description || "—"}</dd></div>
                <div><dt className="text-fg-muted">{t("bankReconciliation.reference")}</dt><dd className="break-all">{transaction?.structured_reference || transaction?.reference_number || "—"}</dd></div>
              </dl>
              {transaction && transaction.currency !== transaction.base_currency && (
                <div className="mt-4 max-w-xs">
                  <Input
                    label={t("bankReconciliation.conversionRate", {
                      currency: transaction.currency,
                      base: transaction.base_currency,
                    })}
                    type="number"
                    min="0.000001"
                    step="0.000001"
                    value={conversionRate}
                    onChange={(event) => setConversionRate(event.target.value)}
                    hint={t("bankReconciliation.conversionRateHint")}
                  />
                </div>
              )}
              <Link className="mt-3 inline-block text-sm text-brand underline" to={`/app/bank-transaction/${encodeURIComponent(transaction!.name)}`}>{t("bankReconciliation.openTransaction")}</Link>
            </Card>

            {suggestions.active_reconciliation ? (
              <Card>
                <h2 className="text-base font-semibold text-fg">{t("bankReconciliation.activeMatch")}</h2>
                <p className="mt-2 text-sm text-fg-muted">{suggestions.active_reconciliation.mode}</p>
                {suggestions.active_reconciliation.bank_transactions?.length > 1 && (
                  <p className="mt-1 text-sm text-fg-muted">
                    {suggestions.active_reconciliation.bank_transactions.join(" + ")}
                  </p>
                )}
                <Link className="mt-1 inline-block text-sm text-brand underline" to={voucherPath(suggestions.active_reconciliation.voucher_type, suggestions.active_reconciliation.voucher_no)}>
                  {suggestions.active_reconciliation.voucher_type} {suggestions.active_reconciliation.voucher_no}
                </Link>
                <div className="mt-4">
                  <Button variant="danger" disabled={busy} onClick={runUndo}><Undo2 className="h-4 w-4" />{t("bankReconciliation.undo")}</Button>
                </div>
              </Card>
            ) : (
              <>
                {suggestions.existing_vouchers.length > 0 && (
                  <Card>
                    <h2 className="text-base font-semibold text-fg">{t("bankReconciliation.existingVouchers")}</h2>
                    <p className="mt-1 text-sm text-fg-muted">{t("bankReconciliation.existingVouchersHint")}</p>
                    <div className="mt-3 divide-y divide-line">
                      {suggestions.existing_vouchers.map((item) => (
                        <div key={`${item.voucher_type}:${item.voucher_no}`} className="flex flex-wrap items-center justify-between gap-3 py-3">
                          <div>
                            <Link className="font-medium text-brand underline" to={voucherPath(item.voucher_type, item.voucher_no)}>{item.voucher_type} {item.voucher_no}</Link>
                            <p className="text-xs text-fg-muted">
                              {item.posting_date} · {money(item.amount, item.currency)}
                              {item.kind === "existing_voucher_group" && ` · ${t("bankReconciliation.groupedTransactions", { count: item.bank_transactions.length })}`}
                            </p>
                            {item.kind === "existing_voucher_group" && (
                              <p className="mt-1 text-xs text-fg-muted">
                                {item.bank_transactions.map((member) => member.name).join(" + ")}
                              </p>
                            )}
                          </div>
                          <Button variant="secondary" disabled={busy} onClick={() => runExisting(item)}>{t("bankReconciliation.match")}</Button>
                        </div>
                      ))}
                    </div>
                  </Card>
                )}

                <Card>
                  <h2 className="text-base font-semibold text-fg">{t("bankReconciliation.openInvoices")}</h2>
                  <p className="mt-1 text-sm text-fg-muted">{t("bankReconciliation.openInvoicesHint")}</p>
                  <div className="mt-3 divide-y divide-line">
                    {suggestions.invoices.map((item) => {
                      const key = suggestionKey(item);
                      const checked = allocations[key] != null;
                      const compatible = compatibleInvoice(item);
                      return (
                        <div key={key} className={cn("grid gap-3 py-3 sm:grid-cols-[auto_1fr_150px] sm:items-center", !compatible && !checked && "opacity-45")}>
                          <input
                            type="checkbox"
                            checked={checked}
                            disabled={!compatible && !checked}
                            onChange={(event) => setAllocations((old) => {
                              const next = { ...old };
                              if (event.target.checked) next[key] = item.suggested_allocation;
                              else delete next[key];
                              return next;
                            })}
                            className="h-4 w-4 rounded border-line text-brand"
                          />
                          <div>
                            <Link className="font-medium text-brand underline" to={voucherPath(item.reference_doctype, item.reference_name)}>{item.reference_doctype} {item.reference_name}</Link>
                            <p className="text-sm text-fg">{item.party_name}</p>
                            <p className="text-xs text-fg-muted">{item.posting_date} · {t("bankReconciliation.outstanding")}: {money(item.outstanding_amount, item.currency)}</p>
                          </div>
                          <Input
                            type="number" min="0.01" step="0.01"
                            disabled={!checked}
                            value={checked ? allocations[key] : ""}
                            onChange={(event) => setAllocations((old) => ({ ...old, [key]: Number(event.target.value) }))}
                            aria-label={t("bankReconciliation.allocation")}
                          />
                        </div>
                      );
                    })}
                    {!suggestions.invoices.length && <p className="py-4 text-sm text-fg-muted">{t("bankReconciliation.noInvoiceSuggestions")}</p>}
                  </div>
                  {selectedInvoices.length > 0 && transaction && (
                    <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
                      <p className="text-sm text-fg-muted">{t("bankReconciliation.allocated", { amount: money(allocatedTotal, transaction.currency), total: money(transaction.amount, transaction.currency) })}</p>
                      <Button disabled={busy || allocatedTotal <= 0 || allocatedTotal > transaction.amount + 0.01} onClick={runPayment}>{t("bankReconciliation.createPayment")}</Button>
                    </div>
                  )}
                </Card>

                <Card>
                  <h2 className="text-base font-semibold text-fg">{t("bankReconciliation.directPosting")}</h2>
                  <p className="mt-1 text-sm text-fg-muted">{t("bankReconciliation.directPostingHint")}</p>
                  <div className="mt-4 grid gap-3 sm:grid-cols-2">
                    <Select
                      label={t("bankReconciliation.counterpartyAccount")}
                      value={journalAccount}
                      options={journalAccounts.map((account) => ({ value: account.name, label: `${account.account_name} · ${account.name}` }))}
                      onChange={(event) => setJournalAccount(event.target.value)}
                    />
                    <Input label={t("bankReconciliation.remarks")} value={journalRemarks} onChange={(event) => setJournalRemarks(event.target.value)} />
                  </div>
                  <div className="mt-4"><Button disabled={busy || !journalAccount || Boolean(transaction && transaction.currency !== transaction.base_currency && !conversionRate)} onClick={runJournal}>{t("bankReconciliation.createJournal")}</Button></div>
                </Card>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
