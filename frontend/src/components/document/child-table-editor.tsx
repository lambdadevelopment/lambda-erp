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
    <div className="w-full overflow-x-auto rounded-md border border-gray-200">
      <table className="w-full divide-y divide-gray-200 text-sm">
        <thead className="bg-gray-50">
          <tr>
            <th className="w-10 px-2 py-2 text-center text-xs font-medium text-gray-500">
              #
            </th>
            {fields.map((field) => (
              <th
                key={field.name}
                className="px-3 py-2 text-left text-xs font-medium text-gray-500"
              >
                {field.label}
                {field.required && <span className="text-red-500"> *</span>}
              </th>
            ))}
            {!readOnly && (
              <th className="w-10 px-2 py-2 text-center text-xs font-medium text-gray-500" />
            )}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100 bg-white">
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex} className="hover:bg-gray-50">
              <td className="px-2 py-1.5 text-center text-xs text-gray-400">
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
                    className="rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-500"
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
                className="px-4 py-6 text-center text-sm text-gray-400"
              >
                No rows. {!readOnly && "Click the button below to add one."}
              </td>
            </tr>
          )}
        </tbody>
      </table>
      {!readOnly && (
        <div className="border-t border-gray-200 bg-gray-50 px-3 py-2">
          <button
            type="button"
            onClick={addRow}
            className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs font-medium text-blue-600 hover:bg-blue-50"
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
    "w-full rounded border border-gray-200 bg-transparent px-2 py-1 text-sm outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-400";
  const readOnlyClass = "w-full px-2 py-1 text-sm text-gray-700";

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
