// Edit one PDF template: HTML + CSS on the left, the saved working copy
// rendered against an offer on the right. Saving overwrites the working copy
// in place (no versions); publishing makes it what offers print with. A
// version appears only when an offer is submitted with a layout.
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ArrowLeft, ChevronDown, ChevronRight, RefreshCw, Trash2, Upload } from "lucide-react";
import { api } from "@/api/client";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SaveButton } from "@/components/ui/save-button";
import { useConfirm } from "@/components/ui/dialog";
import { useAuth } from "@/contexts/auth-context";
import { useUnsavedChanges } from "@/lib/use-unsaved-changes";
import { PDF_TEMPLATES_KEY, usePdfTemplate } from "@/hooks/use-pdf-templates";
import { TemplateBadges, canEditPdfTemplates } from "@/pages/settings/pdf-templates";

function Section({ title, children, open: initiallyOpen = false }: {
  title: string; children: React.ReactNode; open?: boolean;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <div className="rounded-xl bg-surface ring-1 ring-line shadow-card">
      <button type="button" onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm font-semibold text-fg">
        {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        {title}
      </button>
      {open && <div className="border-t border-line px-4 py-3">{children}</div>}
    </div>
  );
}

/** The saved working copy (or the built-in layout) rendered as a PDF. */
function Preview({ template, revision, source }: { template: string; revision: number; source: "draft" | "live" }) {
  const { t } = useTranslation();
  const [document, setDocument] = useState("");
  const [applied, setApplied] = useState("");
  const [state, setState] = useState<{ url?: string; error?: string; document?: string; blocked?: string; loading: boolean }>({ loading: true });

  useEffect(() => {
    let url: string | undefined;
    let cancelled = false;
    setState((prev) => ({ ...prev, loading: true }));
    api.pdfTemplatePreview(template, source, applied || undefined)
      .then((result) => {
        if (cancelled) return;
        url = URL.createObjectURL(result.blob);
        setState({ url, document: result.document, blocked: result.blocked, loading: false });
      })
      .catch((err) => !cancelled && setState({ error: err.message, loading: false }));
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [template, revision, source, applied]);

  return (
    <div className="flex min-h-0 flex-col gap-2">
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1">
          <Input label={t("pdfTemplates.previewWith")} placeholder={state.document || "QTN-0001"}
            value={document} onChange={(e) => setDocument(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") setApplied(document.trim()); }} />
        </div>
        <Button variant="secondary" onClick={() => setApplied(document.trim())} disabled={state.loading}>
          <RefreshCw className={state.loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
          {t("pdfTemplates.refresh")}
        </Button>
      </div>
      {state.blocked && (
        <p role="alert" className="text-sm text-amber-700">{t("pdfTemplates.blocked", { urls: state.blocked })}</p>
      )}
      {state.error ? (
        <p role="alert" className="rounded-lg bg-rose-50 p-3 text-sm text-rose-700 ring-1 ring-rose-200">{state.error}</p>
      ) : state.url ? (
        <iframe title={t("pdfTemplates.preview")} src={state.url}
          className="h-[75vh] w-full rounded-lg bg-white ring-1 ring-line" />
      ) : (
        <p className="text-sm text-fg-muted">{t("common.loading")}</p>
      )}
    </div>
  );
}

export default function PdfTemplateEditor() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { user } = useAuth();
  const { data: tpl, error: loadError } = usePdfTemplate(id);
  const editable = canEditPdfTemplates(user?.role) && !!tpl && !tpl.standard && !tpl.archived;
  const { dirty, markDirty, markClean } = useUnsavedChanges();
  const [html, setHtml] = useState("");
  const [title, setTitle] = useState("");
  const [revision, setRevision] = useState(0);
  const [source, setSource] = useState<"draft" | "live">("draft");
  const [notice, setNotice] = useState("");
  const editor = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  // Follow the stored copy (first load, the chat changing it) unless editing.
  useEffect(() => {
    if (!tpl || dirty) return;
    setHtml(tpl.standard ? tpl.html ?? "" : tpl.draft_html ?? "");
    setTitle(tpl.title);
  }, [tpl, dirty]);

  // Action results omit the HTML, so merge them into the cached template
  // rather than replacing it.
  const refresh = (next?: Record<string, unknown>) => {
    if (next) qc.setQueryData([...PDF_TEMPLATES_KEY, "detail", id], (prev: object | undefined) => ({ ...prev, ...next }));
    qc.invalidateQueries({ queryKey: PDF_TEMPLATES_KEY });
    setRevision((r) => r + 1);
  };

  const action = useMutation({
    mutationFn: ({ name, args }: { name: string; args: Record<string, unknown> }) =>
      api.runAction(name, { template: id, ...args }),
  });

  const save = useCallback(async (publish = false) => {
    if (!tpl) return;
    setNotice("");
    const result = await action.mutateAsync({
      name: "update_pdf_template",
      args: { html, ...(title.trim() && title !== tpl.title ? { title: title.trim() } : {}),
        expected_draft_modified: tpl.draft_modified, publish },
    });
    markClean();
    refresh({ ...result, draft_html: html });
    if (publish && result.checked_against) setNotice(t("pdfTemplates.publishedNotice", { document: result.checked_against }));
  }, [tpl, html, title, action, markClean, t]);

  const publish = async () => {
    try {
      if (dirty) await save(true);
      else {
        const result = await action.mutateAsync({ name: "publish_pdf_template", args: {} });
        if (result.checked_against) setNotice(t("pdfTemplates.publishedNotice", { document: result.checked_against }));
        refresh();
      }
    } catch { /* shown below */ }
  };

  const revert = async () => {
    if (!(await confirm({ title: t("pdfTemplates.revertTitle"), body: t("pdfTemplates.revertBody"),
      confirmLabel: t("pdfTemplates.revert"), danger: true }))) return;
    await action.mutateAsync({ name: "revert_pdf_template_draft", args: {} });
    markClean();
    refresh();
  };

  const upload = async (file: File | undefined) => {
    if (!file) return;
    try {
      refresh(await api.uploadPdfTemplateAsset(id, file));
    } catch (err) {
      setNotice((err as Error).message);
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "s") {
      e.preventDefault();
      if (dirty) void save().catch(() => undefined);
    } else if (e.key === "Tab" && !e.shiftKey) {
      // Indent instead of leaving the editor.
      e.preventDefault();
      const el = e.currentTarget;
      const { selectionStart: start, selectionEnd: end } = el;
      const next = html.slice(0, start) + "  " + html.slice(end);
      setHtml(next);
      markDirty();
      requestAnimationFrame(() => { el.selectionStart = el.selectionEnd = start + 2; });
    }
  };

  if (loadError) return <p role="alert" className="text-sm text-rose-700">{loadError.message}</p>;
  if (!tpl) return <p className="text-fg-muted">{t("common.loading")}</p>;

  const lines = html.split("\n").length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/settings/pdf-templates" className="inline-flex items-center gap-1 text-sm text-fg-muted hover:text-fg">
          <ArrowLeft className="h-4 w-4" />
          {t("pdfTemplates.title")}
        </Link>
      </div>

      <Card>
        <div className="flex flex-wrap items-start gap-4">
          <div className="min-w-0 flex-1 space-y-2">
            {editable ? (
              <Input label={t("pdfTemplates.name")} value={title}
                onChange={(e) => { setTitle(e.target.value); markDirty(); }} />
            ) : (
              <h3 className="text-base font-semibold tracking-tight text-fg">
                {tpl.standard ? t("pdfTemplates.standard") : tpl.title}
              </h3>
            )}
            <TemplateBadges template={tpl} />
            <p className="text-xs text-fg-muted">
              {tpl.standard ? t("pdfTemplates.standardDescription") : t("pdfTemplates.editHint")}
            </p>
          </div>
        </div>
      </Card>

      {/* Actions stay in reach while scrolling through a long template. */}
      {editable && (
        <div className="sticky top-0 z-20 flex flex-wrap items-center justify-end gap-2 rounded-xl bg-surface px-4 py-2 ring-1 ring-line shadow-card">
          {action.error ? (
            <p role="alert" className="mr-auto min-w-0 text-sm text-rose-700">{action.error.message}</p>
          ) : notice ? (
            <p className="mr-auto min-w-0 text-sm text-emerald-700">{notice}</p>
          ) : dirty && <p className="mr-auto text-sm text-fg-muted">{t("menuLayout.unsaved")}</p>}
          {tpl.published && tpl.unpublished_changes && (
            <Button variant="ghost" size="sm" onClick={revert} disabled={action.isPending}>
              {t("pdfTemplates.revert")}
            </Button>
          )}
          <SaveButton dirty={dirty} saving={action.isPending}
            onClick={() => void save().catch(() => undefined)} />
          <Button variant="secondary" onClick={publish}
            disabled={action.isPending || (!dirty && tpl.published && !tpl.unpublished_changes)}>
            {t("pdfTemplates.publish")}
          </Button>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-2">
          <div className="flex items-center justify-between text-xs text-fg-muted">
            <span>{t("pdfTemplates.source")}</span>
            <span>{t("pdfTemplates.lines", { count: lines })}</span>
          </div>
          <textarea ref={editor} value={html} readOnly={!editable} spellCheck={false} wrap="off"
            onChange={(e) => { setHtml(e.target.value); markDirty(); }} onKeyDown={onKeyDown}
            aria-label={t("pdfTemplates.source")}
            className="h-[75vh] w-full resize-y rounded-lg bg-surface p-3 font-mono text-xs leading-5 text-fg ring-1 ring-line focus:outline-none focus:ring-2 focus:ring-brand/40" />
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <div className="flex items-center justify-between text-xs text-fg-muted">
            <span>{t("pdfTemplates.preview")}</span>
            {!tpl.standard && tpl.published && (
              <span className="inline-flex gap-1">
                {(["draft", "live"] as const).map((value) => (
                  <button key={value} type="button" onClick={() => setSource(value)}
                    className={value === source ? "font-semibold text-fg" : "hover:text-fg"}>
                    {t(`pdfTemplates.show_${value}`)}
                  </button>
                ))}
              </span>
            )}
          </div>
          <Preview template={id} revision={revision} source={tpl.standard ? "live" : source} />
          {dirty && <p className="text-xs text-fg-muted">{t("pdfTemplates.previewShowsSaved")}</p>}
        </div>
      </div>

      {!tpl.standard && (
        <Section title={t("pdfTemplates.assets", { count: tpl.assets.length })} open={tpl.assets.length > 0}>
          <p className="mb-3 text-xs text-fg-muted">{t("pdfTemplates.assetsHint")}</p>
          <ul className="space-y-2">
            {tpl.assets.map((asset) => (
              <li key={asset.filename} className="flex items-center gap-3">
                {asset.content_type?.startsWith("image/") ? (
                  <img src={api.pdfTemplateAssetUrl(id, asset.filename)} alt=""
                    className="h-8 w-12 rounded bg-white object-contain ring-1 ring-line" />
                ) : <span className="h-8 w-12 rounded bg-surface-subtle ring-1 ring-line" />}
                <code className="min-w-0 flex-1 truncate text-xs">{asset.filename}</code>
                <span className="text-xs text-fg-muted">{Math.ceil(asset.size_bytes / 1024)} kB</span>
                {editable && (
                  <button type="button" title={t("common.delete")} aria-label={t("common.delete")}
                    className="rounded p-1 text-fg-muted hover:text-rose-700"
                    onClick={async () => refresh(await action.mutateAsync({
                      name: "delete_pdf_template_asset", args: { filename: asset.filename } }))}>
                    <Trash2 className="h-4 w-4" />
                  </button>
                )}
              </li>
            ))}
          </ul>
          {editable && (
            <>
              <input ref={fileInput} type="file" className="hidden"
                accept=".png,.jpg,.jpeg,.gif,.webp,.svg,.woff,.woff2,.ttf,.otf"
                onChange={(e) => { void upload(e.target.files?.[0]); e.target.value = ""; }} />
              <Button variant="secondary" size="sm" className="mt-3" onClick={() => fileInput.current?.click()}>
                <Upload className="h-4 w-4" />
                {t("pdfTemplates.upload")}
              </Button>
            </>
          )}
        </Section>
      )}

      <Section title={t("pdfTemplates.versions", { count: tpl.versions.length })}>
        <p className="mb-3 text-xs text-fg-muted">{t("pdfTemplates.versionsHint")}</p>
        {tpl.versions.length === 0 ? (
          <p className="text-sm text-fg-muted">{t("pdfTemplates.noVersions")}</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {tpl.versions.map((version) => (
              <li key={version.version} className="flex flex-wrap gap-x-3">
                <span className="font-semibold">{version.label}</span>
                <span className="text-fg-muted">{t("pdfTemplates.firstUsed", { date: version.first_used.slice(0, 10) })}</span>
                <span>{t("pdfTemplates.usedBy", { count: version.documents })}</span>
                {version.example_document && (
                  <Link className="text-sky-600 hover:underline"
                    to={`/app/quotation/${encodeURIComponent(version.example_document)}`}>
                    {version.example_document}
                  </Link>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title={t("pdfTemplates.reference")}>
        <pre className="whitespace-pre-wrap text-xs leading-5 text-fg-muted">{tpl.context_reference}</pre>
      </Section>

      {tpl.standard && canEditPdfTemplates(user?.role) && (
        <p className="text-sm text-fg-muted">
          {t("pdfTemplates.standardEditHint")}{" "}
          <button type="button" className="text-sky-600 hover:underline" onClick={() => navigate("/settings/pdf-templates")}>
            {t("pdfTemplates.title")}
          </button>
        </p>
      )}
    </div>
  );
}
