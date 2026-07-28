// Remembers the list a user was last looking at, per doctype — its filters (so a
// detail page's prev/next stays within that filtered set) and its query string
// (so "back to list" returns to it). Backed by sessionStorage so it survives a
// detail-page reload; scoped per tab. Read by DocPager on the detail page.

export type ListContext = {
  /** Filters as the list built them (status / from_date / to_date / …). */
  filters: Record<string, unknown>;
  /** The list's URL query string (e.g. "?status=Draft"), for back-to-list. */
  search: string;
};

const KEY = "lad.docListContext";

function readAll(): Record<string, ListContext> {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) || "{}");
  } catch {
    return {};
  }
}

export function setListContext(doctype: string, ctx: ListContext): void {
  try {
    const all = readAll();
    all[doctype] = ctx;
    sessionStorage.setItem(KEY, JSON.stringify(all));
  } catch {
    /* sessionStorage unavailable — prev/next just falls back to no filters. */
  }
}

export function getListContext(doctype: string): ListContext | undefined {
  return readAll()[doctype];
}
