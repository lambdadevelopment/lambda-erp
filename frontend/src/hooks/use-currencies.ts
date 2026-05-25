import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

/**
 * Currencies the ERP can transact in — the company base currency plus any
 * currency that has an exchange rate on file or is set as a party/company
 * default. Used to populate the document currency dropdown, so a document can
 * only be denominated in a currency the books can convert back to base.
 *
 * Pass `enabled=false` to skip the fetch (e.g. from fields that aren't currency
 * pickers, keeping React Query's request count down).
 */
export function useCurrencies(company?: string, enabled = true): string[] {
  const { data } = useQuery({
    queryKey: ["currencies", company ?? ""],
    queryFn: () => api.currencies(company),
    enabled,
    staleTime: 5 * 60 * 1000,
  });
  return data?.currencies ?? [];
}
