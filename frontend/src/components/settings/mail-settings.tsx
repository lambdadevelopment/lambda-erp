// Settings → E-Mail (Microsoft 365), admins only (api/mail.py). The tenant
// and the app's credentials come from the deployment and are only reported as
// configured or not; here the admin picks the sending mailbox, switches mail
// on and sends a test message. See docs/mail.md.
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ApiError, request } from "@/api/client";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { formatDateTime } from "@/lib/utils";

interface MailStatus {
  enabled: boolean;
  sender: string | null;
  senders: string[];
  configured: boolean;
  tenant_configured: boolean;
  client_configured: boolean;
  secret_configured: boolean;
  ready: boolean;
  allowed_recipients: string[];
}

interface MailLogRow {
  name: string;
  creation: string;
  sender: string;
  recipients: { to?: string[]; cc?: string[] };
  subject: string;
  status: string;
  error?: string | null;
}

const inputClass =
  "w-full rounded-lg bg-surface px-3 py-2 text-sm text-fg ring-1 ring-line focus:outline-none focus:ring-2 focus:ring-brand/30";

export function MailSettingsCard() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["mail-settings"], queryFn: () => request<MailStatus>("/mail/settings") });
  const log = useQuery({ queryKey: ["mail-log"], queryFn: () => request<{ messages: MailLogRow[] }>("/mail/log?limit=10") });
  const [sender, setSender] = useState("");
  const [senders, setSenders] = useState("");
  const [testTo, setTestTo] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (!status.data) return;
    setSender(status.data.sender ?? "");
    setSenders(status.data.senders.join(", "));
  }, [status.data]);

  const fail = (err: unknown) => setMessage({ ok: false, text: err instanceof ApiError ? err.message : String(err) });
  const save = useMutation({
    mutationFn: (enabled?: boolean) => request<MailStatus>("/mail/settings", {
      method: "PUT",
      body: JSON.stringify({
        sender, senders: senders.split(/[,\s;]+/).filter(Boolean), ...(enabled === undefined ? {} : { enabled }),
      }),
    }),
    onSuccess: (data) => {
      queryClient.setQueryData(["mail-settings"], data);
      setMessage({ ok: true, text: t("mail.saved", { defaultValue: "Saved." }) });
    },
    onError: fail,
  });
  const test = useMutation({
    mutationFn: () => request<{ name: string }>("/mail/test", { method: "POST", body: JSON.stringify({ to: testTo }) }),
    onSuccess: () => {
      setMessage({ ok: true, text: t("mail.testSent", { defaultValue: "Test mail sent. Check the inbox and the sending mailbox's Sent Items." }) });
      queryClient.invalidateQueries({ queryKey: ["mail-log"] });
    },
    onError: (err) => { fail(err); queryClient.invalidateQueries({ queryKey: ["mail-log"] }); },
  });

  const data = status.data;
  return (
    <Card title={t("mail.title", { defaultValue: "E-Mail (Microsoft 365)" })}>
      <p className="mb-4 text-sm text-gray-700">
        {t("mail.intro", {
          defaultValue: "The ERP sends confirmations and documents from a mailbox of your Microsoft 365. Mail comes from your domain and is kept in that mailbox's Sent Items.",
        })}
      </p>
      {!data ? (
        <p className="text-sm text-gray-500">{t("common.loading")}</p>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2">
            <Badge variant={data.configured ? "success" : "warning"} dot>
              {data.configured
                ? t("mail.connected", { defaultValue: "Microsoft 365 connection configured" })
                : t("mail.notConfigured", { defaultValue: "Not configured for this deployment" })}
            </Badge>
            <Badge variant={data.ready ? "success" : "secondary"} dot>
              {data.ready ? t("mail.on", { defaultValue: "Mail is on" }) : t("mail.off", { defaultValue: "Mail is off" })}
            </Badge>
          </div>
          {!data.configured && (
            <p className="text-sm text-gray-600">
              {t("mail.configureHint", {
                defaultValue: "The deployment needs MAIL_GRAPH_TENANT_ID (your Microsoft 365 tenant) and the app's credentials; your Microsoft 365 admin must grant the app Mail.Send.",
              })}
            </p>
          )}
          {data.allowed_recipients.length > 0 && (
            <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">
              {t("mail.allowlist", { defaultValue: "This environment only mails:" })} {data.allowed_recipients.join(", ")}
            </p>
          )}
          <label className="block text-sm">
            <span className="mb-1 block font-medium text-fg">{t("mail.sender", { defaultValue: "Sending mailbox" })}</span>
            <input className={inputClass} value={sender} onChange={(e) => setSender(e.target.value)}
              placeholder="innendienst@example.ch" />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block font-medium text-fg">
              {t("mail.senders", { defaultValue: "Further mailboxes a message may be sent from (optional)" })}
            </span>
            <input className={inputClass} value={senders} onChange={(e) => setSenders(e.target.value)}
              placeholder="buchhaltung@example.ch" />
          </label>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" disabled={save.isPending} onClick={() => save.mutate(undefined)}>
              {t("common.save")}
            </Button>
            {data.enabled ? (
              <Button variant="ghost" disabled={save.isPending} onClick={() => save.mutate(false)}>
                {t("mail.switchOff", { defaultValue: "Switch mail off" })}
              </Button>
            ) : (
              <Button disabled={save.isPending || !sender.trim() || !data.configured} onClick={() => save.mutate(true)}>
                {t("mail.switchOn", { defaultValue: "Switch mail on" })}
              </Button>
            )}
          </div>
          {data.ready && (
            <div className="flex flex-wrap items-end gap-2">
              <label className="block min-w-[16rem] flex-1 text-sm">
                <span className="mb-1 block font-medium text-fg">{t("mail.testTo", { defaultValue: "Send a test mail to" })}</span>
                <input className={inputClass} value={testTo} onChange={(e) => setTestTo(e.target.value)} />
              </label>
              <Button variant="secondary" disabled={!testTo.trim() || test.isPending} onClick={() => test.mutate()}>
                {t("mail.sendTest", { defaultValue: "Send test mail" })}
              </Button>
            </div>
          )}
          {message && <p className={`text-sm ${message.ok ? "text-green-700" : "text-red-600"}`}>{message.text}</p>}
          {!!log.data?.messages.length && (
            <div>
              <h4 className="mb-2 text-sm font-medium text-fg">{t("mail.recent", { defaultValue: "Recent messages" })}</h4>
              <ul className="divide-y divide-line text-sm">
                {log.data.messages.map((row) => (
                  <li key={row.name} className="flex flex-wrap items-center gap-2 py-2">
                    <Badge variant={row.status === "Sent" ? "success" : row.status === "Failed" ? "danger" : "warning"}>
                      {t(`mail.status.${row.status}`, { defaultValue: row.status })}
                    </Badge>
                    <span className="text-gray-500">{formatDateTime(row.creation)}</span>
                    <span className="font-medium">{row.subject}</span>
                    <span className="text-gray-600">→ {(row.recipients.to ?? []).join(", ")}</span>
                    {row.error && <span className="w-full text-xs text-red-600">{row.error}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
