import { useEffect, useId, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Bell, Check, RotateCcw, X } from "lucide-react";
import { request } from "@/api/client";
import { useAuth } from "@/contexts/auth-context";

export interface MentionUser { name: string; full_name: string; email: string; mention_text?: string }
const label = (user: MentionUser) => user.full_name || user.email;
const mentionText = (user: MentionUser) => user.mention_text || `@${label(user)}`;
export type MentionAction = <T>(action: string, args: Record<string, unknown>) => Promise<T>;
export const mentionAction = <T,>(action: string, args: unknown) =>
  request<T>(`/actions/${action}`, { method: "POST", body: JSON.stringify(args) });
const searchColleagues = (query: string, signal: AbortSignal) =>
  request<{ users: MentionUser[] }>("/actions/search_mention_users", {
    method: "POST", body: JSON.stringify({ query }), signal,
  });

/** Autocomplete inserts the visible token; the server resolves recipients from the note text. */
export function MentionEditor({ value, recipients, onChange, placeholder, className, disabled = false, searchUsers = searchColleagues }: {
  value: string; recipients: MentionUser[];
  onChange: (value: string, recipients: MentionUser[]) => void;
  placeholder?: string; className?: string; disabled?: boolean;
  searchUsers?: typeof searchColleagues;
}) {
  const { t } = useTranslation();
  const textarea = useRef<HTMLTextAreaElement>(null);
  const container = useRef<HTMLDivElement>(null);
  const [trigger, setTrigger] = useState<{ start: number; end: number; query: string } | null>(null);
  const [users, setUsers] = useState<MentionUser[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(0);
  const listId = useId();
  useEffect(() => {
    if (!trigger) return;
    const abort = new AbortController();
    setUsers([]); setError(""); setLoading(true); setSelected(0);
    const timer = window.setTimeout(() => {
      searchUsers(trigger.query, abort.signal).then(data => { if (!abort.signal.aborted) setUsers(data.users); })
        .catch(e => { if (!abort.signal.aborted) setError(e.message); })
        .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    }, 180);
    return () => { clearTimeout(timer); abort.abort(); };
  }, [trigger?.query, searchUsers]);
  useEffect(() => {
    const outside = (e: PointerEvent) => { if (!container.current?.contains(e.target as Node)) setTrigger(null); };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, []);
  const choose = (user: MentionUser) => {
    if (!trigger) return;
    const inserted = `${mentionText(user)} `;
    onChange(value.slice(0, trigger.start) + inserted + value.slice(trigger.end),
      recipients.some(u => u.name === user.name) ? recipients : [...recipients, user]);
    const cursor = trigger.start + inserted.length;
    setTrigger(null);
    requestAnimationFrame(() => { textarea.current?.focus(); textarea.current?.setSelectionRange(cursor, cursor); });
  };
  return <div ref={container} className="relative">
    <textarea ref={textarea} value={value} disabled={disabled} autoFocus rows={3}
      placeholder={placeholder || t("mentions.placeholder")} className={className}
      aria-label={t("mentions.note")} aria-controls={trigger ? listId : undefined}
      aria-expanded={!!trigger} aria-autocomplete="list"
      aria-activedescendant={trigger && users[selected] ? `${listId}-${selected}` : undefined}
      onChange={e => {
        const next = e.target.value, end = e.target.selectionStart;
        onChange(next, recipients.filter(u => next.includes(mentionText(u))));
        const match = next.slice(0, end).match(/(?:^|\s)@([^\s@]{0,50})$/);
        setTrigger(match ? { start: end - match[1].length - 1, end, query: match[1] } : null);
      }}
      onKeyDown={e => {
        if (!trigger) return;
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setTrigger(null); }
        if (users.length && ["ArrowDown", "ArrowUp"].includes(e.key)) {
          e.preventDefault(); setSelected(i => (i + (e.key === "ArrowDown" ? 1 : users.length - 1)) % users.length);
        }
        if (e.key === "Enter" && users[selected]) { e.preventDefault(); choose(users[selected]); }
      }} />
    {trigger && <div id={listId} role="listbox" aria-label={t("mentions.colleagues")}
      className="absolute z-30 max-h-56 w-full overflow-auto rounded border bg-white shadow-lg">
      {loading && <p className="p-3 text-sm text-gray-500">{t("mentions.loading")}</p>}
      {error && <p role="alert" className="p-3 text-sm text-red-600">{error}</p>}
      {!loading && !error && !users.length && <p className="p-3 text-sm">{t("mentions.noUsers")}</p>}
      {users.map((u, i) => <button key={u.name} id={`${listId}-${i}`} role="option" type="button"
        aria-selected={i === selected} onMouseDown={e => e.preventDefault()} onClick={() => choose(u)}
        className={`block w-full px-3 py-2 text-left text-sm ${i === selected ? "bg-blue-50" : "hover:bg-gray-50"}`}>
        <span className="font-medium">{label(u)}</span><span className="ml-2 text-gray-500">{u.email}</span>
      </button>)}
    </div>}
    <p className="mt-1 text-xs text-gray-500">{t("mentions.hint")}</p>
    <div className="mt-1 flex flex-wrap gap-1">
      {recipients.map(u => <button key={u.name} type="button" disabled={disabled}
        title={u.email} aria-label={t("mentions.remove", { name: label(u) })}
        onClick={() => onChange(value.split(mentionText(u)).join(""), recipients.filter(item => item.name !== u.name))}
        className="inline-flex items-center gap-1 rounded bg-blue-50 px-2 py-1 text-xs text-blue-800">
        {label(u)}<X size={12} />
      </button>)}
    </div>
  </div>;
}

interface Mention {
  id: string; actor_name: string; title: string; excerpt: string; view_url: string;
  created_at: string; read_at: string | null; done_at: string | null;
}
interface Inbox { rows: Mention[]; total: number; has_more: boolean; unread_count: number }

export function MentionInbox({ identity, action = mentionAction, onOpenRecord }: {
  identity?: { name: string; role: string };
  action?: MentionAction;
  onOpenRecord?: (url: string) => void;
} = {}) {
  const { user: signedInUser } = useAuth();
  const user = identity ?? signedInUser;
  const { t } = useTranslation();
  const navigate = useNavigate(), cache = useQueryClient();
  const dialog = useRef<HTMLDialogElement>(null);
  const heading = useId();
  const [open, setOpen] = useState(false);
  const [view, setView] = useState("open");
  const [offset, setOffset] = useState(0);
  const enabled = !!user && user.role !== "public_manager";
  const inbox = useQuery({
    queryKey: ["mentions", user?.name, view, offset],
    queryFn: () => action<Inbox>("list_my_mentions", { view, offset, limit: 25 }),
    enabled, refetchInterval: 30000,
  });
  const update = useMutation({
    mutationFn: ({ id, state }: { id: string; state: string }) =>
      action("set_mention_state", { notification_id: id, state }),
    onSuccess: () => cache.invalidateQueries({ queryKey: ["mentions", user?.name] }),
  });
  useEffect(() => {
    if (open && !dialog.current?.open) dialog.current?.showModal();
    if (!open && dialog.current?.open) dialog.current?.close();
  }, [open]);
  const showRecord = async (item: Mention) => {
    try {
      await update.mutateAsync({ id: item.id, state: "read" });
      setOpen(false);
      if (onOpenRecord) onOpenRecord(item.view_url);
      else navigate(item.view_url);
    } catch { /* Keep the inbox open and display the mutation error. */ }
  };
  if (!enabled) return null;
  return <>
    <button type="button" onClick={() => { setOpen(true); void inbox.refetch(); }}
      className="relative rounded p-1.5 text-gray-600 hover:bg-gray-100"
      aria-label={`${t("mentions.title")} (${inbox.data?.unread_count || 0})`}>
      <Bell size={19} />
      {!!inbox.data?.unread_count && <span className="absolute -right-1 -top-1 inline-flex h-5 min-w-5 items-center justify-center whitespace-nowrap rounded-full bg-blue-600 px-1 text-[10px] leading-none tabular-nums text-white">
        {inbox.data.unread_count > 99 ? "99+" : inbox.data.unread_count}
      </span>}
    </button>
    <dialog ref={dialog} aria-labelledby={heading} onClose={() => setOpen(false)}
      onCancel={e => { e.preventDefault(); setOpen(false); }}
      onKeyDown={e => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setOpen(false); } }}
      onClick={e => { if (e.target === dialog.current && e.clientX < dialog.current.getBoundingClientRect().left) setOpen(false); }}
      className="fixed inset-y-0 left-auto right-0 m-0 h-dvh max-h-none w-full max-w-md border-l bg-white p-0 text-gray-900 shadow-xl backdrop:bg-black/30">
      <div className="flex h-full flex-col">
        <div className="flex items-center justify-between border-b p-4">
          <h2 id={heading} className="font-semibold">{t("mentions.title")}</h2>
          <button type="button" onClick={() => setOpen(false)} aria-label={t("mentions.close")} className="rounded p-1 hover:bg-gray-100"><X size={20} /></button>
        </div>
        <div className="flex gap-2 border-b p-3">
          {["open", "unread", "done"].map(tab => <button key={tab} type="button" aria-pressed={view === tab}
            onClick={() => { setView(tab); setOffset(0); update.reset(); }}
            className={`rounded px-3 py-1.5 text-sm ${view === tab ? "bg-blue-50 text-blue-800" : "hover:bg-gray-50"}`}>
            {t(`mentions.${tab}`)}</button>)}
        </div>
        <div className="flex-1 overflow-y-auto">
          {(inbox.error || update.error) && <p role="alert" className="p-4 text-sm text-red-600">{(inbox.error || update.error)?.message}
            <button type="button" className="ml-2 underline" onClick={() => void inbox.refetch()}>{t("mentions.retry")}</button></p>}
          {inbox.isPending && <p className="p-4 text-sm text-gray-500">{t("mentions.loading")}</p>}
          {inbox.data && !inbox.data.rows.length && <p className="p-4 text-sm text-gray-500">{t("mentions.empty")}</p>}
          {inbox.data?.rows.map(item => <article key={item.id} className={`border-b p-4 ${!item.read_at ? "bg-blue-50/50" : ""}`}>
            <button type="button" disabled={update.isPending} onClick={() => void showRecord(item)} className="w-full text-left">
              <div className="text-sm"><strong>{item.actor_name}</strong> {t("mentions.mentionedYou")}</div>
              <div className="mt-1 font-medium text-blue-700">{item.title}</div>
              <p className="mt-1 whitespace-pre-wrap break-words text-sm text-gray-600">{item.excerpt}</p>
              <time className="mt-2 block text-xs text-gray-400" dateTime={item.created_at}>{new Date(item.created_at).toLocaleString()}</time>
            </button>
            <div className="mt-3 flex gap-3 text-xs">
              <button type="button" disabled={update.isPending} onClick={() => update.mutate({ id: item.id, state: item.done_at ? "reopen" : "done" })}
                className="inline-flex items-center gap-1 rounded px-2 py-1 hover:bg-gray-100">
                {item.done_at ? <RotateCcw size={13} /> : <Check size={13} />}{t(item.done_at ? "mentions.reopen" : "mentions.markDone")}
              </button>
              {!item.done_at && <button type="button" disabled={update.isPending} className="rounded px-2 py-1 hover:bg-gray-100"
                onClick={() => update.mutate({ id: item.id, state: item.read_at ? "unread" : "read" })}>
                {t(item.read_at ? "mentions.markUnread" : "mentions.markRead")}</button>}
            </div>
          </article>)}
        </div>
        <div className="flex justify-between border-t p-3 text-sm">
          <button type="button" disabled={!offset} onClick={() => setOffset(n => Math.max(0, n - 25))}>{t("mentions.previous")}</button>
          <button type="button" disabled={!inbox.data?.has_more} onClick={() => setOffset(n => n + 25)}>{t("mentions.next")}</button>
        </div>
      </div>
    </dialog>
  </>;
}
