"""
Proposal (Sammelofferte).

A print-only assembly of several *independent* Quotations into one branded,
customer-facing PDF — the partner picks which existing offers to present (each a
lettered position A, B, C…), writes a cover letter, optionally appends a static
PDF (e.g. a price overview), and generates one cohesive document.

Deliberately has NO financial behaviour:
- no submit, no GL, no stock — it is never submitted;
- it never mutates the Quotations it references; it only points at them so the
  PDF can render their line items. The quotations stay fully independent and are
  accepted/converted/billed on their own.

Saving a Proposal exists only so the partner can reopen and duplicate it (so the
cover letter and per-position copy aren't retyped), not to link the offers.
"""

from lambda_erp.model import Document
from lambda_erp.database import get_db
from lambda_erp.exceptions import DocumentStatusError


class Proposal(Document):
    DOCTYPE = "Proposal"
    PREFIX = "PROP"

    # Each child row references one independent Quotation; the appendix PDF is
    # stored out-of-band (Proposal Appendix table) so this CRUD never serialises
    # bytes.
    CHILD_TABLES = {
        "quotations": ("Proposal Item", None),
    }

    LINK_FIELDS = {
        "customer": "Customer",
        "company": "Company",
    }
    CHILD_LINK_FIELDS = {
        "quotations": {"quotation": "Quotation"},
    }

    def submit(self):
        # A Proposal is a print-only assembly — there is nothing to post. Guard
        # against an accidental submit from the chat or API; you "deliver" it by
        # generating its PDF, not by submitting.
        raise DocumentStatusError(
            "A Proposal (Sammelofferte) is print-only and is not submitted. "
            "Generate its PDF instead."
        )

    def before_save(self) -> None:
        # Denormalise the customer's display name so the list view has it without
        # a join. The PDF still looks the name up live at render time.
        if self.customer and not self.customer_name:
            self.customer_name = (
                get_db().get_value("Customer", self.customer, "customer_name")
                or self.customer
            )
