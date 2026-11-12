// Files kept with a document or master record (api/record_attachments.py):
// the customer's order as it arrived, a signed slip, a certificate. They
// never change the record and are never printed with it.
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Paperclip } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConfirmButton } from "@/components/ui/confirm-button";
import { useAuth } from "@/contexts/auth-context";
import { api, ApiError, type RecordAttachment } from "@/api/client";

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function RecordAttachments({ recordType, recordId }: { recordType: string; recordId: string }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  const enabled = !!user && user.role !== "public_manager";
  const canWrite = user?.role === "manager" || user?.role === "admin";
  const key = ["record-attachments", recordType, recordId];

  const list = useQuery({
    queryKey: key,
    queryFn: () => api.listRecordAttachments(recordType, recordId),
    enabled: enabled && !!recordId,
    retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: key });
  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      for (const file of files) await api.uploadRecordAttachment(recordType, recordId, file);
    },
    onSuccess: () => { setError(null); refresh(); },
    onError: (err) => { setError((err as Error).message); refresh(); },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteRecordAttachment(id),
    onSuccess: refresh,
    onError: (err) => setError((err as Error).message),
  });

  if (!enabled || list.isError) return null;
  const files: RecordAttachment[] = list.data?.attachments ?? [];
  if (!canWrite && files.length === 0) return null;

  return (
    <section id="attachments" className="rounded-xl bg-surface p-4 ring-1 ring-line">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-fg">
          <Paperclip size={14} />
          {t("attachments.title", { defaultValue: "Attachments" })}
          {files.length > 0 && <span className="text-fg-muted">({files.length})</span>}
        </h3>
        {canWrite && (
          <>
            <input
              ref={input}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => {
                const chosen = Array.from(e.target.files ?? []);
                e.target.value = "";
                if (chosen.length) upload.mutate(chosen);
              }}
            />
            <Button variant="secondary" size="sm" disabled={upload.isPending} onClick={() => input.current?.click()}>
              {upload.isPending
                ? t("attachments.uploading", { defaultValue: "Uploading…" })
                : t("attachments.add", { defaultValue: "Add file" })}
            </Button>
          </>
        )}
      </div>
      {error && <p className="mb-2 text-sm text-red-600">{error}</p>}
      {files.length === 0 ? (
        <p className="text-sm text-fg-muted">
          {t("attachments.empty", { defaultValue: "No files yet. Keep the order as it arrived, a signed slip or a certificate here." })}
        </p>
      ) : (
        <ul className="divide-y divide-line">
          {files.map((file) => (
            <li key={file.id} className="flex items-center justify-between gap-3 py-2 text-sm">
              <div className="min-w-0">
                <a className="truncate font-medium text-brand hover:underline" href={api.recordAttachmentUrl(file.id)}>
                  {file.filename}
                </a>
                <div className="text-xs text-fg-muted">
                  {size(file.size)}
                  {file.uploaded_by ? ` · ${file.uploaded_by}` : ""}
                  {file.created_at ? ` · ${String(file.created_at).slice(0, 10)}` : ""}
                </div>
              </div>
              {canWrite && (
                <ConfirmButton onConfirm={() => remove.mutate(file.id)}>
                  {t("attachments.remove", { defaultValue: "Remove" })}
                </ConfirmButton>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
