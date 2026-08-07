import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useAuth } from "@/contexts/auth-context";

const KEY = ["my-settings"];

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

  const mutation = useMutation({
    mutationFn: (patch: Record<string, string>) => api.updateMySettings(patch),
    onSuccess: (fresh) => qc.setQueryData(KEY, fresh),
  });

  const settings: Record<string, string> = data ?? {};

  const setSetting = (key: string, value: string) => {
    // Optimistic: reflect immediately, then persist (server returns the merged bag).
    const prev = (qc.getQueryData(KEY) as Record<string, string>) ?? {};
    qc.setQueryData(KEY, { ...prev, [key]: value });
    mutation.mutate({ [key]: value });
  };

  return { settings, setSetting, isLoaded: data !== undefined };
}
