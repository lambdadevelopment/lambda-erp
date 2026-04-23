"""
Subscription.

Subscription generates recurring Sales or Purchase Invoices based on a
billing interval. Call process() to check if a new invoice is due and
create it automatically.
"""

from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, getdate, nowdate, add_days
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from datetime import timedelta
from dateutil.relativedelta import relativedelta

class Subscription(Document):
    DOCTYPE = "Subscription"
    CHILD_TABLES = {
        "plans": ("Subscription Plan", None),
    }
    PREFIX = "SUB"

    def validate(self):
        if not self.party_type:
            raise ValidationError("Party Type is required")
        if not self.party:
            raise ValidationError("Party is required")
        if not self.start_date:
            raise ValidationError("Start Date is required")
        if not self.get("plans"):
            raise ValidationError("At least one plan item is required")
        if not self.billing_interval:
            self._data["billing_interval"] = "Monthly"
        if not self.company:
            db = get_db()
            companies = db.get_all("Company", fields=["name"], limit=1)
            if companies:
                self._data["company"] = companies[0]["name"]

        # Initialize billing period
        if not self.current_invoice_start:
            self._data["current_invoice_start"] = self.start_date
        if not self.current_invoice_end:
            self._data["current_invoice_end"] = self._get_next_date(self.start_date)

        self._set_status()

    def _set_status(self):
        if self._data.get("status") == "Cancelled":
            return
        today = getdate(nowdate())
        if self.end_date and getdate(self.end_date) < today:
            self._data["status"] = "Completed"
        elif self.current_invoice_end and getdate(self.current_invoice_end) < today:
            self._data["status"] = "Past Due Date"
        else:
            self._data["status"] = "Active"

    def _get_next_date(self, from_date):
        d = getdate(from_date)
        interval = self.billing_interval or "Monthly"
        if interval == "Monthly":
            d = d + relativedelta(months=1)
        elif interval == "Quarterly":
            d = d + relativedelta(months=3)
        elif interval == "Half-Yearly":
            d = d + relativedelta(months=6)
        elif interval == "Yearly":
            d = d + relativedelta(years=1)
        else:
            d = d + relativedelta(months=1)
        return str(d)

    def process(self):
        """Check if a new invoice should be generated and create it.

        Returns the created invoice dict, or None if no invoice was due.
        """
        if self._data.get("status") in ("Cancelled", "Completed"):
            return None

        today = getdate(nowdate())
        invoice_end = getdate(self.current_invoice_end) if self.current_invoice_end else today

        if today < invoice_end:
            return None  # Not due yet

        # Create invoice
        invoice = self._create_invoice()

        # Advance to next period
        self._data["current_invoice_start"] = self.current_invoice_end
        self._data["current_invoice_end"] = self._get_next_date(self.current_invoice_end)

        # Check if subscription has ended
        if self.end_date and getdate(self.end_date) <= today:
            self._data["status"] = "Completed"
        else:
            self._data["status"] = "Active"

        self._persist()

        return invoice.as_dict()

    def _create_invoice(self):
        if self.party_type == "Customer":
            from lambda_erp.accounting.sales_invoice import SalesInvoice
            invoice = SalesInvoice(
                customer=self.party,
                company=self.company,
                posting_date=nowdate(),
                subscription=self.name,
            )
        else:
            from lambda_erp.accounting.purchase_invoice import PurchaseInvoice
            invoice = PurchaseInvoice(
                supplier=self.party,
                company=self.company,
                posting_date=nowdate(),
                subscription=self.name,
            )

        for plan in self.get("plans"):
            invoice.append("items", _dict(
                item_code=plan.get("item_code"),
                item_name=plan.get("item_name"),
                qty=flt(plan.get("qty", 1)),
                rate=flt(plan.get("rate", 0)),
            ))

        invoice.save()
        return invoice
