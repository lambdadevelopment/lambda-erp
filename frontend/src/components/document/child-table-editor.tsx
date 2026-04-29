import { useCallback } from "react";
import { X, Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import { LinkField } from "@/components/document/link-field";
import type { FieldDef } from "@/lib/doctypes";

interface ChildTableEditorProps {
  fields: FieldDef[];
  rows: any[];
  onChange: (rows: any[]) => void;
  readOnly: boolean;
}

export function ChildTableEditor({
  fields,
  rows,
  onChange,
  readOnly,
}: ChildTableEditorProps) {
  const addRow = useCallback(() => {
    const newRow: Record<string, any> = {};
    for (const field of fields) {
      if (field.default !== undefined) {
        newRow[field.name] = field.default;
      } else if (field.type === "number" || field.type === "currency") {
        newRow[field.name] = 0;
      } else {
        newRow[field.name] = "";
      }
    }
    onChange([...rows, newRow]);
  }, [fields, rows, onChange]);

  const removeRow = useCallback(
    (index: number) => {
      onChange(rows.filter((_, i) => i !== index));
    },
    [rows, onChange],
  );

  const updateCell = useCallback(
    (rowIndex: number, fieldName: string, value: any) => {
      const updated = rows.map((row, i) => {
        if (i !== rowIndex) return row;
        const next = { ...row, [fieldName]: value };
        // Auto-compute amount = qty * rate when either changes
        if (
          (fieldName === "qty" || fieldName === "rate") &&
          "qty" in next &&
          "rate" in next &&
          "amount" in next
        ) {
          next.amount = (parseFloat(next.qty) || 0) * (parseFloat(next.rate) || 0);
        }
        return next;
      });
      onChange(updated);
    },
    [rows, onChange],
  );

  return (
    <div className="w-full overflow-x-auto rounded-lg ring-1 ring-line">
      <table className="w-full divide-y divide-line text-sm">
        <thead className="bg-surface-subtle">
          <tr>
            <th className="w-10 px-2 py-2 text-center text-xs font-medium text-fg-muted">
              #
            </th>
            {fields.map((field) => (
              <th
                key={field.name}
                className="px-3 py-2 text-left text-xs font-medium text-fg-muted"
              >
                {field.label}
                {field.required && <span className="text-rose-500"> *</span>}
              </th>
            ))}
            {!readOnly && (
              <th className="w-10 px-2 py-2 text-center text-xs font-medium text-fg-muted" />
            )}
          </tr>
        </thead>
        <tbody className="divide-y divide-line bg-surface">
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex} className="transition-colors hover:bg-surface-subtle">
              <td className="px-2 py-1.5 text-center text-xs text-fg-muted">
                {rowIndex + 1}
              </td>
              {fields.map((field) => (
                <td key={field.name} className="px-1 py-1">
                  <CellEditor
                    field={field}
                    value={row[field.name]}
                    onChange={(val) => updateCell(rowIndex, field.name, val)}
                    readOnly={readOnly || !!field.readOnly}
                  />
                </td>
              ))}
              {!readOnly && (
                <td className="px-1 py-1 text-center">
                  <button
                    type="button"
                    onClick={() => removeRow(rowIndex)}
                    className="rounded-md p-1 text-fg-muted transition-colors hover:bg-rose-50 hover:text-rose-600"
                    title="Remove row"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </td>
              )}
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td
                colSpan={fields.length + (readOnly ? 1 : 2)}
                className="px-4 py-6 text-center text-sm text-fg-muted"
              >
                No rows. {!readOnly && "Click the button below to add one."}
              </td>
            </tr>
          )}
        </tbody>
      </table>
      {!readOnly && (
        <div className="border-t border-line bg-surface-subtle px-3 py-2">
          <button
            type="button"
            onClick={addRow}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-brand transition-colors hover:bg-brand/5"
          >
            <Plus className="h-3.5 w-3.5" />
            Add Row
          </button>
        </div>
      )}
    </div>
  );
}

function CellEditor({
  field,
  value,
  onChange,
  readOnly,
}: {
  field: FieldDef;
  value: any;
  onChange: (val: any) => void;
  readOnly: boolean;
}) {
  const baseInputClass =
    "w-full rounded-md bg-transparent px-2 py-1 text-sm ring-1 ring-transparent outline-none transition-all focus:bg-surface focus:ring-brand/30 focus:ring-2";
  const readOnlyClass = "w-full px-2 py-1 text-sm text-fg";

  if (readOnly) {
    const display =
      field.type === "currency"
        ? (parseFloat(value) || 0).toFixed(2)
        : String(value ?? "");
    return <span className={readOnlyClass}>{display}</span>;
  }

  if (field.type === "link" && field.linkDoctype) {
    return (
      <LinkField
        label=""
        value={value ?? ""}
        onChange={onChange}
        linkDoctype={field.linkDoctype}
        readOnly={false}
      />
    );
  }

  if (field.type === "select" && field.options) {
    return (
      <select
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        className={cn(baseInputClass, "bg-white")}
      >
        <option value="">--</option>
        {field.options.map((opt) => {
          const v = typeof opt === "string" ? opt : opt.value;
          const l = typeof opt === "string" ? opt : opt.label;
          return (
            <option key={v} value={v}>
              {l}
            </option>
          );
        })}
      </select>
    );
  }

  if (field.type === "number" || field.type === "currency") {
    return (
      <input
        type="number"
        step="any"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value === "" ? 0 : parseFloat(e.target.value))}
        className={cn(baseInputClass, "text-right")}
      />
    );
  }

  if (field.type === "date") {
    return (
      <input
        type="date"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        className={baseInputClass}
      />
    );
  }

  // text, textarea (rendered as single-line in tables)
  return (
    <input
      type="text"
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value)}
      className={baseInputClass}
    />
  );
}
