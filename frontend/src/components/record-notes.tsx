// Internal team notes on a document or master record (api/record_notes.py).
// Never printed, never part of the record itself — the opposite of the
// document's printed Notes / Terms field — and @mentions land in the
// colleague's inbox. One card for every record type, plus a header link.
import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Lock, MessageSquare } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConfirmButton } from "@/components/ui/confirm-button";
import { MentionEditor, mentionAction, type MentionUser } from "@/components/mentions";
import { useAuth } from "@/contexts/auth-context";
import { ApiError } from "@/api/client";

export interface RecordNote {
  id: string; body: string; author_name: string; created_at: string; updated_at: string;
  edited: boolean; can_edit: boolean; can_delete: boolean;
}
interface NotesPage { notes: RecordNote[]; total: number; has_more: boolean }

const PAGE = 50;
const editorClass =
  "block w-full rounded-lg bg-surface px-3 py-2 text-sm text-fg ring-1 ring-line placeholder:text-fg-muted/70 focus:outline-none focus:ring-2 focus:ring-brand/30";

function useNotesEnabled() {
  const { user } = useAuth();
  return { enabled: !!user && user.role !== "public_manager", canWrite: user?.role === "manager" || user?.role === "admin" };
}

function useRecordNotes(recordType: string, recordId: string | undefined, limit = PAGE) {
  const { enabled } = useNotesEnabled();
  return useQuery({
    queryKey: ["record-notes", recordType, recordId, limit],
    queryFn: () => mentionAction<NotesPage>("list_record_notes", { record_type: recordType, record_id: recordId, limit }),
    enabled: enabled && !!recordId,
    // 4xx (unsupported type, missing record, no access) won't change on retry.
    retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
  });
}

/** Header shortcut: "3 internal notes" — jumps to the card at the page end. */
export function RecordNotesLink({ recordType, recordId }: { recordType: string; recordId?: string }) {
  const { t } = useTranslation();
  const notes = useRecordNotes(recordType, recordId);
  if (!notes.data?.total) return null;
  return (
    <a href="#internal-notes"
      onClick={(e) => { e.preventDefault(); document.getElementById("internal-notes")?.scrollIntoView({ behavior: "smooth" }); }}
      className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium text-fg-muted ring-1 ring-line hover:bg-surface-subtle hover:text-fg">
      <MessageSquare size={12} />
      {t("recordNotes.count", { count: notes.data.total })}
    </a>
  );
}

