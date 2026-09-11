import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, apiUrl } from "@/api/client";
import { Button } from "@/components/ui/button";

/** Backend capabilities govern both visibility and execution. Never open a
 * fabricated PDF route and leave an API error in a blank browser tab. */
export function PdfButton({ doctype, name }: { doctype: string; name: string }) {
  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { data } = useQuery({
    queryKey: ["document-fields", doctype],
    queryFn: () => api.documentFields(doctype),
  });
  if (!data?.pdf?.supported) return null;

  async function generate() {
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
  return <>
    <Button variant="secondary" disabled={busy} onClick={generate}>{busy ? t("common.loading") : t("common.pdf")}</Button>
    {error && <span role="alert" className="text-sm text-red-600">{error}</span>}
  </>;
}
