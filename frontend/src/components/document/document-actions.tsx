import { Button } from "@/components/ui/button";

interface ConversionAction {
  label: string;
  onClick: () => void;
}

interface DocumentActionsProps {
  docstatus: number;
  onSave: () => void;
  onSubmit: () => void;
  onCancel: () => void;
  conversions?: ConversionAction[];
  saving: boolean;
}

export function DocumentActions({
  docstatus,
  onSave,
  onSubmit,
  onCancel,
  conversions = [],
  saving,
}: DocumentActionsProps) {
  const handleSubmit = () => {
    if (window.confirm("Are you sure you want to submit this document? Once submitted it cannot be edited.")) {
      onSubmit();
    }
  };

  const handleCancel = () => {
    if (window.confirm("Are you sure you want to cancel this document? This action cannot be undone.")) {
      onCancel();
    }
  };

  return (
    <div className="flex items-center gap-2">
      {docstatus === 0 && (
        <>
          <Button
            variant="primary"
            onClick={onSave}
            disabled={saving}
          >
            {saving ? "Saving..." : "Save"}
          </Button>
          <Button variant="secondary" onClick={handleSubmit}>
            Submit
          </Button>
        </>
      )}

      {docstatus === 1 && (
        <>
          <Button variant="danger" onClick={handleCancel}>
            Cancel
          </Button>
          {conversions.map((conversion) => (
            <Button
              key={conversion.label}
              variant="secondary"
              onClick={conversion.onClick}
            >
              {conversion.label}
            </Button>
          ))}
        </>
      )}
    </div>
  );
}
