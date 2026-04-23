import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";

export function useDocument(doctype: string, name: string) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["document", doctype, name],
    queryFn: () => api.getDocument(doctype, name),
    enabled: !!doctype && !!name,
  });

  return { data, isLoading, error, refetch };
}

export function useDocumentMutations(doctype: string) {
  const queryClient = useQueryClient();

  const invalidate = (name: string) => {
    queryClient.invalidateQueries({ queryKey: ["document", doctype, name] });
    queryClient.invalidateQueries({ queryKey: ["documents", doctype] });
  };

  const save = useMutation({
    mutationFn: (params: { name: string; data: any }) =>
      api.updateDocument(doctype, params.name, params.data),
    onSuccess: (_data, variables) => {
      invalidate(variables.name);
    },
  });

  const submit = useMutation({
    mutationFn: (name: string) => api.submitDocument(doctype, name),
    onSuccess: (_data, name) => {
      invalidate(name);
    },
  });

  const cancel = useMutation({
    mutationFn: (name: string) => api.cancelDocument(doctype, name),
    onSuccess: (_data, name) => {
      invalidate(name);
    },
  });

  return { save, submit, cancel };
}
