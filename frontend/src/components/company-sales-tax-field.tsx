import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { request } from "@/api/client";

type TaxTemplate = { name: string; title: string | null };

export function CompanySalesTaxField({ company, value, onChange }: {
  company?: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  const templates = useQuery({
    queryKey: ["company-sales-tax-templates", company],
    queryFn: () => request<TaxTemplate[]>(
      `/masters/company/${encodeURIComponent(company!)}/sales-tax-templates`,
    ),
    enabled: !!company,
  });
  const options = templates.data ?? [];
  const unknown = !!value && !options.some((option) => option.name === value);

  return (
    <div className="sm:col-span-2">
      <label htmlFor="company-sales-tax" className="mb-1.5 block text-sm font-medium text-fg">
        {t("companyTax.label")}
      </label>
      <select
        id="company-sales-tax"
        aria-describedby="company-sales-tax-hint"
        className="block h-10 w-full rounded-lg bg-surface px-3 text-sm text-fg ring-1 ring-line focus:outline-none focus:ring-2 focus:ring-brand/30 disabled:cursor-not-allowed disabled:text-fg-muted"
        value={value}
        disabled={!company || templates.isPending || templates.isError}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">{t("companyTax.none")}</option>
        {unknown && <option value={value}>{value}</option>}
        {options.map((option) => (
          <option key={option.name} value={option.name}>{option.title || option.name}</option>
        ))}
      </select>
      <p id="company-sales-tax-hint" className="mt-2 text-xs text-fg-muted">
        {t(company ? "companyTax.hint" : "companyTax.saveFirst")}
      </p>
      {company && templates.isPending && (
        <p className="mt-1 text-xs text-fg-muted" role="status">{t("common.loading")}</p>
      )}
      {templates.isError && (
        <p className="mt-2 text-sm text-rose-700" role="alert">
          {t("companyTax.loadError")}{" "}
          <button type="button" className="underline" onClick={() => templates.refetch()}>
            {t("companyTax.retry")}
          </button>
        </p>
      )}
      {company && templates.isSuccess && unknown && (
        <p className="mt-2 text-sm text-amber-700" role="alert">{t("companyTax.unavailable")}</p>
      )}
      {company && templates.isSuccess && options.length === 0 && (
        <p className="mt-2 text-xs text-fg-muted">{t("companyTax.empty")}</p>
      )}
    </div>
  );
}
