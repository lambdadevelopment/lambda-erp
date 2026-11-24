// The steps the assistant took for an answer: one small bubble per tool call,
// labelled with the model's own `step_label` or, without one, a readable line
// built from the tool and its arguments ("Changed sales invoice SINV-0005").
// A click on a bubble shows what exactly was called and what came back. Once
// the answer is there, the steps fold into one line ("5 steps") that opens
// them again; the history brings them back after a reload. Since 1.4.30.
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Check, ChevronDown, ChevronRight, Loader2, X } from "lucide-react";
import type { ChatMessage } from "@/components/chat/chat-provider";

export interface ChatStep {
  tool: string;
  intent?: string;
  args?: Record<string, unknown>;
  /** undefined while the tool runs (or when no result was stored). */
  success?: boolean;
  summary?: string;
  error?: string;
}

/** Live tool_call / tool_result messages as steps: each result joins the latest open call of its tool. */
export function stepsFromMessages(messages: ChatMessage[]): ChatStep[] {
  const steps: ChatStep[] = [];
  for (const message of messages) {
    if (message.type === "tool_call") {
      steps.push({ tool: message.tool ?? "", intent: message.intent, args: message.args });
      continue;
    }
    if (message.type !== "tool_result") continue;
    const result = { success: message.success, summary: message.content, error: message.error };
    let open = -1;
    for (let i = steps.length - 1; i >= 0; i--) {
      if (steps[i].tool === message.tool && steps[i].success === undefined) { open = i; break; }
    }
    if (open >= 0) steps[open] = { ...steps[open], ...result };
    else if (steps.length && steps[steps.length - 1].tool === message.tool) steps[steps.length - 1] = { ...steps[steps.length - 1], ...result };
    else steps.push({ tool: message.tool ?? "", ...result });
  }
  return steps;
}

const text = (value: unknown) => (typeof value === "string" || typeof value === "number" ? String(value) : "");

function typeName(t: TFunction, group: "doctypes" | "masters", slug: unknown, form: "one" | "other" = "one"): string {
  const key = text(slug);
  if (!key) return "";
  return t(`${group}.${key}.${form}`, { defaultValue: key.replace(/[-_]/g, " ") });
}

function humanize(tool: string): string {
  const words = tool.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** A readable one-line label for a step without a `step_label` of its own. */
export function stepLabel(step: ChatStep, t: TFunction): string {
  if (step.intent) return step.intent;
  const a = step.args ?? {};
  const doctype = typeName(t, "doctypes", a.doctype);
  const master = typeName(t, "masters", a.master_type);
  const name = text(a.name);
  const doc = [doctype, name].filter(Boolean).join(" ");
  const record = [master, name].filter(Boolean).join(" ");
  const vars = {
    doc, doctype, master, record, name,
    doctypes: typeName(t, "doctypes", a.doctype, "other"),
    masters: typeName(t, "masters", a.master_type, "other"),
    target: typeName(t, "doctypes", text(a.target_doctype).toLowerCase().replace(/ /g, "-")),
    report: text(a.report_type).replace(/-/g, " "),
    template: text(a.template),
    count: Array.isArray(a.records) ? a.records.length : Array.isArray(a.updates) ? a.updates.length : 0,
  };
  const key = `chatSteps.tools.${step.tool}`;
  const label = t(key, vars);
  return label && label !== key ? label.replace(/\s+/g, " ").trim() : humanize(step.tool);
}

function StepStatus({ step, running }: { step: ChatStep; running: boolean }) {
  if (step.success === true) return <Check className="h-3.5 w-3.5 shrink-0 text-emerald-600" />;
  if (step.success === false) return <X className="h-3.5 w-3.5 shrink-0 text-rose-600" />;
  if (running) return <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-fg-muted" />;
  return <span className="h-3.5 w-3.5 shrink-0 text-center leading-[14px] text-fg-muted">·</span>;
}

function StepBubble({ step, running }: { step: ChatStep; running: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const label = stepLabel(step, t);
  const failed = step.success === false;
  return (
    <div className={`max-w-[80%] rounded-lg text-xs ring-1 ring-inset ${failed ? "bg-rose-50 ring-rose-200" : "bg-surface ring-line"}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        title={step.tool}
        className="flex w-full min-w-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-left text-fg-muted hover:text-fg focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
      >
        <StepStatus step={step} running={running} />
        <span className={`min-w-0 truncate ${failed ? "text-rose-700" : "text-fg"}`}>{label}</span>
        {open ? <ChevronDown className="ml-auto h-3.5 w-3.5 shrink-0" /> : <ChevronRight className="ml-auto h-3.5 w-3.5 shrink-0" />}
      </button>
      {open && (
        <div className="space-y-1.5 border-t border-line/70 px-2.5 py-2">
          <div className="font-mono text-[11px] font-medium text-fg-muted">{step.tool}</div>
          {step.args && Object.keys(step.args).length > 0 && (
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] text-fg-muted/90">
              {JSON.stringify(step.args, null, 2)}
            </pre>
          )}
          {step.error && <div className="whitespace-pre-wrap break-words text-[11px] text-rose-700">{step.error}</div>}
          {step.summary && !(step.error && step.summary.includes(step.error)) && (
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] text-fg-muted">
              {step.summary}
            </pre>
          )}
          {step.success === undefined && !running && (
            <div className="text-[11px] text-fg-muted">{t("chatSteps.noResult", { defaultValue: "No result was recorded." })}</div>
          )}
        </div>
      )}
    </div>
  );
}

export function StepList({ steps, running = false }: { steps: ChatStep[]; running?: boolean }) {
  return (
    <div className="flex flex-col items-start gap-1">
      {steps.map((step, index) => <StepBubble key={index} step={step} running={running && index === steps.length - 1} />)}
    </div>
  );
}

/** The steps of a finished answer, folded into one line that opens them. */
export function StepsSummary({ steps }: { steps: ChatStep[] }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (!steps.length) return null;
  const failed = steps.filter((step) => step.success === false).length;
  return (
    <div className="flex flex-col items-start gap-1">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs text-fg-muted ring-1 ring-line hover:bg-surface-subtle hover:text-fg focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
      >
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        {t("chatSteps.count", { count: steps.length })}
        {failed > 0 && <span className="text-rose-600">· {t("chatSteps.failed", { count: failed })}</span>}
      </button>
      {open && <StepList steps={steps} />}
    </div>
  );
}
