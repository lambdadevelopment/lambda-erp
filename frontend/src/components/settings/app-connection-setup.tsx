import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Check, Copy, MessageCircle, Monitor, Terminal, Code2, Plug, ShieldCheck } from "lucide-react";
import { api } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";

const APPS = [
  { id: "chatgpt", name: "ChatGPT", icon: MessageCircle, oauth: true },
  { id: "claude_desktop", name: "Claude Desktop", icon: Monitor, oauth: true },
  { id: "claude_code", name: "Claude Code CLI", icon: Terminal, oauth: false },
  { id: "codex", name: "Codex CLI", icon: Code2, oauth: false },
  { id: "other", name: "", icon: Plug, oauth: false },
] as const;
const RANK: Record<string, number> = { viewer: 1, manager: 2, admin: 3 };

export function CopyValue({ value, label }: { value: string; label: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState(false);
  return <div className="space-y-2">
    <div className="rounded-lg border border-line bg-surface p-3">
      <pre className="whitespace-pre-wrap break-all font-mono text-xs text-fg">{value}</pre>
    </div>
    <Button variant="secondary" size="sm" onClick={async () => {
      try { await navigator.clipboard.writeText(value); setCopied(true); setError(false); }
      catch { setError(true); }
    }}>
      {copied ? <Check size={15} /> : <Copy size={15} />}
      {copied ? t("connections.copied") : label}
    </Button>
    <span className="text-xs text-fg-muted" role="status">{error ? t("connections.copyFailed") : ""}</span>
  </div>;
}

export function AppConnectionSetup({ ownRole }: { ownRole: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [role, setRole] = useState("viewer");
  const [surface, setSurface] = useState("mcp");
  const [newToken, setNewToken] = useState<string | null>(null);
  const app = APPS.find((a) => a.id === selected);
  const status = useQuery({ queryKey: ["connection-status"], queryFn: api.getConnectionStatus });
  const create = useMutation({
    mutationFn: () => api.createApiKey(name.trim(), role, selected as "claude_code" | "codex" | "other"),
    onSuccess: (res) => {
      setNewToken(res.token);
      queryClient.invalidateQueries({ queryKey: ["api-keys"] });
    },
  });
  const enabled = selected === "other" && surface === "chat" ? status.data?.chat_enabled : status.data?.rest_enabled;
  const url = status.data?.mcp_url ?? "";
  const shellUrl = "'" + url.replace(/'/g, "'\\''") + "'";
  const snippet = selected === "claude_code"
    ? `claude mcp add --transport http --scope user lambda-erp ${shellUrl} --header 'Authorization: Bearer ${newToken}'`
    : `# ~/.codex/config.toml\n[mcp_servers.lambda-erp]\nurl = ${JSON.stringify(url)}\nhttp_headers = { Authorization = "Bearer ${newToken}" }`;
  const roles = ["viewer", "manager", "admin"].filter((r) => RANK[r] <= (RANK[ownRole] ?? 1));

  return <section className="space-y-4" aria-label={t("connections.title")}>
    <div>
      <h3 className="text-base font-semibold text-fg">{t("connections.title")}</h3>
      <p className="mt-1 text-sm text-fg-muted">{t("connections.subtitle")}</p>
    </div>
    <div className="grid grid-cols-2 gap-2 lg:grid-cols-5">
      {APPS.map(({ id, name: appName, icon: Icon, oauth }) => <button key={id} type="button"
        disabled={!!newToken || create.isPending} aria-pressed={selected === id}
        className={`flex min-h-28 flex-col items-start gap-2 rounded-xl border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-60 ${selected === id ? "border-brand bg-brand/5" : "border-line bg-surface hover:bg-surface-subtle"}`}
        onClick={() => { setSelected(id); setName(appName || t("connections.customName")); create.reset(); }}>
        <Icon size={22} className="text-brand" aria-hidden="true" />
        <span className="text-sm font-semibold text-fg">{appName || t("connections.other")}</span>
        <span className="text-xs text-fg-muted">{t(oauth ? "connections.signIn" : "connections.apiKey")}</span>
      </button>)}
    </div>
    {app && <div className="space-y-4 rounded-xl border border-line bg-surface-subtle p-4 sm:p-5">
      <h4 className="flex items-center gap-2 font-medium text-fg"><app.icon size={18} />{app.name || t("connections.other")}</h4>
      {status.isError && <p role="alert" className="text-sm text-red-600">{t("common.errorOccurred")} <button className="underline" onClick={() => status.refetch()}>{t("connections.retry")}</button></p>}
      {status.isLoading && <p className="text-sm text-fg-muted">{t("common.loading")}</p>}
      {selected === "other" && !newToken && <Select label={t("connections.interface")} value={surface}
        options={[{ value: "mcp", label: "MCP" }, { value: "rest", label: "REST API" }, { value: "chat", label: "Chat API" }]}
        onChange={(e) => setSurface(e.target.value)} />}
      {status.data && !enabled && <p role="status" className="text-sm text-fg-muted">{t(selected === "other" && surface === "chat" ? "connections.chatDisabled" : "connections.restDisabled")}</p>}
      {app.oauth ? <>
        <ol className="space-y-3 text-sm text-fg">
          <li><span className="mr-2 font-semibold text-brand">1.</span>{t(selected === "chatgpt" ? "connections.chatgptStep" : "connections.claudeStep")}</li>
          <li><span className="mr-2 font-semibold text-brand">2.</span>{t("connections.urlStep")}</li>
        </ol>
        {url && <CopyValue value={url} label={t("connections.copyUrl")} />}
        <p className="text-sm text-fg"><span className="mr-2 font-semibold text-brand">3.</span>{t("connections.approveStep")}</p>
        <p className="text-xs text-fg-muted">{t("connections.noKeyNeeded")}</p>
        <a className="text-sm text-brand hover:underline" target="_blank" rel="noreferrer"
          href={selected === "chatgpt" ? "https://developers.openai.com/plugins/deploy/connect-chatgpt" : "https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp"}>{t("connections.officialGuide")}</a>
      </> : <>
        {!newToken && <div className="grid gap-4 sm:grid-cols-2">
          <Input label={t("settings.chatApiName")} value={name} onChange={(e) => setName(e.target.value)} />
          <Select label={t("connections.access")} value={role} onChange={(e) => setRole(e.target.value)}
            options={roles.map((r) => ({ value: r, label: t(`connections.role_${r}`) }))} />
        </div>}
        {!newToken && <p className="flex items-start gap-2 text-xs text-fg-muted"><ShieldCheck size={16} className="shrink-0" />{t("connections.roleHelp")}</p>}
        {create.isError && <p className="text-sm text-red-600" role="alert">{create.error.message}</p>}
        {newToken && <div className="space-y-4" role="status">
          <p className="flex items-center gap-2 font-medium text-fg"><Check size={18} className="text-green-600" />{t("connections.keyReady")}</p>
          <p className="text-xs text-fg-muted">{t("settings.chatApiTokenOnce")}</p>
          <CopyValue key={newToken} value={newToken} label={t("settings.chatApiCopy")} />
          {selected !== "other" ? <>
            <p className="text-sm text-fg-muted">{t(selected === "codex" ? "connections.codexStep" : "connections.cliStep")}</p>
            <CopyValue key={snippet} value={snippet} label={t("connections.copySetup")} />
          </> : <>
            <CopyValue value={surface === "mcp" ? url : url.replace(/\/mcp$/, surface === "chat" ? "/v1/chat" : "")}
              label={t("connections.copyUrl")} />
            <CopyValue value={`Authorization: Bearer ${newToken}`} label={t("connections.copyHeader")} />
          </>}
        </div>}
        {newToken ? <Button variant="secondary" onClick={() => { setNewToken(null); create.reset(); }}>{t("connections.done")}</Button>
          : <Button onClick={() => create.mutate()} disabled={!enabled || !name.trim() || !role || (selected === "other" && !surface) || create.isPending}>
            {create.isPending ? t("common.loading") : t("settings.chatApiCreate")}</Button>}
      </>}
    </div>}
  </section>;
}
