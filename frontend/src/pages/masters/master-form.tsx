import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import type { FieldDef } from "@/lib/doctypes";

// ---------------------------------------------------------------------------
// Field registry per master type
// ---------------------------------------------------------------------------

const MASTER_FIELDS: Record<string, FieldDef[]> = {
  customer: [
    { name: "name", label: "ID", type: "text", required: true },
    { name: "customer_name", label: "Customer Name", type: "text", required: true },
    { name: "customer_group", label: "Customer Group", type: "select", options: ["Individual", "Commercial", "Government", "Non-Profit", "Premium"] },
    { name: "territory", label: "Territory", type: "text" },
    { name: "credit_limit", label: "Credit Limit", type: "currency" },
    { name: "email", label: "Email", type: "text" },
    { name: "phone", label: "Phone", type: "text" },
    { name: "address", label: "Address", type: "textarea" },
    { name: "city", label: "City", type: "text" },
    { name: "country", label: "Country", type: "text" },
    { name: "tax_id", label: "Tax ID", type: "text" },
  ],
  supplier: [
    { name: "name", label: "ID", type: "text", required: true },
    { name: "supplier_name", label: "Supplier Name", type: "text", required: true },
    { name: "supplier_group", label: "Supplier Group", type: "select", options: ["Local", "Distributor", "Services", "Raw Materials"] },
    { name: "email", label: "Email", type: "text" },
    { name: "phone", label: "Phone", type: "text" },
    { name: "address", label: "Address", type: "textarea" },
    { name: "city", label: "City", type: "text" },
    { name: "country", label: "Country", type: "text" },
    { name: "tax_id", label: "Tax ID", type: "text" },
  ],
  item: [
    { name: "name", label: "Item Code", type: "text", required: true },
    { name: "item_name", label: "Item Name", type: "text", required: true },
    { name: "item_group", label: "Item Group", type: "select", options: ["Products", "Raw Material", "Services", "Consumable"] },
    { name: "stock_uom", label: "Stock UOM", type: "select", options: ["Nos", "Kg", "Ltr", "Mtr", "Box", "Set"] },
    { name: "standard_rate", label: "Standard Rate", type: "currency" },
    { name: "description", label: "Description", type: "textarea" },
  ],
  warehouse: [
    { name: "warehouse_name", label: "Warehouse Name", type: "text", required: true },
    { name: "warehouse_type", label: "Type", type: "select", options: ["Stores", "Manufacturing", "Transit", "Rejected"] },
    { name: "company", label: "Company", type: "text" },
    { name: "address", label: "Address", type: "textarea" },
  ],
  company: [
    { name: "company_name", label: "Company Name", type: "text", required: true },
    { name: "default_currency", label: "Currency", type: "text", default: "USD" },
    { name: "email", label: "Email", type: "text" },
    { name: "phone", label: "Phone", type: "text" },
    { name: "address", label: "Address", type: "textarea" },
    { name: "city", label: "City", type: "text" },
    { name: "country", label: "Country", type: "text" },
    { name: "tax_id", label: "Tax ID", type: "text" },
  ],
};

const TYPE_LABELS: Record<string, string> = {
  customer: "Customer",
  supplier: "Supplier",
  item: "Item",
  warehouse: "Warehouse",
  company: "Company",
};

const AUTO_NAME_TYPES = new Set(["customer", "supplier", "item", "warehouse"]);

// ---------------------------------------------------------------------------
// MasterFormPage
// ---------------------------------------------------------------------------

