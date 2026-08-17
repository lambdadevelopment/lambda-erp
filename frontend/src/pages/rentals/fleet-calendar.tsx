import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { usePageTitle } from "@/lib/use-page-title";
import { useFleetCalendar } from "@/hooks/use-rentals";
import type { CalendarAsset, CalendarReservation } from "@/api/client";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { LinkField } from "@/components/document/link-field";

// --- date helpers (wall-clock; the hire window is yard-local per ADR-0002) ---
const DAY_MS = 24 * 60 * 60 * 1000;
const COL_PX = 44; // width of one day column
const LABEL_PX = 220; // width of the machine-label column

function ymd(d: Date): string {
  const m = `${d.getMonth() + 1}`.padStart(2, "0");
  const day = `${d.getDate()}`.padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}
function startOfToday(): Date {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d;
}
function addDays(d: Date, n: number): Date {
  return new Date(d.getTime() + n * DAY_MS);
}
function parseDT(s: string): number {
  // "YYYY-MM-DD HH:MM:SS" (or a bare date) -> ms. Treat as local wall-clock.
  return new Date(s.replace(" ", "T")).getTime();
}

const STATUS_BAR: Record<string, string> = {
  Reserved: "bg-amber-400 text-amber-950",
  "On Hire": "bg-blue-500 text-white",
  Returned: "bg-gray-300 text-gray-700",
  Cancelled: "bg-gray-200 text-gray-500 line-through",
};
const STATUS_DOT: Record<string, string> = {
  Available: "bg-green-500",
  "On Hire": "bg-blue-500",
  Maintenance: "bg-amber-500",
  Retired: "bg-gray-400",
};

