// The PDF layout a document prints with (api/pdf_templates.py): its type's
// published templates, or empty for the type's default. Forms built on the
// generic FieldRenderer use the `pdfTemplate` options source instead.
import { useTranslation } from "react-i18next";
import { Select } from "@/components/ui/select";
import { usePdfTemplates } from "@/hooks/use-pdf-templates";

export function PdfLayoutSelect({ doctype, value, onChange, label }: {
  doctype: string; value: string; onChange: (value: string) => void; label: string;
}) {
  const { t } = useTranslation();
  const { data } = usePdfTemplates(true);
  const templates = (data?.templates ?? []).filter((tpl) => tpl.standard || tpl.doctype === doctype);
  const title = (id: string) => {
    const tpl = templates.find((entry) => entry.template === id);
    return tpl?.standard || !tpl ? t("pdfTemplates.standard") : tpl.title;
  };
  const options = [
    { value: "", label: t("pdfTemplates.defaultOption", { name: title(data?.defaults?.[doctype] ?? "standard") }) },
    ...templates
      .filter((tpl) => tpl.published && (!tpl.archived || tpl.template === value))
      .map((tpl) => ({ value: tpl.template, label: tpl.standard ? t("pdfTemplates.standard") : tpl.title })),
  ];
  return <Select label={label} options={options} value={value} placeholder={false}
    onChange={(e) => onChange(e.target.value)} />;
}
