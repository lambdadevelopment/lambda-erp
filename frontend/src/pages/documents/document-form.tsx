import { useState, useEffect, useCallback, useMemo } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { getDoctypeConfig, type FieldDef, type ChildTableDef } from "@/lib/doctypes";
import { StatusBadge } from "@/components/document/status-badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { formatCurrency, flt } from "@/lib/utils";
import { HintTooltip } from "@/components/ui/hint-tooltip";

// ---------------------------------------------------------------------------
// Link resolution — where does clicking "customer" / "account" / etc. lead?
// ---------------------------------------------------------------------------

// Link doctypes that have a real MasterForm page. Anything else falls through
// to a special route (Account → GL report) or renders as plain text.
const LINKABLE_MASTERS = new Set([
  "customer",
  "supplier",
  "item",
  "warehouse",
]);

export function linkRefHref(linkDoctype: string | undefined | null, value: string): string | null {
  if (!linkDoctype || !value) return null;
  if (LINKABLE_MASTERS.has(linkDoctype)) {
    return `/masters/${linkDoctype}/${encodeURIComponent(value)}`;
  }
  if (linkDoctype === "account") {
    // No standalone Account page — deep-link into the GL report pre-filtered
    // to this account, which is what users actually want to see.
    return `/reports/general-ledger?account=${encodeURIComponent(value)}`;
  }
  // cost_center / company / anything else: no destination yet.
  return null;
}

// ---------------------------------------------------------------------------
// FieldLabel -- label with optional hint tooltip
// ---------------------------------------------------------------------------

function FieldLabel({ label, hint }: { label: string; hint?: string }) {
  return (
    <label className="mb-1 block text-sm font-medium text-gray-700">
      {label}
      {hint && <HintTooltip text={hint} />}
    </label>
  );
}

// ---------------------------------------------------------------------------
// LinkField -- simple search-as-you-type select for master data
// ---------------------------------------------------------------------------

