/** Merge class names (simplified clsx). */
export function cn(...classes: (string | false | null | undefined)[]) {
  return classes.filter(Boolean).join(" ");
}

/** Format a number as currency. */
export function formatCurrency(value: number | null | undefined, currency = "USD") {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
  }).format(value ?? 0);
}

/** Format a number with commas. */
export function formatNumber(value: number | null | undefined, decimals = 2) {
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value ?? 0);
}

// Display locale for dates. Default: the viewer's browser locale — the old
// hardcoded "en-US" forced US month/day order (and English month names) on
// every deployment. A deployment can pin it, e.g. setDateLocale("de-CH") for
// DD.MM.YYYY everywhere, regardless of the viewer's browser.
let dateLocale: string | undefined;

/** Pin the locale used by formatDate (undefined = the viewer's browser locale). */
export function setDateLocale(locale: string | undefined) {
  dateLocale = locale;
}

/** Format a Date as a local calendar date without converting it to UTC. */
export function formatLocalDate(value: Date = new Date()): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/** Format a date string for display (numeric, day-first per the locale). */
export function formatDate(value: string | null | undefined) {
  if (!value) return "";
  const calendarDate = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  const parsed = calendarDate
    ? new Date(Number(calendarDate[1]), Number(calendarDate[2]) - 1, Number(calendarDate[3]))
    : new Date(value);
  if (isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString(dateLocale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

/** Format a datetime for display: date + HH:MM. Accepts "YYYY-MM-DD HH:MM:SS"
 * (the ERP's stored form) or ISO. Falls back to the raw value if unparseable. */
export function formatDateTime(value: string | null | undefined) {
  if (!value) return "";
  const d = new Date(value.replace(" ", "T"));
  if (isNaN(d.getTime())) return value;
  return d.toLocaleString(dateLocale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Safely parse a float, defaulting to 0. */
export function flt(value: unknown, precision?: number): number {
  const n = parseFloat(String(value ?? 0)) || 0;
  return precision !== undefined ? parseFloat(n.toFixed(precision)) : n;
}
