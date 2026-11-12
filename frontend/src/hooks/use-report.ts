import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

export function useTrialBalance(params?: Record<string, string>) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["report", "trial-balance", params],
    queryFn: () => api.trialBalance(params),
  });
  return { data, isLoading, error, refetch };
}

export function useChartOfAccounts(params?: Record<string, string>) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["report", "chart-of-accounts", params],
    queryFn: () => api.chartOfAccounts(params),
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

export function useBatchStock(params?: Record<string, string>) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["report", "batch-stock", params],
    queryFn: () => api.batchStock(params),
  });
  return { data, isLoading, error, refetch };
}

export function useSalesBacklog(params?: Record<string, string>) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["report", "sales-backlog", params],
    queryFn: () => api.salesBacklog(params),
  });
  return { data, isLoading, error, refetch };
}

export function usePurchaseBacklog(params?: Record<string, string>) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["report", "purchase-backlog", params],
    queryFn: () => api.purchaseBacklog(params),
  });
  return { data, isLoading, error, refetch };
}

export function useStockAvailability(params?: Record<string, string>) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["report", "stock-availability", params],
    queryFn: () => api.stockAvailability(params),
  });
  return { data, isLoading, error, refetch };
}

export function useStockDetail(params?: Record<string, string>) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["report", "stock-detail", params],
    queryFn: () => api.stockDetail(params),
  });
  return { data, isLoading, error, refetch };
}

export function useBatchMovements(params: Record<string, string> | null) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["report", "batch-movements", params],
    queryFn: () => api.batchMovements(params as Record<string, string>),
    enabled: !!params,
  });
  return { data, isLoading, error };
}

export function useBatchRecipients(params: Record<string, string> | null) {
  const { data, isLoading } = useQuery({
    queryKey: ["report", "batch-recipients", params],
    queryFn: () => api.batchRecipients(params as Record<string, string>),
    enabled: !!params,
  });
  return { data, isLoading };
}

export function useBatchChanges(params: Record<string, string> | null) {
  const { data, isLoading } = useQuery({
    queryKey: ["report", "batch-changes", params],
    queryFn: () => api.batchChanges(params as Record<string, string>),
    enabled: !!params,
  });
  return { data, isLoading };
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
