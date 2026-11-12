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
  // Files dragged over the card; a counter, since entering a child element
  // fires another dragenter before the parent's dragleave.
  const [dragDepth, setDragDepth] = useState(0);
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
  const classify = useMutation({
    mutationFn: ({ id, sensitivity }: { id: string; sensitivity: string | null }) =>
      api.classifyRecordAttachment(id, sensitivity),
    onSuccess: () => { setError(null); refresh(); },
    onError: (err) => setError((err as Error).message),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteRecordAttachment(id),
    onSuccess: refresh,
    onError: (err) => setError((err as Error).message),
  });

  if (!enabled || list.isError) return null;
  const files: RecordAttachment[] = list.data?.attachments ?? [];
  if (!canWrite && files.length === 0) return null;

  // A file's content cannot be hidden field by field, so the whole file counts
  // as a class of sensitive data: by default the one of the record it is kept
  // with. Someone who sees everything can say otherwise per file.
  const classes = list.data?.sensitivity_classes ?? {};
  const defaultClass = list.data?.default_class ?? null;
  const canClassify = canWrite && !user?.restrictions;
  const className = (cls: string) => t(`attachments.class.${cls}`, { defaultValue: cls.replace(/_/g, " ") });
  const defaultLabel = defaultClass
    ? t("attachments.defaultHidden", { defaultValue: "Default: {{name}}", name: className(defaultClass) })
    : t("attachments.defaultOpen", { defaultValue: "Default: not sensitive" });

  const carriesFiles = (e: React.DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
  const dropping = canWrite && dragDepth > 0;
  const dropHandlers = canWrite ? {
    onDragEnter: (e: React.DragEvent) => { if (carriesFiles(e)) { e.preventDefault(); setDragDepth((d) => d + 1); } },
    onDragOver: (e: React.DragEvent) => { if (carriesFiles(e)) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; } },
    onDragLeave: (e: React.DragEvent) => { if (carriesFiles(e)) setDragDepth((d) => Math.max(0, d - 1)); },
    onDrop: (e: React.DragEvent) => {
      if (!carriesFiles(e)) return;
      e.preventDefault();
      setDragDepth(0);
      const dropped = Array.from(e.dataTransfer.files ?? []);
      if (dropped.length && !upload.isPending) upload.mutate(dropped);
    },
  } : {};

  return (
    <section
      id="attachments"
      {...dropHandlers}
      className={`rounded-xl bg-surface p-4 ring-1 transition-colors ${dropping ? "bg-brand/5 ring-2 ring-brand" : "ring-line"}`}
    >
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
      {dropping && (
        <p className="mb-2 rounded-lg border border-dashed border-brand px-3 py-4 text-center text-sm text-brand">
          {t("attachments.drop", { defaultValue: "Drop the files to attach them" })}
        </p>
      )}
      {files.length === 0 ? (
        <p className="text-sm text-fg-muted">
          {t("attachments.empty", { defaultValue: "No files yet. Keep the order as it arrived, a signed slip or a certificate here." })}
          {canWrite ? ` ${t("attachments.dropHint", { defaultValue: "Drag files here or use Add file." })}` : ""}
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
                  {file.sensitivity_class && !canClassify
                    ? ` · ${t("attachments.hiddenWith", { defaultValue: "hidden with {{name}}", name: className(file.sensitivity_class) })}`
                    : ""}
                </div>
              </div>
              {canClassify && (
                <select
                  className="rounded border border-line bg-surface px-2 py-1 text-xs text-fg"
                  title={t("attachments.classifyHint", { defaultValue: "Who does not see this kind of data does not get the file." })}
                  value={file.sensitivity ?? ""}
                  disabled={classify.isPending}
                  onChange={(e) => classify.mutate({ id: file.id, sensitivity: e.target.value || null })}
                >
                  <option value="">{defaultLabel}</option>
                  <option value="none">{t("attachments.notSensitive", { defaultValue: "Not sensitive" })}</option>
                  {Object.keys(classes).map((cls) => (
                    <option key={cls} value={cls}>{className(cls)}</option>
                  ))}
                </select>
              )}
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
