// PDF templates: the layouts documents print with, per document type (offers,
// orders, invoices, purchase orders, delivery notes, proposals, reservations;
// api/pdf_templates.py). Managers and admins create and edit them; everyone can
// see them and pick one on a document. The chat, REST and MCP clients reach the
// same actions.
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Copy, FileText, Plus } from "lucide-react";
import { api } from "@/api/client";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useConfirm, usePrompt } from "@/components/ui/dialog";
import { useAuth } from "@/contexts/auth-context";
import { Select } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import {
  PDF_TEMPLATES_KEY, PDF_TEMPLATE_DOCTYPES, pdfTemplateDoctype, usePdfTemplates, usePdfTextDefaults,
  type PdfTemplateDoctype, type PdfTemplateSummary,
} from "@/hooks/use-pdf-templates";

/** Link to a template's editor; the built-in one is per document type. */
export function templateHref(template: string, doctype: string) {
  const base = `/settings/pdf-templates/${encodeURIComponent(template)}`;
  return template === "standard" ? `${base}?doctype=${encodeURIComponent(doctype)}` : base;
}

export function canEditPdfTemplates(role: string | undefined) {
  return role === "admin" || role === "manager" || role === "public_manager";
}

/** The shared demo account edits layouts but can't upload or remove files. */
export function canManagePdfAssets(role: string | undefined) {
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

/** The intro and closing texts new documents of a type start with (one pair per type). */
function TextDefaultsCard({ canEdit, type }: { canEdit: boolean; type: PdfTemplateDoctype }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data } = usePdfTextDefaults(true, type.doctype);
  const [texts, setTexts] = useState({ intro_text: "", remarks: "" });
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (data) setTexts({ intro_text: data.intro_text, remarks: data.remarks });
  }, [data]);
  const save = useMutation({
    mutationFn: () => api.runAction("set_pdf_text_defaults", { doctype: type.doctype, ...texts }),
    onSuccess: (result) => {
      qc.setQueryData([...PDF_TEMPLATES_KEY, "text-defaults", type.doctype], result);
      setSaved(true);
    },
  });
  const dirty = !!data && (texts.intro_text !== data.intro_text || texts.remarks !== data.remarks);
  const field = (key: "intro_text" | "remarks", label: string) => (
    <label className="block">
      <span className="mb-1.5 block text-sm font-medium text-fg">{t(`fields.${label}`, { defaultValue: label })}</span>
      <textarea rows={4} value={texts[key]} disabled={!canEdit}
        onChange={(e) => { setTexts((prev) => ({ ...prev, [key]: e.target.value })); setSaved(false); }}
        className="block w-full rounded-lg bg-surface px-3 py-2 text-sm text-fg ring-1 ring-line focus:outline-none focus:ring-2 focus:ring-brand/30 disabled:opacity-70" />
    </label>
  );
  return (
    <Card>
      <h3 className="text-base font-semibold tracking-tight text-fg">
        {t("pdfTemplates.textDefaultsFor", { type: t(`doctypes.${type.slug}.other`) })}
      </h3>
      <p className="mt-1 max-w-2xl text-sm text-fg-muted">
        {t("pdfTemplates.textDefaultsIntroFor", { type: t(`doctypes.${type.slug}.other`) })}
      </p>
      <div className="mt-4 space-y-4">
        {field("intro_text", "Intro text")}
        {field("remarks", "Closing text / terms")}
      </div>
      {data && (
        <p className="mt-2 text-xs text-fg-muted">
          {t("pdfTemplates.textDefaultsPlaceholders", { placeholders: data.placeholders.join(", "), interpolation: { escapeValue: false } })}
        </p>
      )}
      {save.error && <p role="alert" className="mt-2 text-sm text-rose-700">{save.error.message}</p>}
      {canEdit && (
        <div className="mt-4 flex items-center gap-3">
          <Button onClick={() => save.mutate()} disabled={!dirty || save.isPending}>{t("pdfTemplates.saveTexts")}</Button>
          {saved && !dirty && <span className="text-sm text-fg-muted">{t("pdfTemplates.textsSaved")}</span>}
        </div>
      )}
    </Card>
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
  const [params, setParams] = useSearchParams();
  const type = pdfTemplateDoctype(params.get("type")) ?? PDF_TEMPLATE_DOCTYPES[0];
  const { data, isLoading, error } = usePdfTemplates(showArchived, true, type.doctype);
  // Templates of the other types, to start this type's layout in the same style.
  const { data: all } = usePdfTemplates(false, canEditPdfTemplates(user?.role));
  const otherTypes = (all?.templates ?? []).filter((tpl) => !tpl.standard && tpl.doctype !== type.doctype);

  const run = useMutation({
    mutationFn: ({ action, args }: { action: string; args: Record<string, unknown> }) => api.runAction(action, args),
    onSuccess: () => qc.invalidateQueries({ queryKey: PDF_TEMPLATES_KEY }),
  });

  const create = async (basedOn: PdfTemplateSummary) => {
    const name = basedOn.standard ? t("pdfTemplates.standard") : basedOn.title;
    const title = await prompt({
      title: t("pdfTemplates.newTitleFor", { type: t(`doctypes.${type.slug}.one`) }),
      body: t("pdfTemplates.newBody", { name }),
      defaultValue: basedOn.standard ? "" : t("pdfTemplates.copyOf", { name: basedOn.title }),
      confirmLabel: t("pdfTemplates.create"),
    });
    if (!title?.trim()) return;
    const created = await run.mutateAsync({
      action: "create_pdf_template",
      args: { title: title.trim(), based_on: basedOn.template, doctype: type.doctype },
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
        </div>
        <div role="tablist" className="mt-4 flex flex-wrap gap-1.5">
          {PDF_TEMPLATE_DOCTYPES.map((entry) => (
            <button key={entry.slug} type="button" role="tab" aria-selected={entry.slug === type.slug}
              onClick={() => setParams(entry.slug === "quotation" ? {} : { type: entry.slug })}
              className={cn("rounded-full px-3 py-1 text-sm ring-1 transition-colors",
                entry.slug === type.slug
                  ? "bg-brand text-white ring-brand"
                  : "bg-surface text-fg-muted ring-line hover:text-fg")}>
              {t(`doctypes.${entry.slug}.other`)}
            </button>
          ))}
        </div>
        {canEdit && standard && (
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button onClick={() => create(standard)} disabled={run.isPending}>
              <Plus className="h-4 w-4" />
              {t("pdfTemplates.newFor", { type: t(`doctypes.${type.slug}.one`) })}
            </Button>
            {otherTypes.length > 0 && (
              <div className="w-72 max-w-full">
                <Select value="" placeholder={false}
                  options={[
                    { value: "", label: t("pdfTemplates.fromOtherType") },
                    ...otherTypes.map((tpl) => ({
                      value: tpl.template,
                      label: `${tpl.title} (${t(`doctypes.${pdfTemplateDoctype(tpl.doctype)?.slug ?? "quotation"}.one`)})`,
                    })),
                  ]}
                  onChange={(e) => {
                    const source = otherTypes.find((tpl) => tpl.template === e.target.value);
                    if (source) void create(source);
                  }} />
              </div>
            )}
          </div>
        )}
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
                <Link to={templateHref(tpl.template, type.doctype)}
                  className="truncate text-sm font-semibold text-fg hover:text-brand">
                  {tpl.standard ? t("pdfTemplates.standard") : tpl.title}
                </Link>
                <TemplateBadges template={tpl} />
              </div>
              <p className="mt-0.5 text-xs text-fg-muted">
                {[tpl.standard ? t("pdfTemplates.standardDescription") : tpl.description,
                  type.slug === "proposal" || type.slug === "reservation" ? null
                    : t("pdfTemplates.usedByIssued", { count: tpl.used_by_documents })].filter(Boolean).join(" · ")}
              </p>
            </div>
            {canEdit && (
              <div className="flex flex-wrap items-center gap-2">
                {!tpl.default && tpl.published && !tpl.archived && (
                  <Button variant="ghost" size="sm" disabled={run.isPending}
                    onClick={() => run.mutate({ action: "set_default_pdf_template",
                      args: { doctype: type.doctype, template: tpl.template } })}>
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

      {type.texts && <TextDefaultsCard key={type.doctype} canEdit={canEdit} type={type} />}
    </div>
  );
}
