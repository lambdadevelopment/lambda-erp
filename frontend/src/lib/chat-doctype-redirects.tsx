// Auto-registered redirects for page-less chat-doctypes.
//
// A doctype registered via register_chat_doctype(..., page="<link_field>") has
// no standalone page — it's shown inside a parent (e.g. a contact inside its
// lead). The AI chat may still emit `/app/{slug}/{name}` links for it. This
// fetches /api/chat-doctypes at bootstrap and, for each such doctype, registers
// a route that looks up the record's parent and redirects to the parent's page,
// so those links resolve. Best-effort: if the fetch fails the app still renders
// (the chat's prompt page-rules already point it at the right link).
import { useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api, request } from "@/api/client";
import { registerRoute } from "@/routes";

type PageInfo =
  | { kind: "self"; slug: string }
  | { kind: "via"; link_field: string; parent_slug: string }
  | { kind: "none" };

function ChatDoctypeRedirect({ slug, parentSlug, linkField }: {
  slug: string; parentSlug: string; linkField: string;
}) {
  const { name = "" } = useParams();
  const navigate = useNavigate();
  useEffect(() => {
    let alive = true;
    api
      .getDocument(slug, name)
      .then((doc: any) => {
        if (!alive) return;
        const parent = doc?.[linkField];
        navigate(parent ? `/app/${parentSlug}/${parent}` : `/app/${slug}`, { replace: true });
      })
      .catch(() => { if (alive) navigate("/app", { replace: true }); });
    return () => { alive = false; };
  }, [slug, name, parentSlug, linkField, navigate]);
  return <div className="p-6 text-sm text-fg-muted">…</div>;
}

export async function registerChatDoctypeRedirects(): Promise<void> {
  try {
    const res = await request<{ doctypes: { slug: string; page: PageInfo | null }[] }>(
      "/chat-doctypes",
      { signal: AbortSignal.timeout(1500) },
    );
    for (const d of res.doctypes || []) {
      const page = d.page;
      if (page && page.kind === "via") {
        registerRoute({
          path: `app/${d.slug}/:name`,
          element: (
            <ChatDoctypeRedirect slug={d.slug} parentSlug={page.parent_slug} linkField={page.link_field} />
          ),
        });
      }
    }
  } catch {
    // best-effort — the app renders fine without these fallback redirects.
  }
}
