import { useState, useEffect, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/api/client";
import { LinkField } from "@/components/document/link-field";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

// A Proposal (Sammelofferte) bundles several independent quotations into one
// branded PDF. It is print-only: nothing here touches the quotations' own
// lifecycle. Saving lets the user reopen/duplicate so cover text isn't retyped.

interface PositionRow {
  quotation: string;
  position_title: string;
  position_blurb: string;
  is_recommended: number;
}

const blankRow = (): PositionRow => ({
  quotation: "",
  position_title: "",
  position_blurb: "",
  is_recommended: 0,
});

const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

export default function ProposalForm() {
  const { name } = useParams<{ name: string }>();
  const isNew = !name;
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { t } = useTranslation();
  const tr = (k: string, dflt: string) => t(k, { defaultValue: dflt });

  const [form, setForm] = useState<Record<string, string>>({
    title: "Offerte",
    customer: "",
    company: "",
    proposal_date: new Date().toISOString().slice(0, 10),
    partner_name: "",
    partner_email: "",
    cover_letter: "",
  });
  const [rows, setRows] = useState<PositionRow[]>([blankRow()]);
  const [appendix, setAppendix] = useState<string | null>(null);
  const [appendixBusy, setAppendixBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));

  // Load an existing proposal.
  const { data: existing } = useQuery({
    queryKey: ["document", "proposal", name],
    queryFn: () => api.getDocument("proposal", name!),
    enabled: !isNew,
  });

  useEffect(() => {
    if (!existing) return;
    setForm({
      title: existing.title ?? "Offerte",
      customer: existing.customer ?? "",
      company: existing.company ?? "",
      proposal_date: existing.proposal_date ?? "",
      partner_name: existing.partner_name ?? "",
      partner_email: existing.partner_email ?? "",
      cover_letter: existing.cover_letter ?? "",
    });
    const qs = (existing.quotations ?? []) as PositionRow[];
    setRows(qs.length ? qs.map((r) => ({ ...blankRow(), ...r })) : [blankRow()]);
    setAppendix(existing.appendix_filename ?? null);
  }, [existing]);

  // Pre-fill the cover letter from the company template once a customer is
  // chosen on a NEW proposal and the letter is still empty — so the salutation
  // and intro aren't retyped. Never clobbers text the user has entered.
  const loadCoverDefault = async () => {
    try {
      const { cover_letter } = await api.getProposalCoverDefault(form.company, form.customer);
      if (cover_letter) set("cover_letter", cover_letter);
    } catch {
      /* a missing template is fine */
    }
  };
  useEffect(() => {
    if (isNew && form.customer && !form.cover_letter) loadCoverDefault();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.customer, form.company]);

  const payload = () => ({
    ...form,
    quotations: rows
      .filter((r) => r.quotation)
      .map((r, i) => ({ ...r, idx: i + 1, is_recommended: r.is_recommended ? 1 : 0 })),
  });

  const saveMut = useMutation({
    mutationFn: async () => {
      if (isNew) return api.createDocument("proposal", payload());
      return api.updateDocument("proposal", name!, payload());
    },
    onSuccess: (res) => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["documents", "proposal"] });
      qc.invalidateQueries({ queryKey: ["document", "proposal", res.name] });
      if (isNew) navigate(`/app/proposal/${encodeURIComponent(res.name)}`);
    },
    onError: (e: any) => setError(e?.message ?? "Save failed"),
  });

  // --- Position rows ---
  const updateRow = (i: number, patch: Partial<PositionRow>) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const addRow = () => setRows((rs) => [...rs, blankRow()]);
  const removeRow = (i: number) => setRows((rs) => (rs.length > 1 ? rs.filter((_, j) => j !== i) : rs));
  const moveRow = (i: number, dir: -1 | 1) =>
    setRows((rs) => {
      const j = i + dir;
      if (j < 0 || j >= rs.length) return rs;
      const next = [...rs];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });

  // --- Appendix ---
  const onPickAppendix = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || isNew) return;
    setAppendixBusy(true);
    setError(null);
    try {
      const res = await api.uploadProposalAppendix(name!, file);
      setAppendix(res.appendix_filename);
      qc.invalidateQueries({ queryKey: ["document", "proposal", name] });
    } catch (err: any) {
      setError(err?.message ?? "Upload failed");
    } finally {
      setAppendixBusy(false);
    }
  };
  const removeAppendix = async () => {
    if (isNew) return;
    setAppendixBusy(true);
    try {
      await api.deleteProposalAppendix(name!);
      setAppendix(null);
    } catch (err: any) {
      setError(err?.message ?? "Failed");
    } finally {
      setAppendixBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-5 pb-16">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-fg">
            {isNew ? tr("proposal.new", "New Proposal") : (form.title || tr("proposal.title", "Proposal"))}
          </h1>
          {!isNew && <p className="text-sm text-fg-muted">{name}</p>}
        </div>
        <div className="flex gap-2">
          {!isNew && (
            <Button variant="secondary" onClick={() => window.open(api.proposalPdfUrl(name!), "_blank")}>
              {tr("common.pdf", "PDF")}
            </Button>
          )}
          <Button onClick={() => saveMut.mutate()} disabled={saveMut.isPending}>
            {saveMut.isPending ? tr("common.saving", "Saving…") : tr("common.save", "Save")}
          </Button>
        </div>
      </div>

      {error && (
        <div className="rounded-lg bg-red-50 px-4 py-2 text-sm text-red-700 ring-1 ring-red-200">{error}</div>
      )}

      {/* Header fields */}
      <Card className="space-y-4 p-5">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Input label={tr("fields.Title", "Title")} value={form.title} onChange={(e) => set("title", e.target.value)} />
          <Input label={tr("fields.Date", "Date")} type="date" value={form.proposal_date} onChange={(e) => set("proposal_date", e.target.value)} />
          <LinkField label={tr("fields.Customer", "Customer")} value={form.customer} onChange={(v) => set("customer", v)} linkDoctype="customer" readOnly={false} />
          <LinkField label={tr("fields.Company", "Company")} value={form.company} onChange={(v) => set("company", v)} linkDoctype="company" readOnly={false} />
          <Input label={tr("fields.Partner", "Account Manager")} value={form.partner_name} onChange={(e) => set("partner_name", e.target.value)} />
          <Input label={tr("fields.Partner Email", "Account Manager Email")} value={form.partner_email} onChange={(e) => set("partner_email", e.target.value)} />
        </div>
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <label className="block text-sm font-medium text-fg">{tr("proposal.coverLetter", "Cover letter")}</label>
            <button type="button" onClick={loadCoverDefault} className="text-xs text-brand hover:underline">
              {tr("proposal.loadTemplate", "Load from template")}
            </button>
          </div>
          <textarea
            value={form.cover_letter}
            onChange={(e) => set("cover_letter", e.target.value)}
            rows={5}
            className="block w-full rounded-lg bg-surface px-3 py-2 text-sm text-fg ring-1 ring-line focus:outline-none focus:ring-2 focus:ring-brand/30"
          />
        </div>
      </Card>

      {/* Positions */}
      <Card className="space-y-4 p-5">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-fg">{tr("proposal.offers", "Offers")}</h2>
          <Button size="sm" variant="secondary" onClick={addRow}>
            {tr("proposal.addOffer", "Add offer")}
          </Button>
        </div>
        {rows.map((row, i) => (
          <div key={i} className="rounded-lg p-3 ring-1 ring-line">
            <div className="mb-2 flex items-center gap-2">
              <span className="flex h-6 w-6 items-center justify-center rounded-full bg-brand/10 text-sm font-bold text-brand">
                {LETTERS[i] ?? i + 1}
              </span>
              <div className="flex-1">
                <LinkField label="" value={row.quotation} onChange={(v) => updateRow(i, { quotation: v })} linkDoctype="quotation" readOnly={false} />
              </div>
              <button type="button" onClick={() => moveRow(i, -1)} disabled={i === 0} className="px-1 text-fg-muted disabled:opacity-30">↑</button>
              <button type="button" onClick={() => moveRow(i, 1)} disabled={i === rows.length - 1} className="px-1 text-fg-muted disabled:opacity-30">↓</button>
              <button type="button" onClick={() => removeRow(i)} className="px-1 text-red-500 hover:text-red-700">✕</button>
            </div>
            <div className="grid grid-cols-1 gap-2 pl-8 sm:grid-cols-2">
              <Input label={tr("proposal.positionTitle", "Position title")} value={row.position_title} onChange={(e) => updateRow(i, { position_title: e.target.value })} placeholder={tr("proposal.positionTitleHint", "defaults to the offer's first item")} />
              <label className="flex items-center gap-2 self-end pb-2 text-sm text-fg">
                <input type="checkbox" checked={!!row.is_recommended} onChange={(e) => updateRow(i, { is_recommended: e.target.checked ? 1 : 0 })} />
                {tr("proposal.recommended", "Mark as recommendation")}
              </label>
              <div className="sm:col-span-2">
                <Input label={tr("proposal.blurb", "Description")} value={row.position_blurb} onChange={(e) => updateRow(i, { position_blurb: e.target.value })} placeholder={tr("proposal.blurbHint", "defaults to the offer's notes")} />
              </div>
            </div>
          </div>
        ))}
      </Card>

      {/* Appendix */}
      <Card className="space-y-3 p-5">
        <h2 className="font-semibold text-fg">{tr("proposal.appendix", "Appendix PDF")}</h2>
        {isNew ? (
          <p className="text-sm text-fg-muted">{tr("proposal.saveFirst", "Save the proposal first to attach an appendix.")}</p>
        ) : appendix ? (
          <div className="flex items-center gap-3 text-sm">
            <span className="text-fg">📎 {appendix}</span>
            <button type="button" onClick={removeAppendix} disabled={appendixBusy} className="text-red-500 hover:underline">
              {tr("common.remove", "Remove")}
            </button>
          </div>
        ) : (
          <div>
            <input ref={fileRef} type="file" accept="application/pdf" onChange={onPickAppendix} className="hidden" />
            <Button size="sm" variant="secondary" disabled={appendixBusy} onClick={() => fileRef.current?.click()}>
              {appendixBusy ? tr("common.uploading", "Uploading…") : tr("proposal.uploadAppendix", "Upload appendix PDF")}
            </Button>
            <p className="mt-1 text-xs text-fg-muted">{tr("proposal.appendixHint", "Appended after the offers — e.g. a price overview.")}</p>
          </div>
        )}
      </Card>
    </div>
  );
}
