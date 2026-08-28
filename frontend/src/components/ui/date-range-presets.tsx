import { useTranslation } from "react-i18next";
import { formatLocalDate } from "@/lib/utils";

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
  id: string;
  labelKey: string;
  range: (now: Date) => [string, string];
}

const PRESETS: Preset[] = [
  {
    id: "today",
    labelKey: "datePresets.today",
    range: (now) => {
      const t = formatLocalDate(now);
      return [t, t];
    },
  },
  {
    id: "thisWeek",
    labelKey: "datePresets.thisWeek",
    range: (now) => [formatLocalDate(startOfWeek(now)), formatLocalDate(endOfWeek(now))],
  },
  {
    id: "thisMonth",
    labelKey: "datePresets.thisMonth",
    range: (now) => [formatLocalDate(startOfMonth(now)), formatLocalDate(endOfMonth(now))],
  },
  {
    id: "thisQuarter",
    labelKey: "datePresets.thisQuarter",
    range: (now) => [formatLocalDate(startOfQuarter(now)), formatLocalDate(endOfQuarter(now))],
  },
  {
    id: "thisYear",
    labelKey: "datePresets.thisYear",
    range: (now) => [formatLocalDate(startOfYear(now)), formatLocalDate(endOfYear(now))],
  },
  {
    id: "lastMonth",
    labelKey: "datePresets.lastMonth",
    range: (now) => {
      const lastMonth = new Date(now.getFullYear(), now.getMonth() - 1, 1);
      return [formatLocalDate(startOfMonth(lastMonth)), formatLocalDate(endOfMonth(lastMonth))];
    },
  },
  {
    id: "lastQuarter",
    labelKey: "datePresets.lastQuarter",
    range: (now) => {
      const lastQuarter = new Date(now.getFullYear(), now.getMonth() - 3, 1);
      return [formatLocalDate(startOfQuarter(lastQuarter)), formatLocalDate(endOfQuarter(lastQuarter))];
    },
  },
  {
    id: "lastYear",
    labelKey: "datePresets.lastYear",
    range: (now) => {
      const lastYear = new Date(now.getFullYear() - 1, 0, 1);
      return [formatLocalDate(startOfYear(lastYear)), formatLocalDate(endOfYear(lastYear))];
    },
  },
];

interface DateRangePresetsProps {
  onSelect: (from: string, to: string) => void;
}

export function DateRangePresets({ onSelect }: DateRangePresetsProps) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap gap-1.5">
      {PRESETS.map((p) => (
        <button
          key={p.id}
          type="button"
          onClick={() => {
            const [from, to] = p.range(new Date());
            onSelect(from, to);
          }}
          className="rounded-full bg-surface px-3 py-1 text-xs text-fg-muted ring-1 ring-line transition-all hover:bg-surface-subtle hover:text-fg hover:ring-brand/30"
        >
          {t(p.labelKey)}
        </button>
      ))}
    </div>
  );
}

interface SingleDatePresetsProps {
  onSelect: (date: string) => void;
}

export function SingleDatePresets({ onSelect }: SingleDatePresetsProps) {
  const { t } = useTranslation();
  const presets: { id: string; labelKey: string; value: (now: Date) => string }[] = [
    { id: "today", labelKey: "datePresets.today", value: (now) => formatLocalDate(now) },
    { id: "endOfThisMonth", labelKey: "datePresets.endOfThisMonth", value: (now) => formatLocalDate(endOfMonth(now)) },
    { id: "endOfLastMonth", labelKey: "datePresets.endOfLastMonth", value: (now) => {
      return formatLocalDate(endOfMonth(new Date(now.getFullYear(), now.getMonth() - 1, 1)));
    } },
    { id: "endOfThisQuarter", labelKey: "datePresets.endOfThisQuarter", value: (now) => formatLocalDate(endOfQuarter(now)) },
    { id: "endOfThisYear", labelKey: "datePresets.endOfThisYear", value: (now) => formatLocalDate(endOfYear(now)) },
    { id: "endOfLastYear", labelKey: "datePresets.endOfLastYear", value: (now) => formatLocalDate(endOfYear(new Date(now.getFullYear() - 1, 0, 1))) },
  ];

  return (
    <div className="flex flex-wrap gap-1.5">
      {presets.map((p) => (
        <button
          key={p.id}
          type="button"
          onClick={() => onSelect(p.value(new Date()))}
          className="rounded-full bg-surface px-3 py-1 text-xs text-fg-muted ring-1 ring-line transition-all hover:bg-surface-subtle hover:text-fg hover:ring-brand/30"
        >
          {t(p.labelKey)}
        </button>
      ))}
    </div>
  );
}
