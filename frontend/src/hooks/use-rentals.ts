import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

/** Fleet availability timeline feed: asset lanes + overlapping reservation bars. */
export function useFleetCalendar(params: {
  from: string;
  to: string;
  warehouse?: string;
  item_code?: string;
}) {
  return useQuery({
    queryKey: ["fleet-calendar", params],
    queryFn: () => api.fleetCalendar(params),
  });
}
