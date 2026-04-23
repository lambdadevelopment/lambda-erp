import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

export function useTrialBalance(params?: Record<string, string>) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["report", "trial-balance", params],
    queryFn: () => api.trialBalance(params),
  });
  return { data, isLoading, error, refetch };
}

export function useGeneralLedger(params?: Record<string, string>) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["report", "general-ledger", params],
    queryFn: () => api.generalLedger(params),
  });
  return { data, isLoading, error, refetch };
}

export function useStockBalance(params?: Record<string, string>) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["report", "stock-balance", params],
    queryFn: () => api.stockBalance(params),
  });
  return { data, isLoading, error, refetch };
}

export function useProfitAndLoss(params?: Record<string, string>) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["report", "profit-and-loss", params],
    queryFn: () => api.profitAndLoss(params),
  });
  return { data, isLoading, error, refetch };
}

export function useBalanceSheet(params?: Record<string, string>) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["report", "balance-sheet", params],
    queryFn: () => api.balanceSheet(params),
  });
  return { data, isLoading, error, refetch };
}

export function useArAging(params?: Record<string, string>) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["report", "ar-aging", params],
    queryFn: () => api.arAging(params),
  });
  return { data, isLoading, error, refetch };
}

export function useApAging(params?: Record<string, string>) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["report", "ap-aging", params],
    queryFn: () => api.apAging(params),
  });
  return { data, isLoading, error, refetch };
}

export function useAnalyticsMetrics() {
  const { data, isLoading } = useQuery({
    queryKey: ["analytics-metrics"],
    queryFn: () => api.analyticsMetrics(),
    staleTime: 60_000,
  });
  return { data, isLoading };
}

export function useAnalytics(params?: Record<string, string>) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["report", "analytics", params],
    queryFn: () => api.analytics(params!),
    enabled: Boolean(params?.metric && params?.group_by),
  });
  return { data, isLoading, error, refetch };
}

export function useRuntimeDatasets() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["report", "runtime-datasets"],
    queryFn: () => api.runtimeDatasets(),
    staleTime: 60_000,
  });
  return { data, isLoading, error, refetch };
}
