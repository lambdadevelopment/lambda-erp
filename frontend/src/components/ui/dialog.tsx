// Shared, styled confirm/prompt dialogs — a replacement for window.confirm /
// window.prompt. Mount <DialogProvider> once (AppShell does), then anywhere in
// the tree call the useConfirm() / usePrompt() hooks:
//
//   const confirm = useConfirm();
//   if (await confirm({ title: "Delete?", danger: true })) { ... }
//
//   const prompt = usePrompt();
//   const name = await prompt({ title: "Rename", defaultValue: current });
//
// For low-stakes, in-row actions prefer <ConfirmButton> (confirm-button.tsx),
// which arms inline instead of opening a modal.
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type ConfirmOpts = {
  title: string;
  body?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
};

type PromptOpts = ConfirmOpts & {
  defaultValue?: string;
  placeholder?: string;
  inputType?: "text" | "date" | "number";
};

type DialogState =
  | { kind: "confirm"; opts: ConfirmOpts; resolve: (v: boolean) => void }
  | { kind: "prompt"; opts: PromptOpts; resolve: (v: string | null) => void }
  | null;

type DialogApi = {
  confirm: (opts: ConfirmOpts) => Promise<boolean>;
  prompt: (opts: PromptOpts) => Promise<string | null>;
};

const DialogContext = createContext<DialogApi | null>(null);

export function DialogProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const [state, setState] = useState<DialogState>(null);
  const [value, setValue] = useState("");

  const confirm = useCallback(
    (opts: ConfirmOpts) =>
      new Promise<boolean>((resolve) => setState({ kind: "confirm", opts, resolve })),
    [],
  );
  const prompt = useCallback(
    (opts: PromptOpts) =>
      new Promise<string | null>((resolve) => {
        setValue(opts.defaultValue ?? "");
        setState({ kind: "prompt", opts, resolve });
      }),
    [],
  );

  const api = useMemo<DialogApi>(() => ({ confirm, prompt }), [confirm, prompt]);

  const settle = (result: boolean | string | null) => {
    if (!state) return;
    (state.resolve as (v: boolean | string | null) => void)(result);
    setState(null);
  };
  const onConfirm = () => settle(state?.kind === "prompt" ? value : true);
  const onCancel = () => settle(state?.kind === "prompt" ? null : false);

  return (
    <DialogContext.Provider value={api}>
      {children}
      {state && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center p-4"
          role="dialog"
          aria-modal="true"
          onKeyDown={(e) => {
            if (e.key === "Escape") onCancel();
          }}
        >
          <div className="absolute inset-0 bg-black/40 backdrop-blur-[1px]" onClick={onCancel} />
          <div className="relative w-full max-w-md rounded-xl bg-surface p-5 shadow-card ring-1 ring-line">
            <h2 className="text-lg font-semibold text-fg">{state.opts.title}</h2>
            {state.opts.body != null && (
              <div className="mt-2 text-sm text-fg-muted">{state.opts.body}</div>
            )}
            {state.kind === "prompt" && (
              <Input
                autoFocus
                type={state.opts.inputType ?? "text"}
                placeholder={state.opts.placeholder}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") onConfirm();
                }}
                className="mt-3"
              />
            )}
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="secondary" onClick={onCancel}>
                {state.opts.cancelLabel ?? t("common.cancel", { defaultValue: "Cancel" })}
              </Button>
              <Button variant={state.opts.danger ? "danger" : "primary"} onClick={onConfirm}>
                {state.opts.confirmLabel ?? t("common.ok", { defaultValue: "OK" })}
              </Button>
            </div>
          </div>
        </div>
      )}
    </DialogContext.Provider>
  );
}

function useDialog(): DialogApi {
  const ctx = useContext(DialogContext);
  if (!ctx) throw new Error("useConfirm/usePrompt must be used within <DialogProvider>");
  return ctx;
}

/** Returns confirm(opts) => Promise<boolean>. Replaces window.confirm. */
export function useConfirm() {
  return useDialog().confirm;
}

/** Returns prompt(opts) => Promise<string|null>. Replaces window.prompt. */
export function usePrompt() {
  return useDialog().prompt;
}
