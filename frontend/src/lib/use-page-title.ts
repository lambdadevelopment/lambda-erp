import { useEffect } from "react";
import { getBranding } from "./branding";

/**
 * Set the browser tab title to `"<title> — <appName>"` while the calling
 * component is mounted (just the app name when `title` is empty/null), and reset
 * it to the app name on unmount so pages that don't set a title fall back
 * cleanly.
 *
 * Convention:
 *   - detail pages pass the record's human name — a customer's name, a document
 *     id (e.g. "Plus Medica AG", "SINV-0042");
 *   - list / overview pages pass the section label ("Leads", "Kunden").
 * The app name comes from branding, so white-label deployments show their own.
 */
export function usePageTitle(title?: string | null): void {
  useEffect(() => {
    const brand = getBranding().appName || "Lambda ERP";
    document.title = title ? `${title} — ${brand}` : brand;
    return () => {
      document.title = brand;
    };
  }, [title]);
}
