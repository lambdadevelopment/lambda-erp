/**
 * "Back to where I came from" for the header arrow.
 *
 * The header arrow normally goes up to the record's list (see app-shell): the
 * URL decides, not browser history, because paging through records would make
 * history merely reopen the previous record. A record opened from somewhere
 * that isn't its list (today: a link in the chat) carries that place in the
 * router state as `backTo`; the pager passes it along while paging, so the
 * arrow returns there. Reloading keeps it (the browser stores history state);
 * a new tab or a shared URL falls back to the list.
 */
export type BackState = { backTo?: string; backLabel?: "chat" };

export function readBackState(state: unknown): BackState | null {
  const s = state as BackState | null;
  // Only in-app paths; never a protocol-relative or absolute URL.
  if (!s || typeof s.backTo !== "string" || !s.backTo.startsWith("/") || s.backTo.startsWith("//")) return null;
  return { backTo: s.backTo, backLabel: s.backLabel };
}

/** Same-origin absolute URLs (https://erp.example/masters/item/1) as an in-app path, else null. */
export function sameOriginPath(href: string): string | null {
  try {
    const url = new URL(href, window.location.origin);
    if (url.origin !== window.location.origin) return null;
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return null;
  }
}
