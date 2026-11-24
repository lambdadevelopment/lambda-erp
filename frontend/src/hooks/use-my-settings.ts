import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useAuth } from "@/contexts/auth-context";

const KEY = ["my-settings"];

// Writes go out one at a time, newest values last. A drag in the column menu
// changes a setting several times a second; parallel requests could finish in
// any order, and an older answer then overwrote the newer choice (on screen
// and on the server). Changes made while a write is out are merged and sent
// next; only the answer to the last write replaces the cached settings.
let pending: Record<string, string> = {};
let writing = false;

async function persist(qc: QueryClient, patch: Record<string, string>) {
  Object.assign(pending, patch);
  if (writing) return;
  writing = true;
  try {
    while (Object.keys(pending).length) {
      const batch = pending;
      pending = {};
      try {
        const fresh = await api.updateMySettings(batch);
        if (!Object.keys(pending).length) qc.setQueryData(KEY, fresh);
      } catch {
        // Keep the optimistic value; the next load shows what the server has.
      }
    }
  } finally {
    writing = false;
  }
}

/**
 * The current user's personal preferences (a key->value bag persisted server-side
 * per user via /auth/my-settings — cross-device, unlike localStorage). Fetched
 * once and cached; `setSetting` writes optimistically then merge-upserts.
 *
 * Consumers: the list column picker (`columns.<doctype>`) and the language
 * selector (`language`). Gated on auth — app pages are always authenticated, so
 * this never fires the 401 path.
 */
export function useMySettings() {
  const qc = useQueryClient();
  const { user } = useAuth();

  const { data } = useQuery({
    queryKey: KEY,
    queryFn: () => api.getMySettings(),
    enabled: !!user,
    staleTime: 5 * 60 * 1000,
  });

  const settings: Record<string, string> = data ?? {};

  const setSetting = (key: string, value: string) => {
    // Optimistic: reflect immediately, then persist (server returns the merged bag).
    const prev = (qc.getQueryData(KEY) as Record<string, string>) ?? {};
    qc.setQueryData(KEY, { ...prev, [key]: value });
    void persist(qc, { [key]: value });
  };

  return { settings, setSetting, isLoaded: data !== undefined };
}
