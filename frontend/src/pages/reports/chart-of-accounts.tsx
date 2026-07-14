import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useChartOfAccounts } from "@/hooks/use-report";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { formatCurrency as fmtCurrency } from "@/lib/utils";
import { useBaseCurrency } from "@/hooks/use-base-currency";

// The account tree grouped by root type, with period balances. Each leaf links
// to the General Ledger pre-filtered to that account + period — the GL page is
// URL-backed (?account=&from=&to=), so the drill-down needs no extra plumbing.

interface CoaRow {
  name: string;
  account_name: string | null;
  parent_account: string | null;
  root_type: string | null;
  account_type: string | null;
  is_group: boolean;
  disabled: boolean;
  balance: number;
  has_entries: boolean;
}

const ROOT_ORDER = ["Asset", "Liability", "Equity", "Income", "Expense"];

export default function ChartOfAccountsPage() {
  const { t } = useTranslation();
  const currentYear = new Date().getFullYear();
  const [year, setYear] = useState(String(currentYear));
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const yearOptions = useMemo(() => {
    const opts: { value: string; label: string }[] = [];
    for (let y = currentYear; y >= currentYear - 6; y--) {
      opts.push({ value: String(y), label: String(y) });
    }
    opts.push({ value: "all", label: t("coa.allTime", { defaultValue: "All time" }) });
    return opts;
  }, [currentYear, t]);

  const filters = useMemo(() => {
    const f: Record<string, string> = {};
    if (year !== "all") {
      f.from_date = `${year}-01-01`;
      f.to_date = `${year}-12-31`;
    }
    return f;
  }, [year]);

  const { data, isLoading } = useChartOfAccounts(filters);
  const baseCurrency = useBaseCurrency("");
  const fmt = (v: number) => fmtCurrency(v, baseCurrency);

  const accounts: CoaRow[] = data?.accounts ?? [];

  // parent -> children, and the roots of each root_type section. An account
  // whose parent is missing from the result set is treated as a root.
  const { childrenOf, rootsByType } = useMemo(() => {
    const byName = new Map(accounts.map((a) => [a.name, a]));
    const childrenOf = new Map<string, CoaRow[]>();
    const rootsByType = new Map<string, CoaRow[]>();
    for (const acc of accounts) {
      if (acc.parent_account && byName.has(acc.parent_account)) {
        const list = childrenOf.get(acc.parent_account) ?? [];
        list.push(acc);
        childrenOf.set(acc.parent_account, list);
      } else {
        const key = acc.root_type ?? "Other";
        const list = rootsByType.get(key) ?? [];
        list.push(acc);
        rootsByType.set(key, list);
      }
    }
    return { childrenOf, rootsByType };
  }, [accounts]);

  const toggle = (name: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const glLink = (name: string) => {
    const params = new URLSearchParams({ account: name });
    if (year !== "all") {
      params.set("from", `${year}-01-01`);
      params.set("to", `${year}-12-31`);
    }
    return `/reports/general-ledger?${params.toString()}`;
  };

  const renderRow = (acc: CoaRow, depth: number): React.ReactNode => {
    const kids = childrenOf.get(acc.name) ?? [];
    const isCollapsed = collapsed.has(acc.name);
    const dim = !acc.has_entries || acc.disabled;
    return (
      <div key={acc.name}>
        <div
          className={`flex items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-surface-subtle ${dim ? "text-fg-muted" : "text-fg"}`}
          style={{ paddingLeft: `${8 + depth * 20}px` }}
        >
          {kids.length > 0 ? (
            <button
              type="button"
              onClick={() => toggle(acc.name)}
              className="shrink-0 text-fg-muted transition-colors hover:text-fg"
            >
              {isCollapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
            </button>
          ) : (
            <span className="w-3.5 shrink-0" />
          )}
          <span className={`min-w-0 flex-1 truncate ${acc.is_group ? "font-medium" : ""}`}>
            {acc.is_group ? (
              <>{acc.account_name || acc.name}</>
            ) : (
              <Link to={glLink(acc.name)} className="hover:text-brand hover:underline">
                <span className="font-mono text-xs text-fg-muted">{acc.name}</span>{" "}
                {acc.account_name || acc.name}
              </Link>
            )}
            {acc.disabled && (
              <span className="ml-2 text-xs text-fg-muted">
                {t("coa.disabled", { defaultValue: "disabled" })}
              </span>
            )}
          </span>
          {acc.account_type && (
            <span className="hidden shrink-0 text-xs text-fg-muted sm:inline">{acc.account_type}</span>
          )}
          <span className="w-32 shrink-0 text-right font-mono text-sm tabular-nums">
            {acc.has_entries || acc.is_group ? fmt(acc.balance) : "—"}
          </span>
        </div>
        {!isCollapsed && kids.map((kid) => renderRow(kid, depth + 1))}
      </div>
    );
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-4">
        <div className="w-40">
          <Select
            label={t("coa.period", { defaultValue: "Period" })}
            options={yearOptions}
            value={year}
            onChange={(e) => setYear(e.target.value)}
          />
        </div>
        <p className="pb-2 text-xs text-fg-muted">
          {t("coa.semantics", {
            defaultValue:
              "Balance-sheet accounts: closing balance at period end. P&L accounts: movement within the period. Click an account for its ledger.",
          })}
        </p>
      </div>

      {isLoading && (
        <Card>
          <p className="text-sm text-fg-muted">{t("common.loading", { defaultValue: "Loading…" })}</p>
        </Card>
      )}

      {!isLoading && accounts.length === 0 && (
        <Card>
          <p className="text-sm text-fg-muted">
            {t("coa.empty", { defaultValue: "No accounts found. Complete the company setup first." })}
          </p>
        </Card>
      )}

      {!isLoading &&
        ROOT_ORDER.filter((rt) => (rootsByType.get(rt) ?? []).length > 0).map((rootType) => (
          <Card key={rootType}>
            <h3 className="mb-2 px-2 text-xs font-semibold uppercase tracking-wider text-fg-muted">
              {t(`coa.rootType.${rootType}`, { defaultValue: rootType })}
            </h3>
            <div>{(rootsByType.get(rootType) ?? []).map((acc) => renderRow(acc, 0))}</div>
          </Card>
        ))}
    </div>
  );
}
