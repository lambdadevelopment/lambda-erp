/**
 * Branding / theme configuration.
 *
 * The visual theme is already CSS-variable based (see index.css `:root`
 * tokens, surfaced to Tailwind as `brand`, `surface`, `fg`, `line`, …). A
 * customer deployment built on the published library rebrands by calling
 * `configureBranding` at startup: override the product name, point at a logo,
 * and/or override any of the `:root` design tokens at runtime.
 *
 * For deeper restyling the customer's own Tailwind build (which scans this
 * package — the "consumer scans source" model) can add utilities freely; this
 * helper covers the common case of swapping name/logo/colors without a custom
 * stylesheet.
 */
export interface Branding {
  /** Product name — used for the document title and as the sidebar fallback. */
  appName: string;
  /** Optional logo URL (falls back to the core CSS-mask logo when unset). */
  logoUrl?: string;
  /**
   * CSS custom properties to set on :root, e.g. { "--brand": "260 80% 55%" }.
   * The leading "--" is optional. Values follow the token format in index.css
   * (HSL channel triplets for color tokens).
   */
  tokens?: Record<string, string>;
}

let branding: Branding = { appName: "Lambda ERP" };

export function configureBranding(next: Partial<Branding>) {
  branding = { ...branding, ...next };
  if (typeof document === "undefined") return;
  if (next.appName) document.title = next.appName;
  if (next.tokens) {
    const root = document.documentElement;
    for (const [key, value] of Object.entries(next.tokens)) {
      root.style.setProperty(key.startsWith("--") ? key : `--${key}`, value);
    }
  }
}

export function getBranding(): Branding {
  return branding;
}
