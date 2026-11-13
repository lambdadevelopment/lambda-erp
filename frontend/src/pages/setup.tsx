import { useTranslation } from "react-i18next";
import { useState } from "react";
import { usePageTitle } from "@/lib/use-page-title";
import { Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";

const CURRENCY_OPTIONS = ["USD", "EUR", "GBP", "CHF", "JPY", "CAD", "AUD", "CNY", "INR"];

type SeedMode = "none" | "quick" | "history";

export default function SetupPage() {
  const { t } = useTranslation();
  usePageTitle(t("setupPage.title"));
  const queryClient = useQueryClient();

  const { data: status, isLoading } = useQuery({
    queryKey: ["setup-status"],
    queryFn: () => api.setupStatus(),
  });

  const [companyName, setCompanyName] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [details, setDetails] = useState({
    address: "",
    city: "",
    zip_code: "",
    country: "",
    email: "",
    phone: "",
    tax_id: "",
  });
  const setDetail = (key: keyof typeof details) => (e: { target: { value: string } }) =>
    setDetails((d) => ({ ...d, [key]: e.target.value }));
  const [seedMode, setSeedMode] = useState<SeedMode>("quick");
  const [success, setSuccess] = useState(false);
  const [historyStats, setHistoryStats] = useState<Record<string, number> | null>(null);

  const createCompanyMut = useMutation({
    mutationFn: () => api.createCompany({ name: companyName, currency, ...details }),
  });

  const seedDemoMut = useMutation({
    mutationFn: () => api.seedDemo(),
  });

  const seedHistoryMut = useMutation({
    mutationFn: () => api.seedHistory(),
  });

  const handleSetup = async () => {
    if (!companyName.trim()) return;
    try {
      await createCompanyMut.mutateAsync();
      if (seedMode === "history") {
        const res = await seedHistoryMut.mutateAsync();
        setHistoryStats(res.stats);
      } else if (seedMode === "quick") {
        await seedDemoMut.mutateAsync();
      }
      // seedMode === "none": create the company only, seed nothing.
      queryClient.invalidateQueries({ queryKey: ["setup-status"] });
      setSuccess(true);
    } catch {
      // errors handled by mutation state
    }
  };

  if (isLoading) {
    return <p className="text-gray-500">{t("common.loading")}</p>;
  }

  // Setup already complete -- show existing companies
  if (status?.setup_complete && !success) {
    return (
      <div className="space-y-6">
        <Card title={t("setupPage.companies")}>
          {status.companies && status.companies.length > 0 ? (
            <ul className="divide-y divide-gray-100">
              {status.companies.map((company: any, idx: number) => (
                <li key={idx} className="py-3">
                  <CompanyRow company={company} />
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-gray-400">{t("setupPage.noCompanies")}</p>
          )}
        </Card>
        <Link to="/" className="text-blue-600 hover:text-blue-800 text-sm font-medium">
          {t("setupPage.toDashboard")}
        </Link>
      </div>
    );
  }

  // Success state after setup
  if (success) {
    return (
      <div className="space-y-6">
        <Card>
          <div className="py-8 text-center">
            <p className="text-lg font-semibold text-green-700">
              {t("setupPage.complete")}
            </p>
            <p className="mt-2 text-sm text-gray-500">
              {seedMode === "history"
                ? t("setupPage.doneHistory")
                : seedMode === "quick"
                  ? t("setupPage.doneQuick")
                  : t("setupPage.doneEmpty")}
            </p>
            {historyStats && (
              <div className="mx-auto mt-6 max-w-sm text-left">
                <p className="mb-2 text-sm font-medium text-gray-700">{t("setupPage.generated")}</p>
                <ul className="divide-y divide-gray-100 text-sm">
                  {Object.entries(historyStats)
                    .sort(([a], [b]) => a.localeCompare(b))
                    .map(([k, v]) => (
                      <li key={k} className="flex justify-between py-1">
                        <span className="text-gray-600">{k.replace(/_/g, " ")}</span>
                        <span className="font-mono text-gray-900">{v.toLocaleString()}</span>
                      </li>
                    ))}
                </ul>
              </div>
            )}
            <div className="mt-6">
              <Link to="/">
                <Button>{t("setupPage.toDashboard")}</Button>
              </Link>
            </div>
          </div>
        </Card>
      </div>
    );
  }

  // First-run setup form
  const isPending =
    createCompanyMut.isPending || seedDemoMut.isPending || seedHistoryMut.isPending;
  const error = createCompanyMut.error ?? seedDemoMut.error ?? seedHistoryMut.error;

  return (
    <div className="mx-auto max-w-lg space-y-6">
      <p className="text-sm text-gray-500">
        {t("setupPage.intro")}
      </p>

      {error && (
        <div className="rounded-md bg-red-50 p-4 text-sm text-red-700">
          {error.message ?? t("setupPage.error")}
        </div>
      )}

      <Card>
        <div className="space-y-4">
          <Input
            label={t("setupPage.companyName")}
            value={companyName}
            onChange={(e) => setCompanyName(e.target.value)}
            placeholder={t("setupPage.companyPlaceholder")}
            required
          />
          <Select
            label={t("fields.Currency", { defaultValue: "Currency" })}
            options={CURRENCY_OPTIONS}
            value={currency}
            onChange={(e) => setCurrency(e.target.value)}
          />

          <fieldset className="space-y-3">
            <legend className="mb-1 text-sm font-medium text-gray-700">
              {t("setupPage.details")}
            </legend>
            <p className="text-xs text-gray-500">
              {t("setupPage.detailsHelp")}
            </p>
            <Input label={t("fields.Address", { defaultValue: "Address" })} value={details.address} onChange={setDetail("address")}
              placeholder={t("setupPage.addressPlaceholder")} />
            <div className="grid grid-cols-2 gap-3">
              <Input label={t("fields.City", { defaultValue: "City" })} value={details.city} onChange={setDetail("city")} />
              <Input label={t("setupPage.zip")} value={details.zip_code} onChange={setDetail("zip_code")} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Input label={t("fields.Country", { defaultValue: "Country" })} value={details.country} onChange={setDetail("country")} />
              <Input label={t("setupPage.taxId")} value={details.tax_id} onChange={setDetail("tax_id")} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Input label={t("fields.Email", { defaultValue: "Email" })} value={details.email} onChange={setDetail("email")} />
              <Input label={t("fields.Phone", { defaultValue: "Phone" })} value={details.phone} onChange={setDetail("phone")} />
            </div>
          </fieldset>

          <fieldset className="space-y-2">
            <legend className="mb-1 text-sm font-medium text-gray-700">
              {t("setupPage.seed")}
            </legend>
            <label className="flex cursor-pointer items-start gap-3 rounded-md border border-gray-200 p-3 hover:bg-gray-50">
              <input
                type="radio"
                name="seed-mode"
                value="quick"
                checked={seedMode === "quick"}
                onChange={() => setSeedMode("quick")}
                className="mt-1"
              />
              <div>
                <div className="text-sm font-medium text-gray-900">{t("setupPage.quick")}</div>
                <div className="text-xs text-gray-500">
                  {t("setupPage.quickHelp")}
                </div>
              </div>
            </label>
            <label className="flex cursor-pointer items-start gap-3 rounded-md border border-gray-200 p-3 hover:bg-gray-50">
              <input
                type="radio"
                name="seed-mode"
                value="history"
                checked={seedMode === "history"}
                onChange={() => setSeedMode("history")}
                className="mt-1"
              />
              <div>
                <div className="text-sm font-medium text-gray-900">
                  {t("setupPage.history")}
                </div>
                <div className="text-xs text-gray-500">
                  {t("setupPage.historyHelp")}
                </div>
              </div>
            </label>
            <label className="flex cursor-pointer items-start gap-3 rounded-md border border-gray-200 p-3 hover:bg-gray-50">
              <input
                type="radio"
                name="seed-mode"
                value="none"
                checked={seedMode === "none"}
                onChange={() => setSeedMode("none")}
                className="mt-1"
              />
              <div>
                <div className="text-sm font-medium text-gray-900">
                  {t("setupPage.empty")}
                </div>
                <div className="text-xs text-gray-500">
                  {t("setupPage.emptyHelp")}
                </div>
              </div>
            </label>
          </fieldset>

          <div className="pt-2">
            <Button
              onClick={handleSetup}
              disabled={isPending || !companyName.trim()}
            >
              {isPending
                ? seedMode === "history"
                  ? t("setupPage.simulating")
                  : t("setupPage.settingUp")
                : seedMode === "history"
                  ? t("setupPage.createHistory")
                  : seedMode === "quick"
                    ? t("setupPage.createQuick")
                    : t("setupPage.create")}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}

function CompanyRow({ company }: { company: Record<string, any> }) {
  const { t } = useTranslation();
  const cityCountry = [
    [company.city, company.zip_code].filter(Boolean).join(" "),
    company.country,
  ].filter(Boolean).join(", ");
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0 space-y-1">
        <div className="font-medium text-gray-900">
          {company.company_name || company.name}
        </div>
        <dl className="grid grid-cols-[auto,1fr] gap-x-3 gap-y-0.5 text-xs text-gray-500">
          {company.address && (
            <>
              <dt className="text-gray-400">{t("fields.Address", { defaultValue: "Address" })}</dt>
              <dd className="text-gray-700">{company.address}</dd>
            </>
          )}
          {cityCountry && (
            <>
              <dt className="text-gray-400">{t("setupPage.location")}</dt>
              <dd className="text-gray-700">{cityCountry}</dd>
            </>
          )}
          {company.email && (
            <>
              <dt className="text-gray-400">{t("fields.Email", { defaultValue: "Email" })}</dt>
              <dd className="truncate text-gray-700">
                <a
                  href={`mailto:${company.email}`}
                  className="text-blue-600 hover:text-blue-800"
                >
                  {company.email}
                </a>
              </dd>
            </>
          )}
          {company.phone && (
            <>
              <dt className="text-gray-400">{t("fields.Phone", { defaultValue: "Phone" })}</dt>
              <dd className="text-gray-700">{company.phone}</dd>
            </>
          )}
          {company.tax_id && (
            <>
              <dt className="text-gray-400">{t("fields.Tax ID", { defaultValue: "Tax ID" })}</dt>
              <dd className="font-mono text-gray-700">{company.tax_id}</dd>
            </>
          )}
        </dl>
      </div>
      {company.default_currency && (
        <span className="shrink-0 rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-600">
          {company.default_currency}
        </span>
      )}
    </div>
  );
}
