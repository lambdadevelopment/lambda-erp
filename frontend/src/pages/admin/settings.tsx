import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useAuth } from "@/contexts/auth-context";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/select";

// Map window keys (as returned by /admin/demo-spend) to seconds so we can
// normalize spend to $/hour for cross-window comparison.
const DEMO_SPEND_WINDOWS: Array<{ value: string; label: string; hours: number }> = [
  { value: "1h", label: "Last 1 hour", hours: 1 },
  { value: "2h", label: "Last 2 hours", hours: 2 },
  { value: "4h", label: "Last 4 hours", hours: 4 },
  { value: "12h", label: "Last 12 hours", hours: 12 },
  { value: "24h", label: "Last 24 hours", hours: 24 },
  { value: "7d", label: "Last 7 days", hours: 24 * 7 },
];

function fmtUsd(v: number): string {
  if (v === 0) return "$0.00";
  if (v < 0.01) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(2)}`;
}

function DemoSpendCard() {
  const [windowKey, setWindowKey] = useState<string>("1h");
  const { data, isLoading, isFetching, error, refetch, dataUpdatedAt } = useQuery({
    queryKey: ["demo-spend"],
    queryFn: () => api.getDemoSpend(),
    // Refresh once a minute — demo spend changes slowly; no need to poll harder.
    refetchInterval: 60_000,
  });

  const windowSpec = DEMO_SPEND_WINDOWS.find((w) => w.value === windowKey)!;
  const window = data?.windows?.[windowKey];
  const demoCap = data?.caps?.global_hourly_usd ?? 0;
  // Average hourly demo spend over the selected range. This is the number that
  // matters for the cap (which is defined as $/hr), so normalizing by window
  // length lets the admin compare any range against the same threshold.
  const demoUsdPerHour = window ? window.demo_usd / windowSpec.hours : 0;
  const totalUsdPerHour = window ? window.total_usd / windowSpec.hours : 0;
  const capPct = demoCap > 0 ? Math.min(100, (demoUsdPerHour / demoCap) * 100) : 0;
  const capColor =
    capPct >= 90 ? "bg-red-500" : capPct >= 60 ? "bg-amber-500" : "bg-emerald-500";

  return (
    <Card title="Demo Spend">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="max-w-lg text-sm text-gray-600">
          Token spend from LLM calls in this deployment. Only{" "}
          <span className="font-medium text-gray-900">public_manager</span>{" "}
          traffic counts against the demo cap; admin/manager sessions are
          included in <em>Total</em> for visibility.
        </div>
        <div className="flex items-end gap-2">
          <div className="w-48">
            <Select
              label="Time range"
              options={DEMO_SPEND_WINDOWS.map((w) => ({ value: w.value, label: w.label }))}
              value={windowKey}
              onChange={(e) => setWindowKey(e.target.value)}
            />
          </div>
          <button
            type="button"
            onClick={() => refetch()}
            disabled={isFetching}
            title={
              dataUpdatedAt
                ? `Last updated ${new Date(dataUpdatedAt).toLocaleTimeString()}`
                : "Refresh now"
            }
            className="inline-flex h-[38px] items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 20 20"
              fill="currentColor"
              className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`}
            >
              <path
                fillRule="evenodd"
                d="M15.312 11.424a5.5 5.5 0 01-9.201 2.466l-.312-.311h2.433a.75.75 0 000-1.5H3.989a.75.75 0 00-.75.75v4.242a.75.75 0 001.5 0v-2.43l.31.31a7 7 0 0011.712-3.138.75.75 0 00-1.449-.39zm1.23-3.723a.75.75 0 00.219-.53V2.929a.75.75 0 00-1.5 0V5.36l-.31-.31A7 7 0 003.239 8.188a.75.75 0 101.448.389A5.5 5.5 0 0113.89 6.11l.311.31h-2.432a.75.75 0 000 1.5h4.243a.75.75 0 00.53-.219z"
                clipRule="evenodd"
              />
            </svg>
            {isFetching ? "Refreshing" : "Refresh"}
          </button>
        </div>
      </div>

      {isLoading && <p className="mt-4 text-sm text-gray-500">Loading spend data...</p>}
      {error && (
        <p className="mt-4 text-sm text-red-600">
          Failed to load spend data: {(error as Error).message}
        </p>
      )}

      {window && (
        <div className="mt-5 space-y-5">
          {/* Headline: demo $/hr vs cap */}
          <div>
            <div className="flex items-baseline justify-between">
              <div>
                <div className="text-xs font-medium uppercase tracking-wide text-gray-500">
                  Demo spend (avg/hr over {windowSpec.label.toLowerCase()})
                </div>
                <div className="mt-1 text-2xl font-semibold text-gray-900">
                  {fmtUsd(demoUsdPerHour)}
                  <span className="ml-2 text-sm font-normal text-gray-500">
                    / {fmtUsd(demoCap)} hourly cap
                  </span>
                </div>
              </div>
              <div className="text-right">
                <div className="text-xs font-medium uppercase tracking-wide text-gray-500">
                  Total (all roles)
                </div>
                <div className="mt-1 text-lg font-semibold text-gray-700">
                  {fmtUsd(window.total_usd)}
                  <span className="ml-1 text-xs font-normal text-gray-500">
                    ({fmtUsd(totalUsdPerHour)}/hr)
                  </span>
                </div>
              </div>
            </div>
            <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-gray-100">
              <div className={`h-full ${capColor} transition-all`} style={{ width: `${capPct}%` }} />
            </div>
            <div className="mt-1 text-xs text-gray-400">
              {capPct.toFixed(0)}% of the hourly cap
              {capPct >= 100 && " — demo visitors are currently blocked"}
            </div>
          </div>

          {/* Stats grid */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Demo spend" value={fmtUsd(window.demo_usd)} />
            <Stat label="Calls" value={window.call_count.toLocaleString()} />
            <Stat label="Unique IPs" value={window.unique_ips.toLocaleString()} />
            <Stat
              label="Tokens (in/out)"
              value={`${window.prompt_tokens.toLocaleString()} / ${window.completion_tokens.toLocaleString()}`}
            />
          </div>

          {/* Provider breakdown */}
          {Object.keys(window.by_provider).length > 0 && (
            <div>
              <div className="mb-2 text-xs font-medium uppercase tracking-wide text-gray-500">
                By provider
              </div>
              <div className="overflow-hidden rounded-md border border-gray-200">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
                    <tr>
                      <th className="px-3 py-2 text-left font-medium">Provider</th>
                      <th className="px-3 py-2 text-right font-medium">Cost</th>
                      <th className="px-3 py-2 text-right font-medium">Calls</th>
                      <th className="px-3 py-2 text-right font-medium">Prompt tok</th>
                      <th className="px-3 py-2 text-right font-medium">Completion tok</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {Object.entries(window.by_provider).map(([provider, row]) => (
                      <tr key={provider}>
                        <td className="px-3 py-2 font-medium text-gray-900">{provider}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{fmtUsd(row.cost_usd)}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{row.call_count.toLocaleString()}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{row.prompt_tokens.toLocaleString()}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{row.completion_tokens.toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Top IPs — fixed at 24h window regardless of selector, since that's
              what the backend returns and it's the most useful abuse-detection lens */}
          {data && data.top_ips_24h.length > 0 && (
            <div>
              <div className="mb-2 text-xs font-medium uppercase tracking-wide text-gray-500">
                Top 10 IPs (last 24 hours)
              </div>
              <div className="overflow-hidden rounded-md border border-gray-200">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
                    <tr>
                      <th className="px-3 py-2 text-left font-medium">IP</th>
                      <th className="px-3 py-2 text-left font-medium">Role</th>
                      <th className="px-3 py-2 text-right font-medium">Cost</th>
                      <th className="px-3 py-2 text-right font-medium">Calls</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {data.top_ips_24h.map((row, i) => (
                      <tr key={`${row.ip}-${row.role ?? ""}-${i}`}>
                        <td className="px-3 py-2 font-mono text-xs text-gray-900">{row.ip}</td>
                        <td className="px-3 py-2 text-gray-600">{row.role ?? "—"}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{fmtUsd(row.cost_usd)}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{row.call_count.toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-gray-50 px-3 py-2">
      <div className="text-xs font-medium uppercase tracking-wide text-gray-500">{label}</div>
      <div className="mt-0.5 text-sm font-semibold tabular-nums text-gray-900">{value}</div>
    </div>
  );
}

export default function SettingsPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const isAdmin = user?.role === "admin";

  const { data: settings, isLoading } = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.getSettings(),
  });

  const settingsMut = useMutation({
    mutationFn: (data: Record<string, string>) => api.updateSettings(data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings"] }),
  });

  const { data: pubStatus } = useQuery({
    queryKey: ["public-manager"],
    queryFn: () => api.getPublicManagerStatus(),
  });

  const [showConfirm, setShowConfirm] = useState(false);

  const createPubMut = useMutation({
    mutationFn: () => api.createPublicManager(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["public-manager"] });
      setShowConfirm(false);
    },
  });

  const removePubMut = useMutation({
    mutationFn: () => api.removePublicManager(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["public-manager"] }),
  });

  if (isLoading) return <p className="text-gray-500">Loading...</p>;

  return (
    <div className="space-y-6">
      <Card title="PDF & Print">
        <div className="flex flex-wrap items-end gap-4">
          {isAdmin ? (
            <Select
              label="Page Size"
              options={["A4", "letter"]}
              value={settings?.pdf_page_size || "A4"}
              onChange={(e) => settingsMut.mutate({ pdf_page_size: e.target.value })}
            />
          ) : (
            <div>
              <div className="text-xs font-medium text-gray-500 mb-1">Page Size</div>
              <div className="text-sm text-gray-900">{settings?.pdf_page_size || "A4"}</div>
            </div>
          )}
        </div>
        <p className="mt-2 text-xs text-gray-400">
          A4 is standard internationally (210 x 297 mm). Letter is standard in the US (8.5 x 11 in).
        </p>
      </Card>

      <Card title="Opening Balances">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-gray-700">
              The Opening Balances page allows importing account balances, stock, and outstanding invoices from a previous system.
            </p>
            <p className="mt-1 text-xs text-gray-400">
              {settings?.opening_balances_enabled === "1"
                ? "Currently enabled — accessible under Introduction > Opening Balances."
                : "Currently disabled — the page is hidden from the sidebar."}
            </p>
          </div>
          {isAdmin && (
            <button
              onClick={() => settingsMut.mutate({
                opening_balances_enabled: settings?.opening_balances_enabled === "1" ? "0" : "1",
              })}
              className={`ml-4 shrink-0 rounded-full px-4 py-1.5 text-sm font-medium ${
                settings?.opening_balances_enabled === "1"
                  ? "bg-red-50 text-red-700 hover:bg-red-100"
                  : "bg-green-50 text-green-700 hover:bg-green-100"
              }`}
            >
              {settings?.opening_balances_enabled === "1" ? "Disable" : "Enable"}
            </button>
          )}
        </div>
      </Card>
      {/* Public Manager / Demo Mode */}
      {isAdmin && (
        <Card title="Public Access (Demo Mode)">
          {pubStatus?.active ? (
            <div>
              <div className="flex items-center gap-2">
                <span className="inline-block h-2 w-2 rounded-full bg-green-500" />
                <span className="text-sm font-medium text-green-800">Active</span>
              </div>
              <p className="mt-2 text-sm text-gray-600">
                Public access is enabled. Anyone can use the application without logging in.
                All visitors get manager-level permissions.
              </p>
              <button
                onClick={() => removePubMut.mutate()}
                disabled={removePubMut.isPending}
                className="mt-3 rounded-lg bg-gray-100 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-200"
              >
                {removePubMut.isPending ? "Disabling..." : "Disable Public Access"}
              </button>
            </div>
          ) : (
            <div>
              <p className="text-sm text-gray-600">
                Enable public access to let anyone use the application without an account.
                Useful for demos and showcases. All visitors get manager-level permissions and share the same identity.
              </p>
              {!showConfirm ? (
                <button
                  onClick={() => setShowConfirm(true)}
                  className="mt-3 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
                >
                  Enable Public Access
                </button>
              ) : (
                <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-4">
                  <p className="text-sm font-medium text-red-800">Are you sure?</p>
                  <p className="mt-1 text-xs text-red-600">
                    This will allow anyone to access the application without logging in.
                    They will be able to create, edit, and submit documents. Your admin account
                    still requires login.
                  </p>
                  <div className="mt-3 flex gap-2">
                    <button
                      onClick={() => createPubMut.mutate()}
                      disabled={createPubMut.isPending}
                      className="rounded-lg bg-red-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-red-700"
                    >
                      {createPubMut.isPending ? "Enabling..." : "Yes, Enable"}
                    </button>
                    <button
                      onClick={() => setShowConfirm(false)}
                      className="rounded-lg bg-white px-4 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 border border-gray-300"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </Card>
      )}

      {isAdmin && <DemoSpendCard />}
    </div>
  );
}
