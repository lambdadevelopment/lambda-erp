function iso(d: Date): string {
  return d.toISOString().split("T")[0];
}

function startOfWeek(d: Date): Date {
  const r = new Date(d);
  const day = r.getDay();
  // Monday = 1; JS Sunday = 0 → treat Sunday as 7
  const diff = day === 0 ? 6 : day - 1;
  r.setDate(r.getDate() - diff);
  return r;
}

function endOfWeek(d: Date): Date {
  const s = startOfWeek(d);
  s.setDate(s.getDate() + 6);
  return s;
}

function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

function endOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth() + 1, 0);
}

function startOfQuarter(d: Date): Date {
  const q = Math.floor(d.getMonth() / 3);
  return new Date(d.getFullYear(), q * 3, 1);
}

function endOfQuarter(d: Date): Date {
  const s = startOfQuarter(d);
  return new Date(s.getFullYear(), s.getMonth() + 3, 0);
}

function startOfYear(d: Date): Date {
  return new Date(d.getFullYear(), 0, 1);
}

function endOfYear(d: Date): Date {
  return new Date(d.getFullYear(), 11, 31);
}

interface Preset {
  label: string;
  range: () => [string, string];
}

const PRESETS: Preset[] = [
  {
    label: "Today",
    range: () => {
      const t = iso(new Date());
      return [t, t];
    },
  },
  {
    label: "This Week",
    range: () => [iso(startOfWeek(new Date())), iso(endOfWeek(new Date()))],
  },
  {
    label: "This Month",
    range: () => [iso(startOfMonth(new Date())), iso(endOfMonth(new Date()))],
  },
  {
    label: "This Quarter",
    range: () => [iso(startOfQuarter(new Date())), iso(endOfQuarter(new Date()))],
  },
  {
    label: "This Year",
    range: () => [iso(startOfYear(new Date())), iso(endOfYear(new Date()))],
  },
  {
    label: "Last Month",
    range: () => {
      const now = new Date();
      const lastMonth = new Date(now.getFullYear(), now.getMonth() - 1, 1);
      return [iso(startOfMonth(lastMonth)), iso(endOfMonth(lastMonth))];
    },
  },
  {
    label: "Last Quarter",
    range: () => {
      const now = new Date();
      const lastQuarter = new Date(now.getFullYear(), now.getMonth() - 3, 1);
      return [iso(startOfQuarter(lastQuarter)), iso(endOfQuarter(lastQuarter))];
    },
  },
  {
    label: "Last Year",
    range: () => {
      const lastYear = new Date(new Date().getFullYear() - 1, 6, 1);
      return [iso(startOfYear(lastYear)), iso(endOfYear(lastYear))];
    },
  },
];

interface DateRangePresetsProps {
  onSelect: (from: string, to: string) => void;
}

export function DateRangePresets({ onSelect }: DateRangePresetsProps) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {PRESETS.map((p) => (
        <button
          key={p.label}
          type="button"
          onClick={() => {
            const [from, to] = p.range();
            onSelect(from, to);
          }}
          className="rounded-full bg-surface px-3 py-1 text-xs text-fg-muted ring-1 ring-line transition-all hover:bg-surface-subtle hover:text-fg hover:ring-brand/30"
        >
          {p.label}
        </button>
      ))}
    </div>
  );
}

interface SingleDatePresetsProps {
  onSelect: (date: string) => void;
}

export function SingleDatePresets({ onSelect }: SingleDatePresetsProps) {
  const presets: { label: string; value: () => string }[] = [
    { label: "Today", value: () => iso(new Date()) },
    { label: "End of This Month", value: () => iso(endOfMonth(new Date())) },
    { label: "End of Last Month", value: () => {
      const now = new Date();
      return iso(endOfMonth(new Date(now.getFullYear(), now.getMonth() - 1, 1)));
    } },
    { label: "End of This Quarter", value: () => iso(endOfQuarter(new Date())) },
    { label: "End of This Year", value: () => iso(endOfYear(new Date())) },
    { label: "End of Last Year", value: () => iso(endOfYear(new Date(new Date().getFullYear() - 1, 0, 1))) },
  ];

  return (
    <div className="flex flex-wrap gap-1.5">
      {presets.map((p) => (
        <button
          key={p.label}
          type="button"
          onClick={() => onSelect(p.value())}
          className="rounded-full bg-surface px-3 py-1 text-xs text-fg-muted ring-1 ring-line transition-all hover:bg-surface-subtle hover:text-fg hover:ring-brand/30"
        >
          {p.label}
        </button>
      ))}
    </div>
  );
}