function LinkField({
  field,
  value,
  onChange,
  disabled,
  hideLabel,
}: {
  field: FieldDef;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  hideLabel?: boolean;
}) {
  const [query, setQuery] = useState(value ?? "");
  const [open, setOpen] = useState(false);

  const { data: options } = useQuery({
    queryKey: ["link-search", field.linkDoctype, query],
    queryFn: () => api.searchLink(field.linkDoctype!, query),
    enabled: !!field.linkDoctype && open,
  });

  useEffect(() => {
    setQuery(value ?? "");
  }, [value]);

  if (disabled) {
    return (
      <div>
        {!hideLabel && <label className="mb-1 block text-sm font-medium text-gray-700">{field.label}</label>}
        <p className="py-2 text-sm text-gray-700">{value || "-"}</p>
      </div>
    );
  }

  return (
    <div className="relative">
      <Input
        label={hideLabel ? undefined : field.label}
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 200)}
      />
      {open && options && options.length > 0 && (
        <ul className="absolute z-10 mt-1 max-h-40 w-full overflow-auto rounded-md border border-gray-200 bg-white shadow-lg">
          {options.map((opt: any) => (
            <li
              key={opt.name}
              className="cursor-pointer px-3 py-2 text-sm hover:bg-blue-50"
              onMouseDown={() => {
                onChange(opt.name);
                setQuery(opt.name);
                setOpen(false);
              }}
            >
              {opt.name}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// FieldRenderer
// ---------------------------------------------------------------------------

function FieldRenderer({
  field,
  value,
  onChange,
  readOnly,
  rowData,
}: {
  field: FieldDef;
  value: any;
  onChange: (v: any) => void;
  readOnly: boolean;
  rowData?: Record<string, any>;
}) {
  const isDisabled = readOnly || !!field.readOnly;

  if (isDisabled) {
    // Link fields render as a clickable link in read-only mode when there's a
    // real destination. Matches the sky-600 treatment in document lists.
    if (field.type === "link" && value) {
      const resolvedLinkDoctype =
        field.linkDoctypeField && rowData
          ? String(rowData[field.linkDoctypeField] ?? "")
              .toLowerCase()
              .replace(/\s+/g, "-")
          : field.linkDoctype;
      const href = linkRefHref(resolvedLinkDoctype, String(value));
      if (href) {
        return (
          <div>
            <FieldLabel label={field.label} hint={field.hint} />
            <p className="py-2 text-sm">
              <Link
                to={href}
                className="text-sky-600 hover:text-sky-800 hover:underline"
              >
                {value}
              </Link>
            </p>
          </div>
        );
      }
    }

    let display = value ?? "-";
    if ((field.type === "currency") && typeof value === "number") {
      display = formatCurrency(value);
    }
    return (
      <div>
        <FieldLabel label={field.label} hint={field.hint} />
        <p className="py-2 text-sm text-gray-700">{display}</p>
      </div>
    );
  }

  if (field.type === "link") {
    const resolvedField = field.linkDoctypeField && rowData
      ? { ...field, linkDoctype: rowData[field.linkDoctypeField]?.toLowerCase().replace(/\s+/g, "-") }
      : field;
    return (
      <div>
        <FieldLabel label={field.label} hint={field.hint} />
        <LinkField
          field={resolvedField}
          value={value ?? ""}
          onChange={onChange}
          disabled={isDisabled}
          hideLabel
        />
      </div>
    );
  }

  if (field.type === "select" && field.options) {
    return (
      <div>
        <FieldLabel label={field.label} hint={field.hint} />
        <Select
          options={field.options}
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
    );
  }

  if (field.type === "textarea") {
    return (
      <div>
        <FieldLabel label={field.label} hint={field.hint} />
        <textarea
          className="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          rows={3}
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
    );
  }

  const inputType =
    field.type === "number" || field.type === "currency"
      ? "number"
      : field.type === "date"
        ? "date"
        : "text";

  return (
    <div>
      <FieldLabel label={field.label} hint={field.hint} />
      <Input
        type={inputType}
        step={field.type === "currency" ? "0.01" : undefined}
        value={value ?? ""}
        onChange={(e) =>
          onChange(
            inputType === "number" ? parseFloat(e.target.value) || 0 : e.target.value,
          )
        }
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// ChildTableEditor
// ---------------------------------------------------------------------------

function ChildTableEditor({
  tableDef,
  rows,
  onChange,
  readOnly,
}: {
  tableDef: ChildTableDef;
  rows: any[];
  onChange: (rows: any[]) => void;
  readOnly: boolean;
}) {
  const updateRow = (idx: number, fieldName: string, value: any) => {
    const updated = rows.map((row, i) =>
      i === idx ? { ...row, [fieldName]: value } : row,
    );
    onChange(updated);
  };

  const addRow = () => {
    const blank: any = {};
    tableDef.fields.forEach((f) => {
      blank[f.name] = f.default ?? (f.type === "number" || f.type === "currency" ? 0 : "");
    });
    onChange([...rows, blank]);
  };

  const removeRow = (idx: number) => {
    onChange(rows.filter((_, i) => i !== idx));
  };

  return (
    <Card title={tableDef.label}>
      <div>
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead>
            <tr>
              <th className="px-2 py-2 text-left font-medium text-gray-500">
                #
              </th>
              {tableDef.fields.map((f) => (
                <th
                  key={f.name}
                  className="px-2 py-2 text-left font-medium text-gray-500"
                >
                  {f.label}
                </th>
              ))}
              {!readOnly && (
                <th className="px-2 py-2 text-left font-medium text-gray-500" />
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map((row, idx) => (
              <tr key={idx}>
                <td className="px-2 py-1 text-gray-400">{idx + 1}</td>
                {tableDef.fields.map((f) => (
                  <td key={f.name} className="px-2 py-1">
                    <FieldRenderer
                      field={f}
                      value={row[f.name]}
                      onChange={(v) => updateRow(idx, f.name, v)}
                      readOnly={readOnly}
                      rowData={row}
                    />
                  </td>
                ))}
                {!readOnly && (
                  <td className="px-2 py-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => removeRow(idx)}
                    >
                      Remove
                    </Button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!readOnly && (
        <div className="mt-2">
          <Button variant="secondary" size="sm" onClick={addRow}>
            Add Row
          </Button>
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// DocumentActions
// ---------------------------------------------------------------------------

function DocumentActions({
  isNew,
  doctype,
  name,
  docstatus,
  saving,
  config,
  onSave,
  onSubmit,
  onCancel,
  onConvert,
}: {
  isNew: boolean;
  doctype?: string;
  name?: string;
  docstatus: number;
  saving: boolean;
  config: ReturnType<typeof getDoctypeConfig>;
  onSave: () => void;
  onSubmit: () => void;
  onCancel: () => void;
  onConvert: (targetDoctype: string) => void;
}) {
  if (!config) return null;

  const pdfUrl = !isNew && doctype && name
    ? `/api/documents/${doctype}/${encodeURIComponent(name)}/pdf`
    : null;

  return (
    <div className="flex flex-wrap gap-2">
      {pdfUrl && (
        <Button variant="secondary" onClick={() => window.open(pdfUrl, "_blank")}>
          PDF
        </Button>
      )}
      {docstatus < 1 && (
        <Button onClick={onSave} disabled={saving}>
          {saving ? "Saving..." : "Save"}
        </Button>
      )}
      {!isNew && config.canSubmit && docstatus === 0 && (
        <Button variant="secondary" onClick={onSubmit}>
          Submit
        </Button>
      )}
      {!isNew && config.canCancel && docstatus === 1 && (
        <Button variant="danger" onClick={onCancel}>
          Cancel
        </Button>
      )}
      {!isNew &&
        docstatus === 1 &&
        config.conversions.map((conv) => (
          <Button
            key={conv.targetDoctype}
            variant="secondary"
            onClick={() => onConvert(conv.targetDoctype)}
          >
            {conv.label}
          </Button>
        ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Recalculation helper
// ---------------------------------------------------------------------------

function recalculate(formData: any, config: ReturnType<typeof getDoctypeConfig>) {
  if (!config) return formData;

  const updated = { ...formData };

  // Recalculate item amounts
  const hasItems = config.childTables.some((ct) => ct.key === "items");
  if (hasItems && Array.isArray(updated.items)) {
    updated.items = updated.items.map((item: any) => ({
      ...item,
      amount: flt(item.qty, 2) * flt(item.rate, 2),
    }));

    updated.net_total = updated.items.reduce(
      (sum: number, item: any) => sum + flt(item.amount, 2),
      0,
    );
  }

  // Recalculate taxes
  const hasTaxes = config.childTables.some((ct) => ct.key === "taxes");
  if (hasTaxes && Array.isArray(updated.taxes)) {
    updated.taxes = updated.taxes.map((tax: any) => {
      if (tax.charge_type === "On Net Total") {
        return {
          ...tax,
          tax_amount: flt(updated.net_total, 2) * flt(tax.rate, 2) / 100,
        };
      }
      return tax;
    });

    const totalTax = updated.taxes.reduce(
      (sum: number, t: any) => sum + flt(t.tax_amount, 2),
      0,
    );
    updated.total_taxes_and_charges = totalTax;
    updated.grand_total = flt(updated.net_total, 2) + totalTax;
  }

  return updated;
}

// ---------------------------------------------------------------------------
// Main form page
// ---------------------------------------------------------------------------

export default function DocumentFormPage() {
  const { doctype, name } = useParams<{ doctype: string; name: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const config = getDoctypeConfig(doctype ?? "");
  const isNew = !name;

  const [formData, setFormData] = useState<any>({});

  // Fetch existing document
  const { data: existingDoc, isLoading } = useQuery({
    queryKey: ["document", doctype, name],
    queryFn: () => api.getDocument(doctype!, name!),
    enabled: !!doctype && !!name && !isNew,
  });

  // Initialize formData
  useEffect(() => {
    if (isNew && config) {
      const defaults: any = {};
      config.fields.forEach((f) => {
        if (f.type === "date") {
          defaults[f.name] = new Date().toISOString().split("T")[0];
        } else {
          defaults[f.name] = f.default ?? "";
        }
      });
      config.childTables.forEach((ct) => {
        defaults[ct.key] = [];
      });
      defaults.docstatus = 0;
      setFormData(defaults);
    } else if (existingDoc) {
      setFormData(existingDoc);
    }
  }, [isNew, existingDoc, config]);

  // Mutations
  const createMut = useMutation({
    mutationFn: (data: any) => api.createDocument(doctype!, data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["documents", doctype] });
      navigate(`/app/${doctype}/${result.name}`, { replace: true });
    },
  });

  const updateMut = useMutation({
    mutationFn: (data: any) => api.updateDocument(doctype!, name!, data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["document", doctype, name] });
      queryClient.invalidateQueries({ queryKey: ["documents", doctype] });
      setFormData(result);
    },
  });

  const submitMut = useMutation({
    mutationFn: () => api.submitDocument(doctype!, name!),
    onSuccess: (result) => {
      setFormData(result);
      queryClient.invalidateQueries({ queryKey: ["document", doctype, name] });
      queryClient.invalidateQueries({ queryKey: ["documents", doctype] });
    },
  });

  const cancelMut = useMutation({
    mutationFn: () => api.cancelDocument(doctype!, name!),
    onSuccess: (result) => {
      setFormData(result);
      queryClient.invalidateQueries({ queryKey: ["document", doctype, name] });
      queryClient.invalidateQueries({ queryKey: ["documents", doctype] });
    },
  });

  const convertMut = useMutation({
    mutationFn: (targetDoctype: string) =>
      api.convertDocument(doctype!, name!, targetDoctype),
    onSuccess: (result, targetDoctype) => {
      const targetSlug = targetDoctype.toLowerCase().replace(/\s+/g, "-");
      navigate(`/app/${targetSlug}/${result.name}`);
    },
  });

  // Field change handler with recalculation
  const setField = useCallback(
    (fieldName: string, value: any) => {
      setFormData((prev: any) => {
        const next = { ...prev, [fieldName]: value };
        return recalculate(next, config);
      });
    },
    [config],
  );

  const setChildTable = useCallback(
    (key: string, rows: any[]) => {
      setFormData((prev: any) => {
        const next = { ...prev, [key]: rows };
        return recalculate(next, config);
      });
    },
    [config],
  );

  const handleSave = () => {
    if (isNew) {
      createMut.mutate(formData);
    } else {
      updateMut.mutate(formData);
    }
  };

  const handleSubmit = () => {
    submitMut.mutate();
  };

  const handleCancel = () => {
    cancelMut.mutate();
  };

  const handleConvert = (targetDoctype: string) => {
    convertMut.mutate(targetDoctype);
  };

  if (!config) {
    return (
      <p className="text-gray-500">
        Unknown document type: {doctype}
      </p>
    );
  }

  if (!isNew && isLoading) {
    return <p className="text-gray-500">Loading...</p>;
  }

  const docstatus: number = formData.docstatus ?? 0;
  const readOnly = docstatus >= 1;

  // Separate editable parent fields from computed/totals fields
  const editableFields = config.fields.filter((f) => !f.readOnly);
  const totalsFields = config.fields.filter((f) => f.readOnly);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {!isNew && formData.status && (
            <StatusBadge status={formData.status} />
          )}
        </div>
        <DocumentActions
          isNew={isNew}
          doctype={doctype}
          name={name}
          docstatus={docstatus}
          saving={createMut.isPending || updateMut.isPending}
          config={config}
          onSave={handleSave}
          onSubmit={handleSubmit}
          onCancel={handleCancel}
          onConvert={handleConvert}
        />
      </div>

      {/* Error display */}
      {(createMut.error || updateMut.error || submitMut.error || cancelMut.error) && (
        <div className="rounded-md bg-red-50 p-4 text-sm text-red-700">
          {(createMut.error ?? updateMut.error ?? submitMut.error ?? cancelMut.error)
            ?.message ?? "An error occurred"}
        </div>
      )}

      {/* Parent fields */}
      <Card>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {editableFields.map((field) => (
            <FieldRenderer
              key={field.name}
              field={field}
              value={formData[field.name]}
              onChange={(v) => setField(field.name, v)}
              readOnly={readOnly}
              rowData={formData}
            />
          ))}
        </div>
      </Card>

      {/* Child tables */}
      {config.childTables.map((ct) => (
        <ChildTableEditor
          key={ct.key}
          tableDef={ct}
          rows={formData[ct.key] ?? []}
          onChange={(rows) => setChildTable(ct.key, rows)}
          readOnly={readOnly}
        />
      ))}

      {/* Totals section */}
      {totalsFields.length > 0 && (
        <Card title="Totals">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            {totalsFields.map((field) => (
              <FieldRenderer
                key={field.name}
                field={field}
                value={formData[field.name]}
                onChange={() => {}}
                readOnly={true}
              />
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
