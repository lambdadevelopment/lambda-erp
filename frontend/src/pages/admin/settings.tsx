import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "@/api/client";
import { useAuth } from "@/contexts/auth-context";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { LanguageSelect } from "@/components/ui/language-select";

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

function TokenSpendCard({ demoActive }: { demoActive: boolean }) {
  const { t } = useTranslation();
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
    <Card title={t("settings.tokenSpendTitle")}>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="max-w-lg text-sm text-gray-600">
          Cost and token usage from AI / LLM calls in this deployment, across
          all providers.
          {demoActive && (
            <>
              {" "}Only{" "}
              <span className="font-medium text-gray-900">public_manager</span>{" "}
              (demo) traffic counts against the demo cap; admin/manager sessions
              are included in the total.
            </>
          )}
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
            {isFetching ? t("common.refreshing") : t("common.refresh")}
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
          {demoActive ? (
            /* Demo deployments: spend-against-cap is the number that matters. */
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
          ) : (
            /* Normal deployments: total LLM spend is the useful headline. */
            <div>
              <div className="text-xs font-medium uppercase tracking-wide text-gray-500">
                Total spend (avg/hr over {windowSpec.label.toLowerCase()})
              </div>
              <div className="mt-1 text-2xl font-semibold text-gray-900">
                {fmtUsd(totalUsdPerHour)}
                <span className="ml-2 text-sm font-normal text-gray-500">
                  ({fmtUsd(window.total_usd)} over {windowSpec.label.toLowerCase()})
                </span>
              </div>
            </div>
          )}

          {/* Stats grid */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {demoActive ? (
              <Stat label="Demo spend" value={fmtUsd(window.demo_usd)} />
            ) : (
              <Stat label="Cost" value={fmtUsd(window.total_usd)} />
            )}
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

function ChangePasswordCard() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  const mut = useMutation({
    mutationFn: () => api.authChangePassword(current, next),
    onSuccess: () => {
      setDone(true);
      setError("");
      setCurrent("");
      setNext("");
      setConfirm("");
      setTimeout(() => setDone(false), 4000);
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : t("login.registrationFailed")),
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setDone(false);
    if (next !== confirm) {
      setError(t("login.passwordsNoMatch"));
      return;
    }
    if (next.length < 6) {
      setError(t("login.passwordTooShort"));
      return;
    }
    mut.mutate();
  };

  return (
    <Card title={t("settings.passwordTitle")}>
      <form onSubmit={submit} className="max-w-sm space-y-3">
        {/* Hidden username anchor: lets password managers (1Password, etc.)
            associate the saved login so they fill/save against the right
            account. 1Password recommends a real text input hidden with
            display:none rather than the `hidden` attribute. */}
        <input
          type="text"
          name="username"
          id="username"
          autoComplete="username"
          value={user?.email || ""}
          readOnly
          tabIndex={-1}
          aria-hidden="true"
          style={{ display: "none" }}
        />
        <Input
          label={t("settings.currentPassword")}
          id="current-password"
          name="current-password"
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          required
        />
        <Input
          label={t("settings.newPassword")}
          id="new-password"
          name="new-password"
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          required
        />
        <Input
          label={t("settings.confirmNewPassword")}
          id="confirm-password"
          name="confirm-password"
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          required
        />
        {error && <p className="text-sm text-red-600">{error}</p>}
        {done && <p className="text-sm text-green-700">{t("settings.passwordChanged")}</p>}
        <Button type="submit" disabled={mut.isPending || !current || !next || !confirm}>
          {mut.isPending ? t("settings.changingPassword") : t("settings.changePassword")}
        </Button>
      </form>
    </Card>
  );
}

function SetPasswordCard() {
  const { t } = useTranslation();
  const { user, refreshUser } = useAuth();
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  const mut = useMutation({
    mutationFn: () => api.authSetPassword(next),
    onSuccess: async () => {
      setDone(true);
      setError("");
      setNext("");
      setConfirm("");
      // Flip the card to "Change Password" now that a password exists.
      await refreshUser();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : t("login.registrationFailed")),
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setDone(false);
    if (next !== confirm) {
      setError(t("login.passwordsNoMatch"));
      return;
    }
    if (next.length < 6) {
      setError(t("login.passwordTooShort"));
      return;
    }
    mut.mutate();
  };

  return (
    <Card title={t("settings.setPasswordTitle")}>
      <p className="mb-3 max-w-lg text-sm text-gray-600">{t("settings.setPasswordBody")}</p>
      <form onSubmit={submit} className="max-w-sm space-y-3">
        {/* Hidden username anchor so password managers save against the account. */}
        <input
          type="text"
          name="username"
          autoComplete="username"
          value={user?.email || ""}
          readOnly
          tabIndex={-1}
          aria-hidden="true"
          style={{ display: "none" }}
        />
        <Input
          label={t("settings.newPassword")}
          id="new-password"
          name="new-password"
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          required
        />
        <Input
          label={t("settings.confirmNewPassword")}
          id="confirm-password"
          name="confirm-password"
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          required
        />
        {error && <p className="text-sm text-red-600">{error}</p>}
        {done && <p className="text-sm text-green-700">{t("settings.passwordSet")}</p>}
        <Button type="submit" disabled={mut.isPending || !next || !confirm}>
          {mut.isPending ? t("settings.changingPassword") : t("settings.setPassword")}
        </Button>
      </form>
    </Card>
  );
}

function LinkedAccountsCard() {
  const { t } = useTranslation();

  const { data: setup } = useQuery({
    queryKey: ["auth-setup-status"],
    queryFn: () => api.authSetupStatus(),
  });
  const { data: identities } = useQuery({
    queryKey: ["oauth-identities"],
    queryFn: () => api.oauthListIdentities(),
  });

  const providers = setup?.oauth_providers ?? [];
  if (providers.length === 0) return null; // no social login configured on this deployment

  const linked = new Set((identities ?? []).map((i) => i.provider));

  return (
    <Card title={t("settings.linkedAccountsTitle")}>
      <p className="text-sm text-gray-600">{t("settings.linkedAccountsBody")}</p>
      <div className="mt-3 space-y-2">
        {providers.map((p) => {
          const label = p.charAt(0).toUpperCase() + p.slice(1);
          const isLinked = linked.has(p);
          return (
            <div key={p} className="flex items-center justify-between rounded-md border border-gray-200 px-3 py-2">
              <span className="text-sm font-medium text-gray-900">{label}</span>
              {isLinked ? (
                <span className="inline-flex items-center gap-1.5 text-sm text-green-700">
                  <span className="inline-block h-2 w-2 rounded-full bg-green-500" />
                  {t("settings.linked")}
                </span>
              ) : (
                <button
                  type="button"
                  onClick={() => { window.location.href = api.oauthLoginUrl(p, { link: true }); }}
                  className="rounded-md border border-gray-300 bg-white px-3 py-1 text-sm font-medium text-gray-700 hover:bg-gray-50"
                >
                  {t("settings.linkProvider", { provider: label })}
                </button>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

const ROLE_RANK: Record<string, number> = { viewer: 1, manager: 2, admin: 3 };

// Self-service API keys: every key belongs to its creator and can never act
// above the owner's live role (the picked role is only a CAP). Non-admins see
// and manage their own keys; admins see everyone's.
function ApiKeysSection({ ownRole }: { ownRole: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [role, setRole] = useState(ownRole in ROLE_RANK ? ownRole : "viewer");
  const [newToken, setNewToken] = useState<string | null>(null);

  const roleOptions = ["viewer", "manager", "admin"].filter(
    (r) => ROLE_RANK[r] <= (ROLE_RANK[ownRole] ?? 1),
  );

  const { data: keys } = useQuery({
    queryKey: ["api-keys"],
    queryFn: () => api.getApiKeys(),
  });

  const createMut = useMutation({
    mutationFn: () => api.createApiKey(name.trim(), role),
    onSuccess: (res) => {
      setNewToken(res.token);
      setName("");
      queryClient.invalidateQueries({ queryKey: ["api-keys"] });
    },
  });

  const revokeMut = useMutation({
    mutationFn: (id: string) => api.revokeApiKey(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["api-keys"] }),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.deleteApiKey(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["api-keys"] }),
  });

  return (
    <div>
      {newToken && (
            <div className="mb-3 rounded-md bg-amber-50 p-3">
              <p className="text-xs text-amber-800">{t("settings.chatApiTokenOnce")}</p>
              <code className="mt-1 block break-all rounded bg-white px-2 py-1 font-mono text-xs text-gray-900">
                {newToken}
              </code>
              <button
                className="mt-2 text-xs text-blue-600 hover:underline"
                onClick={() => navigator.clipboard?.writeText(newToken)}
              >
                {t("settings.chatApiCopy")}
              </button>
              <button
                className="ml-3 mt-2 text-xs text-gray-500 hover:underline"
                onClick={() => setNewToken(null)}
              >
                {t("settings.chatApiDismiss")}
              </button>
            </div>
          )}

      {keys && keys.length > 0 ? (
        <ul className="mb-4 divide-y divide-gray-100">
          {keys.map((k) => (
            <li key={k.id} className="flex items-center justify-between py-2 text-sm">
              <div>
                <span className="font-medium text-gray-900">{k.name}</span>
                <span className="ml-2 font-mono text-xs text-gray-400">
                  {k.key_prefix}… · {k.role}
                  {k.user ? ` · ${k.user}` : ""}
                </span>
                {k.revoked && <span className="ml-2 text-xs text-red-600">{t("settings.chatApiRevoked")}</span>}
              </div>
              {k.revoked ? (
                <button
                  className="text-xs text-red-600 hover:underline"
                  onClick={() => deleteMut.mutate(k.id)}
                  disabled={deleteMut.isPending}
                >
                  {t("settings.chatApiDelete")}
                </button>
              ) : (
                <button
                  className="text-xs text-red-600 hover:underline"
                  onClick={() => revokeMut.mutate(k.id)}
                  disabled={revokeMut.isPending}
                >
                  {t("settings.chatApiRevoke")}
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mb-4 text-xs text-gray-400">{t("settings.chatApiNoKeys")}</p>
      )}

      <div className="flex flex-wrap items-end gap-3">
        <div className="w-48">
          <Input
            label={t("settings.chatApiName")}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="lambda-web"
          />
        </div>
        <Select
          label={t("settings.chatApiRole")}
          options={roleOptions}
          value={role}
          onChange={(e) => setRole(e.target.value)}
        />
        <Button onClick={() => createMut.mutate()} disabled={!name.trim() || createMut.isPending}>
          {t("settings.chatApiCreate")}
        </Button>
      </div>
      <p className="mt-2 text-xs text-gray-400">{t("settings.chatApiWarn")}</p>
    </div>
  );
}

function ChatApiCard({
  enabled,
  onToggle,
  ownRole,
}: {
  enabled: boolean;
  onToggle: () => void;
  ownRole: string;
}) {
  const { t } = useTranslation();

  return (
    <Card title={t("settings.chatApiTitle")}>
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm text-gray-700">{t("settings.chatApiBody")}</p>
          <p className="mt-1 text-xs text-gray-400">
            {enabled ? t("settings.chatApiEnabled") : t("settings.chatApiDisabled")}
          </p>
        </div>
        <button
          onClick={onToggle}
          className={`ml-4 shrink-0 rounded-full px-4 py-1.5 text-sm font-medium ${
            enabled
              ? "bg-red-50 text-red-700 hover:bg-red-100"
              : "bg-green-50 text-green-700 hover:bg-green-100"
          }`}
        >
          {enabled ? t("common.disable") : t("common.enable")}
        </button>
      </div>

      {enabled && (
        <div className="mt-4 border-t border-gray-100 pt-4">
          <div className="mb-2 text-xs font-medium text-gray-500">{t("settings.chatApiKeysTitle")}</div>
          <ApiKeysSection ownRole={ownRole} />
        </div>
      )}
    </Card>
  );
}

export default function SettingsPage() {
  const { user } = useAuth();
  const { t } = useTranslation();
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

  if (isLoading) return <p className="text-gray-500">{t("common.loading")}</p>;

  return (
    <div className="space-y-6">
      <Card title={t("language.title")}>
        <div className="flex flex-wrap items-end gap-4">
          <div className="w-64">
            <LanguageSelect />
          </div>
        </div>
        <p className="mt-2 text-xs text-gray-400">{t("language.help")}</p>
      </Card>

      {/* Personal account — every signed-in user can change their own password
          (the shared public_manager demo account has none). */}
      {/* Password-less (social-login-only) users get "Set a password"; everyone
          else keeps "Change Password". has_password === false is the only case
          that hides Change Password, so an unknown value stays safe. */}
      {user?.role !== "public_manager" &&
        (user?.has_password === false ? <SetPasswordCard /> : <ChangePasswordCard />)}
      {user?.role !== "public_manager" && <LinkedAccountsCard />}

      <Card title={t("settings.pdfTitle")}>
        <div className="flex flex-wrap items-end gap-4">
          {isAdmin ? (
            <Select
              label={t("settings.pageSize")}
              options={["A4", "letter"]}
              value={settings?.pdf_page_size || "A4"}
              onChange={(e) => settingsMut.mutate({ pdf_page_size: e.target.value })}
            />
          ) : (
            <div>
              <div className="text-xs font-medium text-gray-500 mb-1">{t("settings.pageSize")}</div>
              <div className="text-sm text-gray-900">{settings?.pdf_page_size || "A4"}</div>
            </div>
          )}
        </div>
        <p className="mt-2 text-xs text-gray-400">
          {t("settings.pdfHelp")}
        </p>
      </Card>

      <Card title={t("settings.openingTitle")}>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-gray-700">
              {t("settings.openingBody")}
            </p>
            <p className="mt-1 text-xs text-gray-400">
              {settings?.opening_balances_enabled === "1"
                ? t("settings.openingEnabled")
                : t("settings.openingDisabled")}
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
              {settings?.opening_balances_enabled === "1" ? t("common.disable") : t("common.enable")}
            </button>
          )}
        </div>
      </Card>

      <Card title={t("settings.signupTitle")}>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-gray-700">
              {t("settings.signupBody")}
            </p>
            <p className="mt-1 text-xs text-gray-400">
              {settings?.allow_public_signup === "1"
                ? t("settings.signupEnabled")
                : t("settings.signupDisabled")}
            </p>
          </div>
          {isAdmin && (
            <button
              onClick={() => settingsMut.mutate({
                allow_public_signup: settings?.allow_public_signup === "1" ? "0" : "1",
              })}
              className={`ml-4 shrink-0 rounded-full px-4 py-1.5 text-sm font-medium ${
                settings?.allow_public_signup === "1"
                  ? "bg-red-50 text-red-700 hover:bg-red-100"
                  : "bg-green-50 text-green-700 hover:bg-green-100"
              }`}
            >
              {settings?.allow_public_signup === "1" ? t("common.disable") : t("common.enable")}
            </button>
          )}
        </div>
      </Card>

      {isAdmin && (
        <ChatApiCard
          enabled={settings?.chat_api_enabled === "1"}
          ownRole={user?.role ?? "viewer"}
          onToggle={() =>
            settingsMut.mutate({
              chat_api_enabled: settings?.chat_api_enabled === "1" ? "0" : "1",
            })
          }
        />
      )}

      {/* Self-service API keys for non-admins: keys are personal (bound to the
          creator, capped at their role), so every real user manages their own
          here. Admins get the same section inside the Chat API card above. */}
      {!isAdmin && user?.role !== "public_manager" && (
        <Card title={t("settings.chatApiKeysTitle")}>
          <p className="mb-3 text-sm text-gray-700">{t("settings.chatApiPersonal")}</p>
          <ApiKeysSection ownRole={user?.role ?? "viewer"} />
        </Card>
      )}

      {isAdmin && <TokenSpendCard demoActive={!!pubStatus?.active} />}

      {/* Public Manager / Demo Mode — kept last: it's the most consequential
          toggle (opens the whole app to anonymous demo access). */}
      {isAdmin && (
        <Card title={t("settings.publicTitle")}>
          {pubStatus?.active ? (
            <div>
              <div className="flex items-center gap-2">
                <span className="inline-block h-2 w-2 rounded-full bg-green-500" />
                <span className="text-sm font-medium text-green-800">{t("settings.publicActive")}</span>
              </div>
              <p className="mt-2 text-sm text-gray-600">
                {t("settings.publicActiveBody")}
              </p>
              <button
                onClick={() => removePubMut.mutate()}
                disabled={removePubMut.isPending}
                className="mt-3 rounded-lg bg-gray-100 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-200"
              >
                {removePubMut.isPending ? t("settings.disabling") : t("settings.disablePublic")}
              </button>
            </div>
          ) : (
            <div>
              <p className="text-sm text-gray-600">
                {t("settings.publicInactiveBody")}
              </p>
              {!showConfirm ? (
                <button
                  onClick={() => setShowConfirm(true)}
                  className="mt-3 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
                >
                  {t("settings.enablePublic")}
                </button>
              ) : (
                <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-4">
                  <p className="text-sm font-medium text-red-800">{t("common.areYouSure")}</p>
                  <p className="mt-1 text-xs text-red-600">
                    {t("settings.enableWarning")}
                  </p>
                  <div className="mt-3 flex gap-2">
                    <button
                      onClick={() => createPubMut.mutate()}
                      disabled={createPubMut.isPending}
                      className="rounded-lg bg-red-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-red-700"
                    >
                      {createPubMut.isPending ? t("settings.enabling") : t("settings.yesEnable")}
                    </button>
                    <button
                      onClick={() => setShowConfirm(false)}
                      className="rounded-lg bg-white px-4 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 border border-gray-300"
                    >
                      {t("common.cancel")}
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
