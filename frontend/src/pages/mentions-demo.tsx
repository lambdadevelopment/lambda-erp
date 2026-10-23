import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { MentionEditor, MentionInbox, type MentionAction, type MentionUser } from "@/components/mentions";

// Browser-only preview. It uses the production components, but never calls the
// mention API or grants an anonymous demo visitor a real user's identity.
const colleagues: MentionUser[] = [
  { name: "alex", full_name: "Alex Morgan", email: "alex@example.invalid" },
  { name: "sam", full_name: "Sam Keller", email: "sam@example.invalid" },
];
const searchUsers = async (query: string) => ({
  users: colleagues.filter(u => `${u.full_name} ${u.email}`.toLowerCase().includes(query.toLowerCase())),
});
interface Note { id: string; body: string; author: string }
interface Notification {
  id: string; recipient: string; note_id: string; actor_name: string;
  title: string; excerpt: string; view_url: string; created_at: string;
  read_at: string | null; done_at: string | null;
}

export default function MentionsDemo() {
  const cache = useQueryClient();
  const [scope] = useState(() => `mentions-preview-${crypto.randomUUID()}`);
  const [person, setPerson] = useState(colleagues[0]);
  const [body, setBody] = useState("");
  const [recipients, setRecipients] = useState<MentionUser[]>([]);
  const [highlight, setHighlight] = useState("");
  const [saved, setSaved] = useState(false);
  const [notes, setNotes] = useState<Note[]>([
    { id: "welcome", body: "@Alex Morgan Please review the customer's delivery requirements.", author: "Sam Keller" },
  ]);
  const notifications = useRef<Notification[]>([{
    id: "welcome", recipient: "alex", note_id: "welcome", actor_name: "Sam Keller",
    title: "Alpine Office AG", excerpt: "@Alex Morgan Please review the customer's delivery requirements.",
    view_url: "#activity-welcome", created_at: new Date().toISOString(), read_at: null, done_at: null,
  }]);
  const identity = { name: `${scope}-${person.name}`, role: "manager" };
  const action: MentionAction = async <T,>(name: string, args: Record<string, unknown>): Promise<T> => {
    const mine = notifications.current.filter(n => n.recipient === person.name);
    if (name === "list_my_mentions") {
      const rows = mine.filter(n => args.view === "done" ? !!n.done_at :
        args.view === "unread" ? !n.done_at && !n.read_at : !n.done_at);
      const offset = Number(args.offset ?? 0), limit = Number(args.limit ?? 25);
      return { rows: rows.slice(offset, offset + limit), total: rows.length,
        unread_count: mine.filter(n => !n.read_at && !n.done_at).length,
        has_more: offset + limit < rows.length } as T;
    }
    if (name === "set_mention_state") {
      const n = mine.find(n => n.id === args.notification_id);
      if (!n) throw new Error("Preview mention unavailable");
      const now = new Date().toISOString();
      if (args.state === "read") n.read_at ||= now;
      if (args.state === "done") { n.done_at ||= now; n.read_at ||= now; }
      if (args.state === "unread") { n.read_at = null; n.done_at = null; }
      if (args.state === "reopen") n.done_at = null;
      return { success: true } as T;
    }
    throw new Error("Unsupported preview action");
  };
  const save = () => {
    if (!body.trim()) return;
    const id = crypto.randomUUID();
    setNotes(rows => [{ id, body: body.trim(), author: person.full_name }, ...rows]);
    for (const recipient of recipients.filter(u => u.name !== person.name)) {
      notifications.current.unshift({
        id: crypto.randomUUID(), recipient: recipient.name, note_id: id, actor_name: person.full_name,
        title: "Alpine Office AG", excerpt: body.trim().slice(0, 240), view_url: `#activity-${id}`,
        created_at: new Date().toISOString(), read_at: null, done_at: null,
      });
    }
    setBody(""); setRecipients([]); setSaved(true);
    void cache.invalidateQueries({ predicate: q => q.queryKey[0] === "mentions" && String(q.queryKey[1]).startsWith(scope) });
  };
  return <div className="min-h-dvh bg-surface-muted text-fg">
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line bg-surface px-5 py-3">
      <Link to="/demo" className="font-semibold">Lambda ERP</Link>
      <div className="flex items-center gap-3">
        <label className="text-sm">Preview as <select aria-label="Preview as" value={person.name}
          onChange={e => { setPerson(colleagues.find(u => u.name === e.target.value)!); setBody(""); setRecipients([]); setSaved(false); }}
          className="ml-2 rounded border border-line bg-surface px-2 py-1.5">
          {colleagues.map(u => <option key={u.name} value={u.name}>{u.full_name}</option>)}
        </select></label>
        <MentionInbox key={person.name} identity={identity} action={action} onOpenRecord={url => {
          const id = url.slice("#activity-".length); setHighlight(id);
          requestAnimationFrame(() => document.getElementById(`activity-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }));
        }} />
      </div>
    </header>
    <main className="mx-auto max-w-3xl space-y-6 px-4 py-8">
      <section className="rounded-lg border border-line bg-surface p-5">
        <h1 className="text-xl font-semibold">Mentions preview</h1>
        <p className="mt-2 text-sm text-fg-muted">Fictional colleagues and a sample CRM record. Changes stay in this page and reset on refresh; no real notifications are sent.</p>
        <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm">
          <li>Open the bell to see Alex's example mention.</li>
          <li>Write a note below, type @Sam, select Sam Keller and save.</li>
          <li>Switch “Preview as” to Sam Keller and open the bell.</li>
          <li>Open the mention, mark it unread or done, then reopen it from Done.</li>
        </ol>
        <p className="mt-3 text-xs text-fg-muted">This previews the shared editor and inbox. Real account permissions, CRM storage and chat tools are tested separately in the CRM staging environment.</p>
      </section>
      <section className="rounded-lg border border-line bg-surface p-5">
        <p className="text-xs text-fg-muted">Sample CRM record</p>
        <h2 className="mb-4 text-lg font-semibold">Alpine Office AG</h2>
        <MentionEditor value={body} recipients={recipients} searchUsers={searchUsers}
          onChange={(value, users) => { setBody(value); setRecipients(users); setSaved(false); }}
          className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm" />
        <div className="mt-3 flex items-center justify-end gap-3">
          {saved && <p role="status" className="text-sm text-fg-muted">Note saved. Switch colleague to check their inbox.</p>}
          <button type="button" disabled={!body.trim()} onClick={save}
            className="rounded-md bg-brand px-4 py-2 text-sm text-white disabled:opacity-40">Save note</button>
        </div>
      </section>
      <section aria-label="Activity" className="space-y-3">
        {notes.map(note => <article id={`activity-${note.id}`} key={note.id}
          className={`scroll-mt-6 rounded-lg border border-line p-4 ${highlight === note.id ? "bg-brand/10 ring-1 ring-brand/30" : "bg-surface"}`}>
          <p className="text-xs text-fg-muted">{note.author} · Internal note</p>
          <p className="mt-2 whitespace-pre-wrap break-words text-sm">{note.body}</p>
        </article>)}
      </section>
    </main>
  </div>;
}
