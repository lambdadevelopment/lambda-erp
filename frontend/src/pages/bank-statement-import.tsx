import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle2, FileUp, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { api, type BankStatementImportResult, type BankStatementPreview } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { usePageTitle } from "@/lib/use-page-title";


interface BankAccountOption {
  name: string;
  account_name: string;
  company: string;
  account: string;
  iban: string;
  currency: string;
  disabled?: number;
}


function maskIban(value: string) {
  const compact = (value || "").replace(/\s/g, "");
  return compact.length > 8 ? `${compact.slice(0, 4)} … ${compact.slice(-4)}` : compact;
}


export default function BankStatementImport() {
  const { t, i18n } = useTranslation();
  usePageTitle(t("bankStatements.title"));
  const [files, setFiles] = useState<File[]>([]);
  const [statements, setStatements] = useState<BankStatementPreview[]>([]);
  const [bankAccounts, setBankAccounts] = useState<BankAccountOption[]>([]);
  const [mappings, setMappings] = useState<Record<string, string>>({});
  const [history, setHistory] = useState<any[]>([]);
  const [busy, setBusy] = useState<"preview" | "import" | null>(null);
  const [error, setError] = useState("");
  const [result, setResult] = useState<BankStatementImportResult | null>(null);

  const reloadReferenceData = async () => {
    const [accounts, imports] = await Promise.all([
      api.listDocuments("bank-account", { limit: 500, fields: "name,account_name,company,account,iban,currency,disabled" }),
      api.listBankStatementImports(20),
    ]);
    setBankAccounts(accounts.rows.filter((row) => !Number(row.disabled)));
    setHistory(imports.rows);
  };

  useEffect(() => {
    reloadReferenceData().catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  const money = (value: string | number | null, currency: string) => {
    if (value == null || value === "") return "—";
    try {
      return new Intl.NumberFormat(i18n.language, { style: "currency", currency }).format(Number(value));
    } catch {
      return `${currency} ${value}`;
    }
  };

  const activeStatements = statements.filter((statement) => !statement.already_imported);
  const canImport = activeStatements.length > 0
    && activeStatements.every((statement) => Boolean(mappings[statement.statement_key]));
  const selectedNames = useMemo(
    () => files.map((file) => file.name).join(", "),
    [files],
  );

  const preview = async () => {
    if (!files.length) return;
    setBusy("preview");
    setError("");
    setResult(null);
    try {
      const response = await api.previewBankStatements(files);
      setStatements(response.statements);
      const detected: Record<string, string> = {};
      response.statements.forEach((statement) => {
        if (statement.matched_bank_account) {
          detected[statement.statement_key] = statement.matched_bank_account.name;
        }
      });
      setMappings(detected);
    } catch (err) {
      setStatements([]);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const runImport = async () => {
    setBusy("import");
    setError("");
    try {
      const response = await api.importBankStatements(files, mappings);
      setResult(response);
      await reloadReferenceData();
      const refreshed = await api.previewBankStatements(files);
      setStatements(refreshed.statements);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 sm:p-6">
      <div>
        <h1 className="text-2xl font-semibold text-fg">{t("bankStatements.title")}</h1>
        <p className="mt-1 text-sm text-fg-muted">{t("bankStatements.subtitle")}</p>
      </div>

      <Card>
        <h2 className="mb-4 text-base font-semibold text-fg">{t("bankStatements.selectFiles")}</h2>
        <div className="space-y-4">
          <label className="flex min-h-28 cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed border-line bg-surface-subtle px-4 py-6 text-center transition-colors hover:border-brand/50 hover:bg-brand/5">
            <FileUp className="mb-2 h-7 w-7 text-brand" />
            <span className="text-sm font-medium text-fg">
              {selectedNames || t("bankStatements.chooseFiles")}
            </span>
            <span className="mt-1 text-xs text-fg-muted">{t("bankStatements.fileHint")}</span>
            <input
              className="sr-only"
              type="file"
              multiple
              accept=".xml,.zip,application/xml,text/xml,application/zip,application/x-zip-compressed"
              onChange={(event) => {
                setFiles(Array.from(event.target.files || []));
                setStatements([]);
                setMappings({});
                setResult(null);
                setError("");
              }}
            />
          </label>
          <div className="flex flex-wrap items-center gap-3">
            <Button disabled={!files.length || busy !== null} onClick={preview}>
              {busy === "preview" && <Loader2 className="h-4 w-4 animate-spin" />}
              {t("bankStatements.preview")}
            </Button>
            <span className="text-xs text-fg-muted">{t("bankStatements.noPosting")}</span>
          </div>
        </div>
      </Card>

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {statements.map((statement) => (
        <Card key={statement.statement_key}>
          <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-fg">{statement.account_iban_masked} · {statement.account_currency}</h2>
                <p className="mt-1 text-xs text-fg-muted">{statement.file_name}</p>
              </div>
              {statement.already_imported && (
                <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700">
                  <CheckCircle2 className="h-3.5 w-3.5" /> {t("bankStatements.alreadyImported")}
                </span>
              )}
          </div>
          <div className="mt-5 space-y-5">
            <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm md:grid-cols-4">
              <div><dt className="text-fg-muted">{t("bankStatements.period")}</dt><dd className="font-medium">{statement.from_date} – {statement.to_date}</dd></div>
              <div><dt className="text-fg-muted">{t("bankStatements.openingBalance")}</dt><dd className="font-medium tabular-nums">{money(statement.opening_balance, statement.account_currency)}</dd></div>
              <div><dt className="text-fg-muted">{t("bankStatements.closingBalance")}</dt><dd className="font-medium tabular-nums">{money(statement.closing_balance, statement.account_currency)}</dd></div>
              <div><dt className="text-fg-muted">{t("bankStatements.entries")}</dt><dd className="font-medium tabular-nums">{statement.booked_entry_count}</dd></div>
              <div><dt className="text-fg-muted">{t("bankStatements.credits")}</dt><dd className="font-medium tabular-nums text-emerald-700">{money(statement.credit_total, statement.account_currency)}</dd></div>
              <div><dt className="text-fg-muted">{t("bankStatements.debits")}</dt><dd className="font-medium tabular-nums">{money(statement.debit_total, statement.account_currency)}</dd></div>
              <div><dt className="text-fg-muted">{t("bankStatements.details")}</dt><dd className="font-medium tabular-nums">{statement.detail_count} ({statement.batch_entry_count} {t("bankStatements.batches")})</dd></div>
              <div><dt className="text-fg-muted">{t("bankStatements.qrReferences")}</dt><dd className="font-medium tabular-nums">{statement.qr_reference_count}</dd></div>
            </dl>

            {!statement.already_imported && (
              <div>
                <label className="mb-1.5 block text-sm font-medium text-fg">
                  {t("bankStatements.mapping")}
                </label>
                <select
                  className="h-10 w-full max-w-xl rounded-lg bg-surface px-3 text-sm text-fg ring-1 ring-line focus:outline-none focus:ring-2 focus:ring-brand/30"
                  value={mappings[statement.statement_key] || ""}
                  onChange={(event) => setMappings((old) => ({ ...old, [statement.statement_key]: event.target.value }))}
                >
                  <option value="">{t("bankStatements.selectBankAccount")}</option>
                  {bankAccounts.map((account) => (
                    <option key={account.name} value={account.name}>
                      {account.account_name} · {maskIban(account.iban)} · {account.currency} · {account.company}
                    </option>
                  ))}
                </select>
                {!bankAccounts.length && (
                  <p className="mt-2 text-sm text-amber-700">
                    {t("bankStatements.noBankAccounts")} {" "}
                    <Link className="font-medium underline" to="/app/bank-account/new">
                      {t("bankStatements.createBankAccount")}
                    </Link>
                  </p>
                )}
              </div>
            )}

            {statement.warnings.length > 0 && (
              <div className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
                {statement.warnings.map((warning) => (
                  <div key={warning} className="flex gap-2"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{warning}</div>
                ))}
              </div>
            )}
          </div>
        </Card>
      ))}

      {statements.length > 0 && (
        <div className="flex flex-wrap items-center gap-3">
          <Button disabled={!canImport || busy !== null} onClick={runImport}>
            {busy === "import" && <Loader2 className="h-4 w-4 animate-spin" />}
            {t("bankStatements.import")}
          </Button>
          {!canImport && activeStatements.length > 0 && (
            <span className="text-sm text-amber-700">{t("bankStatements.mappingRequired")}</span>
          )}
        </div>
      )}

      {result && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          <div className="flex items-center gap-2 font-medium"><CheckCircle2 className="h-4 w-4" />{t("bankStatements.importComplete")}</div>
          {result.imports.map((item) => (
            <p key={item.name} className="mt-1">
              {item.name}: {t("bankStatements.importCounts", { imported: item.imported_entry_count, duplicates: item.duplicate_entry_count })}
            </p>
          ))}
        </div>
      )}

      {history.length > 0 && (
        <Card>
          <h2 className="mb-4 text-base font-semibold text-fg">{t("bankStatements.history")}</h2>
          <div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-line text-xs text-fg-muted">
                  <tr>
                    <th className="px-2 py-2 font-medium">{t("bankStatements.importId")}</th>
                    <th className="px-2 py-2 font-medium">{t("bankStatements.bankAccount")}</th>
                    <th className="px-2 py-2 font-medium">{t("bankStatements.period")}</th>
                    <th className="px-2 py-2 text-right font-medium">{t("bankStatements.entries")}</th>
                    <th className="px-2 py-2 font-medium">{t("bankStatements.source")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {history.map((item) => (
                    <tr key={item.name}>
                      <td className="px-2 py-2 font-medium">{item.name}</td>
                      <td className="px-2 py-2">{item.bank_account_name || item.bank_account}<span className="ml-2 text-xs text-fg-muted">{item.account_iban_masked}</span></td>
                      <td className="px-2 py-2">{item.from_date} – {item.to_date}</td>
                      <td className="px-2 py-2 text-right tabular-nums">{item.imported_entry_count}</td>
                      <td className="px-2 py-2"><a className="text-brand underline" href={api.bankStatementSourceUrl(item.name)}>{item.source_filename}</a></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
