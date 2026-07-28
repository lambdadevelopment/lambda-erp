// Prev/next navigation for a document detail page. Steps through records in the
// same order + filters as the list the user came from (via the doc-list-context
// store), using the backend keyset `/adjacent` endpoint. Works on every doctype;
// exported so a deployment's custom detail page can drop it in too.
//
// Buttons: ‹ = previous (up the list), › = next (down). Keyboard (only when not
// typing in a field): k / ← = prev, j / → = next, Esc / u = back to the list.
// Cmd/Ctrl+S = save (fires even while typing) when an onSave is provided.
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { api } from "@/api/client";
import { getListContext } from "@/lib/doc-list-context";

export function DocPager({ slug, name, onSave }: {
  slug: string;
  name: string;
  /** Called on Cmd/Ctrl+S. Omit if the page has nothing to save. */
  onSave?: () => void;
}) {
  const navigate = useNavigate();
  const ctx = getListContext(slug);

  const { data } = useQuery({
    queryKey: ["adjacent", slug, name, ctx?.filters ?? null],
    queryFn: () => api.adjacentDocument(slug, name, ctx?.filters as any),
  });
  const prev = data?.prev ?? null;
  const next = data?.next ?? null;

  useEffect(() => {
    const goPrev = () => prev && navigate(`/app/${slug}/${prev}`);
    const goNext = () => next && navigate(`/app/${slug}/${next}`);
    const back = () => navigate(`/app/${slug}${ctx?.search || ""}`);

    const onKey = (e: KeyboardEvent) => {
      // Save works even mid-edit.
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        if (onSave) { e.preventDefault(); onSave(); }
        return;
      }
      const el = document.activeElement as HTMLElement | null;
      const typing = !!el && (/^(input|textarea|select)$/i.test(el.tagName) || el.isContentEditable);
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "j" || e.key === "ArrowRight") { e.preventDefault(); goNext(); }
      else if (e.key === "k" || e.key === "ArrowLeft") { e.preventDefault(); goPrev(); }
      else if (e.key === "Escape" || e.key === "u") { e.preventDefault(); back(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [prev, next, slug, ctx?.search, onSave, navigate]);

  const btn =
    "rounded-md border border-line bg-surface p-1.5 text-fg-muted transition hover:bg-surface-subtle hover:text-fg disabled:cursor-default disabled:opacity-40";

  return (
    <div className="flex items-center gap-1">
      <button
        type="button"
        disabled={!prev}
        title="Previous record (k / ←)"
        onClick={() => prev && navigate(`/app/${slug}/${prev}`)}
        className={btn}
      >
        <ChevronLeft className="h-4 w-4" />
      </button>
      <button
        type="button"
        disabled={!next}
        title="Next record (j / →)"
        onClick={() => next && navigate(`/app/${slug}/${next}`)}
        className={btn}
      >
        <ChevronRight className="h-4 w-4" />
      </button>
    </div>
  );
}
