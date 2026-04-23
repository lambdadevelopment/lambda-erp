import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";

const CURRENCY_OPTIONS = ["USD", "EUR", "GBP", "INR"];

type SeedMode = "quick" | "history";

export default function SetupPage() {
  const queryClient = useQueryClient();

  const { data: status, isLoading } = useQuery({
    queryKey: ["setup-status"],
    queryFn: () => api.setupStatus(),
  });

  const [companyName, setCompanyName] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [seedMode, setSeedMode] = useState<SeedMode>("quick");
  const [success, setSuccess] = useState(false);
  const [historyStats, setHistoryStats] = useState<Record<string, number> | null>(null);

  const createCompanyMut = useMutation({
    mutationFn: () => api.createCompany({ name: companyName, currency }),
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
      } else {
        await seedDemoMut.mutateAsync();
      }
      queryClient.invalidateQueries({ queryKey: ["setup-status"] });
      setSuccess(true);
    } catch {
      // errors handled by mutation state
    }
  };

  if (isLoading) {
    return <p className="text-gray-500">Loading...</p>;
  }

  // Setup already complete -- show existing companies
  if (status?.setup_complete && !success) {
    return (
      <div className="space-y-6">
        <Card title="Companies">
          {status.companies && status.companies.length > 0 ? (
            <ul className="divide-y divide-gray-100">
              {status.companies.map((company: any, idx: number) => (
                <li key={idx} className="py-3">
                  <CompanyRow company={company} />
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-gray-400">No companies found</p>
          )}
        </Card>
        <Link to="/" className="text-blue-600 hover:text-blue-800 text-sm font-medium">
          Go to Dashboard
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
              Setup complete!
            </p>
            <p className="mt-2 text-sm text-gray-500">
              {historyStats
                ? "Your company has been created and three years of history has been simulated."
                : "Your company has been created and demo data has been seeded."}
            </p>
            {historyStats && (
              <div className="mx-auto mt-6 max-w-sm text-left">
                <p className="mb-2 text-sm font-medium text-gray-700">Generated documents:</p>
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
                <Button>Go to Dashboard</Button>
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
        Set up your first company to get started.
      </p>

      {error && (
        <div className="rounded-md bg-red-50 p-4 text-sm text-red-700">
          {error.message ?? "An error occurred during setup"}
        </div>
      )}

      <Card>
        <div className="space-y-4">
          <Input
            label="Company Name"
            value={companyName}
            onChange={(e) => setCompanyName(e.target.value)}
            placeholder="e.g. My Company Ltd"
            required
          />
          <Select
            label="Currency"
            options={CURRENCY_OPTIONS}
            value={currency}
            onChange={(e) => setCurrency(e.target.value)}
          />

          <fieldset className="space-y-2">
            <legend className="mb-1 text-sm font-medium text-gray-700">
              Seed data
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
                <div className="text-sm font-medium text-gray-900">Quick demo</div>
                <div className="text-xs text-gray-500">
                  Customers, suppliers, items, one warehouse. No transactions.
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
                  Simulate 3 years of history
                </div>
                <div className="text-xs text-gray-500">
                  Walks business days (skips weekends + US holidays) and generates
                  quotations, orders, deliveries, invoices, payments, and
                  reorder-driven purchasing. Seasonality + YoY growth baked in.
                  Takes ~3 minutes.
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
                  ? "Simulating three years..."
                  : "Setting up..."
                : seedMode === "history"
                  ? "Create Company & Simulate History"
                  : "Create Company & Seed Demo Data"}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}

function CompanyRow({ company }: { company: Record<string, any> }) {
  const cityCountry = [company.city, company.country].filter(Boolean).join(", ");
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0 space-y-1">
        <div className="font-medium text-gray-900">
          {company.company_name || company.name}
        </div>
        <dl className="grid grid-cols-[auto,1fr] gap-x-3 gap-y-0.5 text-xs text-gray-500">
          {company.address && (
            <>
              <dt className="text-gray-400">Address</dt>
              <dd className="text-gray-700">{company.address}</dd>
            </>
          )}
          {cityCountry && (
            <>
              <dt className="text-gray-400">Location</dt>
              <dd className="text-gray-700">{cityCountry}</dd>
            </>
          )}
          {company.email && (
            <>
              <dt className="text-gray-400">Email</dt>
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
              <dt className="text-gray-400">Phone</dt>
              <dd className="text-gray-700">{company.phone}</dd>
            </>
          )}
          {company.tax_id && (
            <>
              <dt className="text-gray-400">Tax ID</dt>
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
