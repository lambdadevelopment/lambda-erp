/**
 * "Back to where I came from" for the header arrow.
 *
 * The header arrow normally goes up to the record's list (see app-shell): the
 * URL decides, not browser history, because paging through records would make
 * history merely reopen the previous record. A record opened from somewhere
 * else (a link in a list, in another record, in the chat) carries that place
 * in the router state as `backTo`; the pager passes it along while paging, so
 * the arrow returns there. Reloading keeps it (the browser stores history
 * state); a new tab or a shared URL falls back to the list.
 *
 * Since 1.4.29 the arrow walks back along the whole way the user came: each
 * link passes on `backChain`, the places before `backTo` (most recent first),
 * so list -> invoice -> customer goes back to the invoice, then to the list
 * with its filters. Paging does not add steps. At most MAX_BACK_STEPS are
 * kept, a few hundred bytes in the browser's history entry.
 */
export type BackState = { backTo?: string; backLabel?: "chat"; backChain?: string[] };

export const MAX_BACK_STEPS = 10;

// Only in-app paths; never a protocol-relative or absolute URL.
function inAppPath(value: unknown): value is string {
  return typeof value === "string" && value.startsWith("/") && !value.startsWith("//");
}

export function readBackState(state: unknown): BackState | null {
  const s = state as BackState | null;
  if (!s || !inAppPath(s.backTo)) return null;
  const chain = Array.isArray(s.backChain) ? s.backChain.filter(inAppPath).slice(0, MAX_BACK_STEPS - 1) : [];
  return { backTo: s.backTo, backLabel: s.backLabel, backChain: chain };
}

/**
 * The state for a link from the current page to another record: back from
 * there returns here (the list with its filters, or the record the link was
 * on), and from here on along the way this page was reached. A user opening a
 * customer from an invoice list expects back to return to the invoices.
 */
export function backStateHere(location: { pathname: string; search?: string; state?: unknown }): BackState {
  const before = readBackState(location.state);
  const chain = before ? [before.backTo as string, ...(before.backChain ?? [])] : [];
  return { backTo: `${location.pathname}${location.search || ""}`, backChain: chain.slice(0, MAX_BACK_STEPS - 1) };
}

/** Where the arrow goes from `state`, and the state that page gets: the rest of the way back. */
export function stepBack(state: BackState): { path: string; state: BackState | undefined } {
  const [next, ...rest] = state.backChain ?? [];
  return { path: state.backTo as string, state: next ? { backTo: next, backChain: rest } : undefined };
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
