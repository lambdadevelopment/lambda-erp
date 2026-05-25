import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

/**
 * The company's base/functional currency — what the ledger and all financial
 * statements are expressed in. Reports show this (not per-document currencies).
 * Falls back to USD until the company list loads. Pass a company name to pick
 * its currency in a multi-company setup; otherwise the first company is used.
 */
export function useBaseCurrency(company?: string): string {
  const { data } = useQuery({
    queryKey: ["setup-status"],
    queryFn: () => api.setupStatus(),
    staleTime: 5 * 60 * 1000,
  });
  const companies: any[] = data?.companies ?? [];
  if (company) {
    const match = companies.find(
      (c) => c.name === company || c.company_name === company,
    );
    if (match?.default_currency) return match.default_currency;
  }
  return companies[0]?.default_currency || "USD";
}