export default function FleetCalendarPage() {
  usePageTitle("Fleet Calendar");
  const navigate = useNavigate();

  const [winDays, setWinDays] = useState(28);
  const [winStart, setWinStart] = useState<Date>(startOfToday());

  // filter inputs vs applied filters (Apply commits — avoids a refetch per keystroke)
  const [whInput, setWhInput] = useState("");
  const [itemInput, setItemInput] = useState("");
  const [warehouse, setWarehouse] = useState("");
  const [itemCode, setItemCode] = useState("");

  const winEnd = useMemo(() => addDays(winStart, winDays), [winStart, winDays]);
  const winStartMs = winStart.getTime();
  const winSpanMs = winDays * DAY_MS;

  const days = useMemo(
    () => Array.from({ length: winDays }, (_, i) => addDays(winStart, i)),
    [winStart, winDays],
  );

  const { data, isLoading, error } = useFleetCalendar({
    from: ymd(winStart),
    to: ymd(winEnd),
    warehouse: warehouse || undefined,
    item_code: itemCode || undefined,
  });

  const assets: CalendarAsset[] = data?.assets ?? [];
  const byAsset = useMemo(() => {
    const map = new Map<string, CalendarReservation[]>();
    for (const r of data?.reservations ?? []) {
      if (!r.asset) continue; // pooled bookings have no unit lane (see note below)
      const list = map.get(r.asset) ?? [];
      list.push(r);
      map.set(r.asset, list);
    }
    return map;
  }, [data]);

  const pooledCount = (data?.reservations ?? []).filter((r) => !r.asset).length;

  function barGeometry(r: CalendarReservation) {
    const from = parseDT(r.from_datetime);
    const to = parseDT(r.to_datetime);
    const leftMs = Math.max(0, from - winStartMs);
    const rightMs = Math.min(winSpanMs, to - winStartMs);
    const left = (leftMs / winSpanMs) * 100;
    const width = Math.max(0.6, ((rightMs - leftMs) / winSpanMs) * 100);
    return { left, width };
  }

  const apply = () => {
    setWarehouse(whInput);
    setItemCode(itemInput);
  };
  const shift = (deltaDays: number) => setWinStart((d) => addDays(d, deltaDays));

  const trackWidth = winDays * COL_PX;

  return (
    <div className="space-y-4">
      {/* --- controls --- */}
      <div className="flex flex-wrap items-end gap-3">
        <LinkField label="Yard" value={whInput} onChange={setWhInput} linkDoctype="warehouse" readOnly={false} />
        <LinkField label="Machine type" value={itemInput} onChange={setItemInput} linkDoctype="item" readOnly={false} />
        <Button onClick={apply}>Apply</Button>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="secondary" onClick={() => shift(-winDays)}>‹ Prev</Button>
          <Button variant="secondary" onClick={() => setWinStart(startOfToday())}>Today</Button>
          <Button variant="secondary" onClick={() => shift(winDays)}>Next ›</Button>
          <select
            className="rounded border border-gray-300 px-2 py-1 text-sm"
            value={winDays}
            onChange={(e) => setWinDays(Number(e.target.value))}
          >
            <option value={14}>2 weeks</option>
            <option value={28}>4 weeks</option>
            <option value={56}>8 weeks</option>
          </select>
          <Button onClick={() => navigate("/app/reservation/new")}>New booking</Button>
        </div>
      </div>

      {/* --- legend --- */}
      <div className="flex flex-wrap gap-4 text-xs text-gray-500">
        <span><span className="mr-1 inline-block h-3 w-3 rounded bg-amber-400 align-middle" />Reserved</span>
        <span><span className="mr-1 inline-block h-3 w-3 rounded bg-blue-500 align-middle" />On Hire</span>
        <span>{ymd(winStart)} → {ymd(winEnd)}</span>
        {pooledCount > 0 && <span>· {pooledCount} pooled booking(s) not shown on a unit lane</span>}
      </div>

      {error ? (
        <p className="py-8 text-center text-red-500">Could not load the fleet calendar.</p>
      ) : isLoading ? (
        <p className="py-8 text-center text-gray-400">Loading…</p>
      ) : assets.length === 0 ? (
        <p className="py-8 text-center text-gray-400">No assets to show. Add machines under Rentals → Fleet.</p>
      ) : (
        <Card>
          <div className="overflow-x-auto">
            <div style={{ minWidth: LABEL_PX + trackWidth }}>
              {/* header: day columns */}
              <div className="flex border-b bg-gray-50 text-xs text-gray-500">
                <div className="shrink-0 px-3 py-2 font-medium" style={{ width: LABEL_PX }}>Machine</div>
                <div className="flex" style={{ width: trackWidth }}>
                  {days.map((d) => (
                    <div key={d.getTime()} className="shrink-0 border-l py-2 text-center" style={{ width: COL_PX }}>
                      <div>{d.getDate()}</div>
                      <div className="text-[10px] uppercase">
                        {d.toLocaleDateString(undefined, { weekday: "short" })}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* one lane per asset */}
              {assets.map((a) => {
                const bars = byAsset.get(a.name) ?? [];
                return (
                  <div key={a.name} className="flex border-b hover:bg-gray-50">
                    <div className="shrink-0 px-3 py-2" style={{ width: LABEL_PX }}>
                      <div className="flex items-center gap-2 text-sm font-medium">
                        <span className={`inline-block h-2.5 w-2.5 rounded-full ${STATUS_DOT[a.status ?? ""] ?? "bg-gray-300"}`} />
                        {a.asset_tag ?? a.name}
                      </div>
                      <div className="text-xs text-gray-500">{a.item_code} · {a.warehouse ?? "—"}</div>
                    </div>
                    <div className="relative" style={{ width: trackWidth, height: 44 }}>
                      {/* day gridlines */}
                      <div className="absolute inset-0 flex">
                        {days.map((d) => (
                          <div key={d.getTime()} className="shrink-0 border-l" style={{ width: COL_PX }} />
                        ))}
                      </div>
                      {/* reservation bars */}
                      {bars.map((r) => {
                        const { left, width } = barGeometry(r);
                        const cls = STATUS_BAR[r.status ?? ""] ?? "bg-slate-400 text-white";
                        return (
                          <button
                            key={r.name}
                            onClick={() => navigate(`/app/reservation/${encodeURIComponent(r.name)}`)}
                            title={`${r.party ?? "—"} · ${r.from_datetime} → ${r.to_datetime}${r.purpose ? ` · ${r.purpose}` : ""}`}
                            className={`absolute top-1.5 h-8 overflow-hidden truncate rounded px-2 text-left text-xs ${cls}`}
                            style={{ left: `${left}%`, width: `${width}%` }}
                          >
                            {r.party ?? r.status}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
