import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Plug, ShieldCheck } from "lucide-react";
import { useAuth } from "@/contexts/auth-context";
import { api } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { LanguageSelect } from "@/components/ui/language-select";

export default function ConnectAuthorize() {
  const { t } = useTranslation();
  const { user, loading } = useAuth();
  const [params] = useSearchParams();
  const requestId = params.get("request") ?? "";
  const [role, setRole] = useState("viewer");
  const realUser = user && user.role !== "public_manager";
  const consent = useQuery({ queryKey: ["mcp-consent", requestId, user?.name],
    queryFn: () => api.getMcpConsent(requestId), enabled: !!realUser && !!requestId, retry: false });
  const decision = useMutation({
    mutationFn: (allow: boolean) => api.approveMcp(requestId, consent.data!.csrf_token, allow, role),
    onSuccess: (result) => { window.location.assign(result.redirect_uri); },
  });
  const returnTo = `/connect/authorize?request=${encodeURIComponent(requestId)}`;
  return <main className="flex min-h-dvh items-center justify-center bg-surface-muted px-4 py-10">
    <div className="w-full max-w-lg space-y-5 rounded-2xl border border-line bg-surface p-6 shadow-sm sm:p-8">
      <div className="flex items-center justify-between"><span className="font-semibold text-fg">Lambda ERP</span><LanguageSelect /></div>
      <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-brand/10 text-brand"><Plug size={26} /></div>
      <h1 className="text-xl font-semibold text-fg">{t("connections.consentTitle")}</h1>
      {!requestId ? <p role="alert">{t("connections.expired")}</p>
        : loading ? <p>{t("common.loading")}</p>
        : !realUser ? <>
          <p className="text-sm text-fg-muted">{t("connections.loginFirst")}</p>
          <Link className="inline-block rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-fg" to={`/login?next=${encodeURIComponent(returnTo)}`}>{t("connections.signIn")}</Link>
        </> : <>
          <p className="text-sm text-fg-muted">{user.email}</p>
          {consent.isLoading && <p>{t("common.loading")}</p>}
          {consent.isError && <p role="alert" className="text-sm text-red-600">{consent.error.message}</p>}
          {consent.data && <>
            <p className="text-sm text-fg">{t("connections.consentBody", { app: consent.data.client_name })}</p>
            <div className="rounded-lg border border-line bg-surface-subtle p-3">
              <p className="text-xs text-fg-muted">{t("connections.returnTo")}</p>
              <p className="mt-1 break-all text-sm font-medium text-fg">{new URL(consent.data.redirect_uri).origin}</p>
            </div>
            <Select label={t("connections.access")} value={role} onChange={(e) => setRole(e.target.value)}
              options={consent.data.roles.map((r) => ({ value: r, label: t(`connections.role_${r}`) }))} />
            <p className="text-sm text-fg-muted">{t(`connections.explain_${role || "viewer"}`)}</p>
            <p className="flex items-start gap-2 text-xs text-fg-muted"><ShieldCheck size={16} className="shrink-0" />{t("connections.revokeHelp")}</p>
            {decision.isError && <p role="alert" className="text-sm text-red-600">{decision.error.message}</p>}
            <div className="flex justify-end gap-2">
              <Button variant="secondary" disabled={decision.isPending} onClick={() => decision.mutate(false)}>{t("connections.deny")}</Button>
              <Button disabled={decision.isPending || !role} onClick={() => decision.mutate(true)}>{t("connections.allow")}</Button>
            </div>
          </>}
        </>}
    </div>
  </main>;
}
