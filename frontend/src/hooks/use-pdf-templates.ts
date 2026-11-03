import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

/** Document types with PDF templates (api/pdf_templates.py DOCTYPES), by slug. */
export const PDF_TEMPLATE_DOCTYPES = [
  { slug: "quotation", doctype: "Quotation", texts: true },
  { slug: "sales-order", doctype: "Sales Order", texts: true },
  { slug: "sales-invoice", doctype: "Sales Invoice", texts: true },
  { slug: "purchase-order", doctype: "Purchase Order", texts: true },
  { slug: "delivery-note", doctype: "Delivery Note", texts: true },
  { slug: "proposal", doctype: "Proposal", texts: false },
  { slug: "reservation", doctype: "Reservation", texts: false },
] as const;

export type PdfTemplateDoctype = (typeof PDF_TEMPLATE_DOCTYPES)[number];

export function pdfTemplateDoctype(value: string | null | undefined): PdfTemplateDoctype | undefined {
  return PDF_TEMPLATE_DOCTYPES.find((entry) => entry.slug === value || entry.doctype === value);
}

/** One PDF layout, as list_pdf_templates returns it (api/pdf_templates.py). */
export interface PdfTemplateSummary {
  template: string;
  title: string;
  doctype: string;
  description: string;
  standard: boolean;
  default: boolean;
  archived: boolean;
  published: boolean;
  unpublished_changes: boolean;
  used_by_documents: number;
  /** Submittable types pin and archive the layout they were issued with. */
  issued?: boolean;
  draft_modified?: string;
  published_at?: string | null;
}

export interface PdfTemplateVersion {
  version: string;
  label: string;
  first_used: string;
  documents: number;
  example_document: string | null;
}

export interface PdfTemplateDetail extends PdfTemplateSummary {
  html?: string; // the built-in layout's source
  draft_html?: string;
  live_html?: string | null;
  versions: PdfTemplateVersion[];
  assets: { filename: string; content_type: string | null; size_bytes: number }[];
  context_reference: string;
  required?: { field: string; variable: string; label: string }[];
  draft_modified_by?: string | null;
  published_by?: string | null;
}

export const PDF_TEMPLATES_KEY = ["pdf-templates"] as const;

/** Templates of one document type (`doctype`), or of all types. */
export function usePdfTemplates(includeArchived = false, enabled = true, doctype?: string) {
  return useQuery({
    queryKey: [...PDF_TEMPLATES_KEY, "list", includeArchived, doctype ?? "all"],
    queryFn: () =>
      api.runAction("list_pdf_templates", {
        include_archived: includeArchived, ...(doctype ? { doctype } : {}),
      }) as Promise<{
        templates: PdfTemplateSummary[];
        defaults: Record<string, string>;
      }>,
    enabled,
    staleTime: 60 * 1000,
  });
}

export interface PdfTextDefaults {
  doctype: string;
  intro_text: string;
  remarks: string;
  placeholders: string[];
}

export function usePdfTextDefaults(enabled = true, doctype = "Quotation") {
  return useQuery({
    queryKey: [...PDF_TEMPLATES_KEY, "text-defaults", doctype],
    queryFn: () => api.runAction("get_pdf_text_defaults", { doctype }) as Promise<PdfTextDefaults>,
    enabled,
    staleTime: 60 * 1000,
  });
}

/** One template; for the built-in "standard" one, `doctype` picks the type's layout. */
export function usePdfTemplate(template: string | undefined, doctype?: string) {
  return useQuery({
    queryKey: [...PDF_TEMPLATES_KEY, "detail", template, doctype ?? ""],
    queryFn: () => api.runAction("get_pdf_template", {
      template, ...(doctype ? { doctype } : {}),
    }) as Promise<PdfTemplateDetail>,
    enabled: !!template,
  });
}
