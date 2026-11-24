import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, apiUrl } from "@/api/client";
import { Button } from "@/components/ui/button";

/** Backend capabilities govern both visibility and execution. Never open a
 * fabricated PDF route and leave an API error in a blank browser tab. */
export function PdfButton({ doctype, name, compact = false }: { doctype: string; name: string; compact?: boolean }) {
  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { data } = useQuery({
    queryKey: ["document-fields", doctype],
    queryFn: () => api.documentFields(doctype),
  });
  if (!data?.pdf?.supported) return null;

  async function generate(event?: { stopPropagation: () => void }) {
    // In a list row the click must not also open the record.
    event?.stopPropagation();
    setError(null);
    setBusy(true);
    const tab = window.open("about:blank", "_blank");
    if (tab) tab.opener = null;
    try {
      const file = await api.generateDocumentPdf(doctype, name);
      const path = `/documents/${encodeURIComponent(doctype)}/${encodeURIComponent(name)}/pdf?artifact_id=${encodeURIComponent(file.artifact_id)}`;
      const url = apiUrl(path);
      if (tab) tab.location.replace(url);
      else window.location.assign(url);
    } catch (err) {
      tab?.close();
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  if (compact) {
    // The PDF column of a list: a link-styled button; the error as its tooltip.
    return (
      <button
        type="button"
        disabled={busy}
        onClick={generate}
        title={error ?? t("common.openPdf", { defaultValue: "Open the PDF" })}
        className={error ? "text-red-600 hover:underline" : "text-brand transition-colors hover:text-brand/80 hover:underline disabled:opacity-50"}
      >
        {busy ? t("common.loading") : error ? t("common.pdf") + " !" : t("common.pdf")}
      </button>
    );
  }
  return <>
    <Button variant="secondary" disabled={busy} onClick={generate}>{busy ? t("common.loading") : t("common.pdf")}</Button>
    {error && <span role="alert" className="text-sm text-red-600">{error}</span>}
  </>;
}