// Plain text with clickable http(s) links; everything else stays literal.
function NoteBody({ text }: { text: string }) {
  const parts = text.split(/(https?:\/\/[^\s<>"]+)/g);
  return (
    <p className="whitespace-pre-wrap break-words text-sm text-fg">
      {parts.map((part, i) => i % 2
        ? <a key={i} href={part} target="_blank" rel="noreferrer" className="text-sky-600 hover:underline">{part}</a>
        : part)}
    </p>
  );
}

export function RecordNotes({ recordType, recordId }: { recordType: string; recordId: string }) {
  const { t } = useTranslation();
  const { enabled, canWrite } = useNotesEnabled();
  const cache = useQueryClient();
  const { hash } = useLocation();
  const [limit, setLimit] = useState(PAGE);
  const [composing, setComposing] = useState(false);
  const [draft, setDraft] = useState({ body: "", recipients: [] as MentionUser[], requestId: crypto.randomUUID() });
  const [editing, setEditing] = useState<{ id: string; body: string; recipients: MentionUser[] } | null>(null);
  const notes = useRecordNotes(recordType, recordId, limit);
  const refresh = () => cache.invalidateQueries({ queryKey: ["record-notes", recordType, recordId] });

  const add = useMutation({
    mutationFn: () => mentionAction("add_record_note", {
      doctype: recordType, record_id: recordId, body: draft.body, request_id: draft.requestId,
    }),
    onSuccess: () => {
      setDraft({ body: "", recipients: [], requestId: crypto.randomUUID() });
      setComposing(false);
      void refresh();
    },
  });
  const save = useMutation({
    mutationFn: (note: { id: string; body: string }) => mentionAction("update_record_note", { note_id: note.id, body: note.body }),
    onSuccess: () => { setEditing(null); void refresh(); },
  });
  const remove = useMutation({
    mutationFn: (id: string) => mentionAction("delete_record_note", { note_id: id }),
    onSuccess: () => void refresh(),
  });

  // Inbox links end in #note-<id>: bring that note into view once loaded.
  const target = hash.startsWith("#note-") ? decodeURIComponent(hash.slice("#note-".length)) : "";
  useEffect(() => {
    if (!target || !notes.data) return;
    if (notes.data.notes.some((n) => n.id === target)) {
      document.getElementById(`note-${target}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    } else if (notes.data.has_more) {
      setLimit((n) => n + PAGE);
    }
  }, [target, notes.data]);

  // Types with their own notes (e.g. CRM leads) or none at all answer 422.
  if (!enabled || (notes.error instanceof ApiError && notes.error.status === 422)) return null;
  const error = add.error ?? save.error ?? remove.error ?? notes.error;

  return (
    <section id="internal-notes" className="scroll-mt-6 rounded-xl bg-surface p-6 ring-1 ring-line shadow-card">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold tracking-tight text-fg">
            {t("recordNotes.title")}
            {!!notes.data?.total && <span className="ml-2 text-sm font-normal text-fg-muted">{notes.data.total}</span>}
          </h3>
          <p className="mt-0.5 inline-flex items-center gap-1 text-xs text-fg-muted">
            <Lock size={12} /> {t("recordNotes.private")}
          </p>
        </div>
        {canWrite && !composing && (
          <Button variant="secondary" size="sm" onClick={() => setComposing(true)}>{t("recordNotes.add")}</Button>
        )}
      </div>

      {error && <p role="alert" className="mb-3 rounded-lg bg-rose-50 p-3 text-sm text-rose-700 ring-1 ring-rose-200">{error.message}</p>}

      {composing && (
        <div className="mb-5 space-y-2">
          <MentionEditor value={draft.body} recipients={draft.recipients} className={editorClass}
            placeholder={t("recordNotes.placeholder")} disabled={add.isPending}
            onChange={(body, recipients) => setDraft((d) => ({ ...d, body, recipients }))} />
          <div className="flex gap-2">
            <Button size="sm" disabled={!draft.body.trim() || add.isPending} onClick={() => add.mutate()}>
              {t("recordNotes.save")}
            </Button>
            <Button size="sm" variant="ghost" disabled={add.isPending}
              onClick={() => { setComposing(false); add.reset(); }}>{t("common.cancel")}</Button>
          </div>
        </div>
      )}

      {notes.isPending && <p className="text-sm text-fg-muted">{t("common.loading")}</p>}
      {notes.data && !notes.data.total && !composing && <p className="text-sm text-fg-muted">{t("recordNotes.empty")}</p>}

      <ol className="space-y-4">
        {notes.data?.notes.map((note) => (
          <li key={note.id} id={`note-${note.id}`}
            className={`scroll-mt-24 rounded-lg p-3 ring-1 ${note.id === target ? "bg-sky-50 ring-sky-200" : "ring-line"}`}>
            <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2 text-xs text-fg-muted">
              <span>
                <strong className="font-medium text-fg">{note.author_name}</strong>{" · "}
                <time dateTime={note.created_at}>{new Date(note.created_at).toLocaleString()}</time>
                {note.edited && <span title={new Date(note.updated_at).toLocaleString()}> · {t("recordNotes.edited")}</span>}
              </span>
              {editing?.id !== note.id && (note.can_edit || note.can_delete) && canWrite && (
                <span className="flex items-center gap-2">
                  {note.can_edit && <button type="button" className="hover:text-fg"
                    onClick={() => { save.reset(); setEditing({ id: note.id, body: note.body, recipients: [] }); }}>
                    {t("common.edit", { defaultValue: "Edit" })}</button>}
                  {note.can_delete && <ConfirmButton onConfirm={() => remove.mutate(note.id)} disabled={remove.isPending}
                    className="hover:text-rose-600">{t("common.delete")}</ConfirmButton>}
                </span>
              )}
            </div>
            {editing?.id === note.id ? (
              <div className="space-y-2">
                <MentionEditor value={editing.body} recipients={editing.recipients} className={editorClass}
                  disabled={save.isPending}
                  onChange={(body, recipients) => setEditing({ ...editing, body, recipients })} />
                <div className="flex gap-2">
                  <Button size="sm" disabled={!editing.body.trim() || save.isPending}
                    onClick={() => save.mutate({ id: note.id, body: editing.body })}>{t("recordNotes.save")}</Button>
                  <Button size="sm" variant="ghost" disabled={save.isPending} onClick={() => setEditing(null)}>
                    {t("common.cancel")}</Button>
                </div>
              </div>
            ) : <NoteBody text={note.body} />}
          </li>
        ))}
      </ol>

      {notes.data?.has_more && (
        <Button variant="ghost" size="sm" className="mt-3" onClick={() => setLimit((n) => n + PAGE)}>
          {t("recordNotes.older")}
        </Button>
      )}
    </section>
  );
}