export default function MasterFormPage() {
  const { type, name } = useParams<{ type: string; name: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isNew = !name || name === "new";
  const label = TYPE_LABELS[type ?? ""] ?? type ?? "";
  const fields = MASTER_FIELDS[type ?? ""] ?? [];

  const [formData, setFormData] = useState<Record<string, any>>({});

  // Fetch existing record
  const { data: existing, isLoading } = useQuery({
    queryKey: ["master", type, name],
    queryFn: () => api.getMaster(type!, name!),
    enabled: !!type && !!name && !isNew,
  });

  useEffect(() => {
    if (isNew) {
      const defaults: Record<string, any> = {};
      fields.forEach((f) => {
        defaults[f.name] = f.default ?? "";
      });
      setFormData(defaults);
    } else if (existing) {
      setFormData(existing);
    }
  }, [isNew, existing, fields]);

  // Mutations
  const createMut = useMutation({
    mutationFn: (data: any) => api.createMaster(type!, data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["masters", type] });
      navigate(`/masters/${type}/${result.name}`, { replace: true });
    },
  });

  const updateMut = useMutation({
    mutationFn: (data: any) => api.updateMaster(type!, name!, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["master", type, name] });
      queryClient.invalidateQueries({ queryKey: ["masters", type] });
    },
  });

  const deleteMut = useMutation({
    mutationFn: () => api.deleteMaster(type!, name!),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["masters", type] });
      if (result?.status === "disabled") {
        navigate(`/masters/${type}`, {
          replace: true,
          state: {
            notice: `${label} ${name} was disabled instead of deleted because it is referenced by ${result.reason ?? "other records"}.`,
          },
        });
        return;
      }
      navigate(`/masters/${type}`, { replace: true });
    },
  });

  const handleSave = () => {
    if (missingRequiredFields.length > 0) return;
    const payload =
      type === "company" && !formData.name
        ? { ...formData, name: formData.company_name }
        : formData;
    if (isNew) {
      createMut.mutate(payload);
    } else {
      updateMut.mutate(payload);
    }
  };

  const handleDelete = () => {
    if (window.confirm(`Delete this ${label}?`)) {
      deleteMut.mutate();
    }
  };

  const setField = (fieldName: string, value: any) => {
    setFormData((prev) => ({ ...prev, [fieldName]: value }));
  };

  if (!isNew && isLoading) {
    return <p className="text-fg-muted">Loading...</p>;
  }

  const saving = createMut.isPending || updateMut.isPending;
  const missingRequiredFields = fields.filter((field) => {
    if (!field.required || field.readOnly) return false;
    if (isNew && field.name === "name" && AUTO_NAME_TYPES.has(type ?? "")) return false;
    const value = formData[field.name];
    return value === undefined || value === null || String(value).trim() === "";
  });

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-end">
        <div className="flex gap-2">
          <Button onClick={handleSave} disabled={saving || missingRequiredFields.length > 0}>
            {saving ? "Saving..." : "Save"}
          </Button>
          {!isNew && (
            <Button
              variant="danger"
              onClick={handleDelete}
              disabled={deleteMut.isPending}
            >
              Delete
            </Button>
          )}
        </div>
      </div>

      {/* Error display */}
      {(createMut.error || updateMut.error || deleteMut.error) && (
        <div className="rounded-lg bg-rose-50 p-4 text-sm text-rose-700 ring-1 ring-rose-200">
          {(createMut.error ?? updateMut.error ?? deleteMut.error)?.message ??
            "An error occurred"}
        </div>
      )}
      {missingRequiredFields.length > 0 && (
        <div className="rounded-lg bg-amber-50 p-4 text-sm text-amber-800 ring-1 ring-amber-200">
          Required: {missingRequiredFields.map((field) => field.label).join(", ")}
        </div>
      )}
      {formData.disabled === 1 && (
        <div className="rounded-lg bg-amber-50 p-4 text-sm text-amber-800 ring-1 ring-amber-200">
          This {label.toLowerCase()} is disabled. It remains in the system because other records still reference it.
        </div>
      )}

      {/* Form fields */}
      <Card>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {fields.map((field) => {
            // Name field is read-only on edit
            const isNameField = field.name === "name" && !isNew;
            const isReadOnly = isNameField || field.readOnly;
            const isRequired = field.required && !(isNew && field.name === "name" && AUTO_NAME_TYPES.has(type ?? ""));

            if (field.type === "select" && field.options) {
              return (
                <div key={field.name}>
                  <Select
                    label={field.label}
                    options={field.options}
                    value={formData[field.name] ?? ""}
                    required={isRequired}
                    disabled={isReadOnly}
                    onChange={(e) => setField(field.name, e.target.value)}
                  />
                </div>
              );
            }

            if (field.type === "textarea") {
              return (
                <div key={field.name} className="sm:col-span-2">
                  <label className="mb-1.5 block text-sm font-medium text-fg">
                    {field.label}
                  </label>
                  <textarea
                    className="block w-full rounded-lg bg-surface px-3 py-2 text-sm text-fg ring-1 ring-line transition-all placeholder:text-fg-muted/70 focus:outline-none focus:ring-2 focus:ring-brand/30 disabled:bg-surface-subtle disabled:text-fg-muted"
                    rows={3}
                    value={formData[field.name] ?? ""}
                    required={isRequired}
                    readOnly={isReadOnly}
                    disabled={isReadOnly}
                    onChange={(e) => setField(field.name, e.target.value)}
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
              <Input
                key={field.name}
                label={field.label}
                type={inputType}
                step={field.type === "currency" ? "0.01" : undefined}
                required={isRequired}
                readOnly={isReadOnly}
                disabled={isReadOnly}
                value={formData[field.name] ?? ""}
                onChange={(e) =>
                  setField(
                    field.name,
                    inputType === "number"
                      ? parseFloat(e.target.value) || 0
                      : e.target.value,
                  )
                }
              />
            );
          })}
        </div>
      </Card>
    </div>
  );
}
