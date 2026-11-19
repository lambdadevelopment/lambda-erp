import { useRef, useState } from "react";
import { Check, Pencil, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { type ChatSession, useChat } from "./chat-provider";

const iconButton = "shrink-0 rounded p-1.5 text-fg-muted hover:bg-surface-subtle hover:text-fg disabled:opacity-40";

export function ChatTitle({ session }: { session: ChatSession }) {
  const { t } = useTranslation();
  const { renameSession } = useChat();
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const pencil = useRef<HTMLButtonElement>(null);

  function finish() {
    setDraft(null);
    setFailed(false);
    requestAnimationFrame(() => pencil.current?.focus());
  }

  async function save() {
    const title = draft?.trim();
    if (!title || saving) return;
    setSaving(true);
    setFailed(false);
    try {
      await renameSession(session.id, title);
      finish();
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  }

  if (draft === null) return (
    <div className="flex min-w-0 items-center gap-1">
      <h1 title={session.title} className="truncate text-base font-semibold text-gray-500 md:text-lg">{session.title}</h1>
      <button ref={pencil} className={iconButton} title={t("chat.rename")} aria-label={t("chat.rename")}
        onClick={() => setDraft(session.title)}><Pencil size={15} /></button>
    </div>
  );

  return (
    <form className="relative flex w-full max-w-md min-w-0 items-center gap-1"
      onSubmit={event => { event.preventDefault(); void save(); }}
      onKeyDown={event => { if (event.key === "Escape" && !saving) { event.preventDefault(); finish(); } }}>
      <input aria-label={t("chat.rename")} className="min-w-0 flex-1 rounded border border-line px-2 py-1 text-sm"
        autoFocus onFocus={event => event.target.select()} value={draft} disabled={saving}
        onChange={event => { setDraft(event.target.value); setFailed(false); }} />
      <button className={iconButton} type="submit" aria-label={t("common.save")} title={t("common.save")}
        disabled={saving || !draft.trim()}><Check size={16} /></button>
      <button className={iconButton} type="button" aria-label={t("common.cancel")} title={t("common.cancel")}
        disabled={saving} onClick={finish}><X size={16} /></button>
      {failed && <span role="alert" className="absolute left-0 top-full z-50 mt-1 rounded bg-surface px-2 py-1 text-sm text-rose-700 shadow">
        {t("chat.renameFailed")}
      </span>}
    </form>
  );
}
