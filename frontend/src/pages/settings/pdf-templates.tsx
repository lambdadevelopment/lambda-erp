// PDF templates: the layouts offers print with (api/pdf_templates.py). Managers
// and admins create and edit them; everyone can see them and pick one on an
// offer. The chat, REST and MCP clients reach the same actions.
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Copy, FileText, Plus } from "lucide-react";
import { api } from "@/api/client";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useConfirm, usePrompt } from "@/components/ui/dialog";
import { useAuth } from "@/contexts/auth-context";
import { PDF_TEMPLATES_KEY, usePdfTemplates, type PdfTemplateSummary } from "@/hooks/use-pdf-templates";

export function canEditPdfTemplates(role: string | undefined) {
  return role === "admin" || role === "manager";
}

export function TemplateBadges({ template }: { template: PdfTemplateSummary }) {
  const { t } = useTranslation();
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      {template.default && <Badge>{t("pdfTemplates.default")}</Badge>}
      {template.standard && <Badge variant="secondary">{t("pdfTemplates.builtIn")}</Badge>}
      {!template.published && <Badge variant="warning">{t("pdfTemplates.notPublished")}</Badge>}
      {template.published && template.unpublished_changes && (
        <Badge variant="warning">{t("pdfTemplates.unpublishedChanges")}</Badge>
      )}
      {template.archived && <Badge variant="secondary">{t("pdfTemplates.archived")}</Badge>}
    </span>
  );
}

export default function PdfTemplatesPage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const canEdit = canEditPdfTemplates(user?.role);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const prompt = usePrompt();
  const confirm = useConfirm();
  const [showArchived, setShowArchived] = useState(false);
  const { data, isLoading, error } = usePdfTemplates(showArchived);

  const run = useMutation({
    mutationFn: ({ action, args }: { action: string; args: Record<string, unknown> }) => api.runAction(action, args),
    onSuccess: () => qc.invalidateQueries({ queryKey: PDF_TEMPLATES_KEY }),
  });

  const create = async (basedOn: PdfTemplateSummary) => {
    const title = await prompt({
      title: t("pdfTemplates.newTitle"),
      body: t("pdfTemplates.newBody", { name: basedOn.title }),
      defaultValue: basedOn.standard ? "" : t("pdfTemplates.copyOf", { name: basedOn.title }),
      confirmLabel: t("pdfTemplates.create"),
    });
    if (!title?.trim()) return;
    const created = await run.mutateAsync({
      action: "create_pdf_template",
      args: { title: title.trim(), based_on: basedOn.template },
    });
    navigate(`/settings/pdf-templates/${encodeURIComponent(created.template)}`);
  };

  const remove = async (template: PdfTemplateSummary) => {
    if (!(await confirm({ title: t("pdfTemplates.deleteTitle", { name: template.title }),
      body: t("pdfTemplates.deleteBody"), confirmLabel: t("common.delete"), danger: true }))) return;
    run.mutate({ action: "delete_pdf_template", args: { template: template.template } });
  };

  const templates = data?.templates ?? [];
  const standard = templates.find((tpl) => tpl.standard);

  return (
    <div className="space-y-6">
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h3 className="text-base font-semibold tracking-tight text-fg">{t("pdfTemplates.title")}</h3>
            <p className="mt-1 max-w-2xl text-sm text-fg-muted">{t("pdfTemplates.intro")}</p>
            <p className="mt-2 max-w-2xl text-xs text-fg-muted">{t("pdfTemplates.chatHint")}</p>
          </div>
          {canEdit && standard && (
            <Button onClick={() => create(standard)} disabled={run.isPending}>
              <Plus className="h-4 w-4" />
              {t("pdfTemplates.new")}
            </Button>
          )}
        </div>
      </Card>

      {(error || run.error) && (
        <p role="alert" className="text-sm text-rose-700">{(error ?? run.error)?.message}</p>
      )}
      {isLoading && <p className="text-fg-muted">{t("common.loading")}</p>}

      <div className="space-y-3">
        {templates.map((tpl) => (
          <div key={tpl.template}
            className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl bg-surface px-4 py-3 ring-1 ring-line shadow-card">
            <FileText className="h-5 w-5 shrink-0 text-fg-muted" />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <Link to={`/settings/pdf-templates/${encodeURIComponent(tpl.template)}`}
                  className="truncate text-sm font-semibold text-fg hover:text-brand">
                  {tpl.standard ? t("pdfTemplates.standard") : tpl.title}
                </Link>
                <TemplateBadges template={tpl} />
              </div>
              <p className="mt-0.5 text-xs text-fg-muted">
                {[tpl.standard ? t("pdfTemplates.standardDescription") : tpl.description,
                  t("pdfTemplates.usedBy", { count: tpl.used_by_documents })].filter(Boolean).join(" · ")}
              </p>
            </div>
            {canEdit && (
              <div className="flex flex-wrap items-center gap-2">
                {!tpl.default && tpl.published && !tpl.archived && (
                  <Button variant="ghost" size="sm" disabled={run.isPending}
                    onClick={() => run.mutate({ action: "set_default_pdf_template",
                      args: { doctype: "Quotation", template: tpl.template } })}>
                    {t("pdfTemplates.makeDefault")}
                  </Button>
                )}
                <Button variant="ghost" size="sm" disabled={run.isPending} onClick={() => create(tpl)}>
                  <Copy className="h-4 w-4" />
                  {t("pdfTemplates.duplicate")}
                </Button>
                {!tpl.standard && (
                  <Button variant="ghost" size="sm" disabled={run.isPending || (tpl.default && !tpl.archived)}
                    onClick={() => run.mutate({ action: "archive_pdf_template",
                      args: { template: tpl.template, archived: !tpl.archived } })}>
                    {tpl.archived ? t("pdfTemplates.restore") : t("pdfTemplates.archive")}
                  </Button>
                )}
                {!tpl.standard && tpl.used_by_documents === 0 && (
                  <Button variant="ghost" size="sm" disabled={run.isPending} onClick={() => remove(tpl)}
                    className="text-rose-700">
                    {t("common.delete")}
                  </Button>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      <label className="flex items-center gap-2 text-sm text-fg-muted">
        <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
        {t("pdfTemplates.showArchived")}
      </label>
    </div>
  );
}
