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

/** Format a date string for display. */
export function formatDate(value: string | null | undefined) {
  if (!value) return "";
  return new Date(value).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** Safely parse a float, defaulting to 0. */
export function flt(value: unknown, precision?: number): number {
  const n = parseFloat(String(value ?? 0)) || 0;
  return precision !== undefined ? parseFloat(n.toFixed(precision)) : n;
}
