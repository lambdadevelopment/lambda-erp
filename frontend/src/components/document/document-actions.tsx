import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/dialog";

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
  const confirm = useConfirm();

  const handleSubmit = async () => {
    if (await confirm({
      title: "Submit document?",
      body: "Once submitted it cannot be edited.",
      confirmLabel: "Submit",
    })) {
      onSubmit();
    }
  };

  const handleCancel = async () => {
    if (await confirm({
      title: "Cancel document?",
      body: "This action cannot be undone.",
      confirmLabel: "Cancel document",
      danger: true,
    })) {
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
