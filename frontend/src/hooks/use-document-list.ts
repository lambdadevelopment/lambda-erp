import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

export function useDocumentList(
  doctype: string,
  filters?: Record<string, string | number | undefined>,
) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["documents", doctype, filters],
    queryFn: () => api.listDocuments(doctype, filters),
    enabled: !!doctype,
  });

  return { data, isLoading, error, refetch };
}
