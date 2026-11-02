import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

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
  draft_modified_by?: string | null;
  published_by?: string | null;
}

export const PDF_TEMPLATES_KEY = ["pdf-templates"] as const;

export function usePdfTemplates(includeArchived = false, enabled = true) {
  return useQuery({
    queryKey: [...PDF_TEMPLATES_KEY, "list", includeArchived],
    queryFn: () =>
      api.runAction("list_pdf_templates", { include_archived: includeArchived }) as Promise<{
        templates: PdfTemplateSummary[];
        defaults: Record<string, string>;
      }>,
    enabled,
    staleTime: 60 * 1000,
  });
}

export function usePdfTemplate(template: string | undefined) {
  return useQuery({
    queryKey: [...PDF_TEMPLATES_KEY, "detail", template],
    queryFn: () => api.runAction("get_pdf_template", { template }) as Promise<PdfTemplateDetail>,
    enabled: !!template,
  });
}
