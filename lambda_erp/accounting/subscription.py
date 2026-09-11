"""
Subscription.

Subscription generates recurring Sales or Purchase Invoices based on a
billing interval. Call process() to check if a new invoice is due and
create it automatically.
"""

from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, getdate, nowdate, now, add_days
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from datetime import timedelta
from dateutil.relativedelta import relativedelta
import math

class Subscription(Document):
    DOCTYPE = "Subscription"
    CHILD_TABLES = {
        "plans": ("Subscription Plan", None),
    }
    PREFIX = "SUB"
    REQUIRED_FIELDS = ('party_type', 'party', 'company', 'start_date', 'plans')
    CHILD_REQUIREMENTS = {'plans': {'required': ['item_code', 'qty', 'rate']}}
    LINK_FIELDS = {'company': 'Company'}
    DYNAMIC_LINK_FIELDS = {'party': ('party_type', {'Customer': 'Customer', 'Supplier': 'Supplier'})}
    CHILD_LINK_FIELDS = {'plans': {'item_code': 'Item'}}
    CONDITIONAL_REQUIREMENTS = (
        'Choose company explicitly when more than one exists. party_type must be Customer or Supplier and party must exist.',
        'billing_interval is Monthly (default), Quarterly, Half-Yearly or Yearly; unsupported intervals are rejected. End Date cannot precede Start Date.',
        'Each plan requires an existing item_code, positive finite qty and explicit nonnegative finite rate (zero is allowed deliberately).',
        'No generic submit/cancel. Use status=Cancelled to stop billing, or discard. Processing creates at most one due period per call; repeat until caught up. Completed means billed through end_date. A final shortened period uses the full plan price (no automatic proration).',
    )

    def validate(self):
        if self.status == 'Discarded' and not self.get('discarded'):
            raise ValidationError('Use discard to stop and discard a Subscription; do not set status=Discarded directly')
        if self.status not in (None, '', 'Active', 'Past Due Date', 'Completed', 'Cancelled', 'Discarded'):
            raise ValidationError('Invalid Subscription status; use Cancelled to stop billing, other billing states are derived')
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
            companies = db.get_all("Company", fields=["name"], limit=2)
            if len(companies) == 1:
                self._data["company"] = companies[0]["name"]
            else:
                raise ValidationError('Company is required; choose the intended company explicitly')
        if self.billing_interval not in ('Monthly', 'Quarterly', 'Half-Yearly', 'Yearly'):
            raise ValidationError('Billing Interval must be Monthly, Quarterly, Half-Yearly or Yearly')
        if self.end_date and getdate(self.end_date) < getdate(self.start_date):
            raise ValidationError('End Date cannot precede Start Date')
        for idx, plan in enumerate(self.get('plans'), 1):
            if not plan.get('item_code'):
                raise ValidationError(f'Subscription plan {idx}: Item Code is required')
            for field in ('qty', 'rate'):
                try:
                    value = float(plan.get(field))
                except (TypeError, ValueError):
                    raise ValidationError(f'Subscription plan {idx}: {field} is required')
                if not math.isfinite(value) or value < 0 or (field == 'qty' and value == 0):
                    raise ValidationError(f'Subscription plan {idx}: invalid {field}')
        self._validate_links()

        # Initialize billing period
        if not self.current_invoice_start:
            self._data["current_invoice_start"] = self.start_date
        if not self.current_invoice_end or getdate(self.current_invoice_end) <= getdate(self.current_invoice_start):
            self._data["current_invoice_end"] = self._get_next_date(self.current_invoice_start)
        if self.end_date and getdate(self.current_invoice_end) > getdate(self.end_date):
            self._data['current_invoice_end'] = self.end_date

        self._set_status()

    def _set_status(self):
        if self._data.get('discarded'):
            self._data['status'] = 'Discarded'
            return
        if self._data.get("status") == "Cancelled":
            return
        today = getdate(nowdate())
        if self.end_date and self.current_invoice_start and getdate(self.current_invoice_start) >= getdate(self.end_date):
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
            raise ValidationError('Unsupported Billing Interval')
        return str(d)

    def process(self):
        db = get_db()
        with db.atomic():
            lock = ' FOR UPDATE' if db.dialect == 'postgres' else ''
            db.sql('SELECT name FROM "Subscription" WHERE name = ?' + lock, [self.name])
            self.reload()
            return self._process()

    def _process(self):
        """Check if a new invoice should be generated and create it.

        Returns the created invoice dict, or None if no invoice was due.
        """
        if self._data.get('discarded') or self._data.get("status") in ("Cancelled", "Discarded"):
            return None

        self.validate()
        if self.status == 'Completed':
            self._persist()
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
        if self.end_date and getdate(self.current_invoice_end) > getdate(self.end_date):
            self._data['current_invoice_end'] = self.end_date
        self._set_status()

        self._data['modified'] = now()
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
            item = get_db().get_value('Item', plan['item_code'], ['item_name', 'stock_uom', 'description'])
            invoice.append("items", _dict(
                item_code=plan.get("item_code"),
                # Resolve defaults here so the invoice does not replace an
                # explicitly free plan (rate=0) with the item's standard rate.
                item_name=plan.get("item_name") or item['item_name'],
                uom=item.get('stock_uom'),
                description=item.get('description'),
                qty=flt(plan.get("qty", 1)),
                rate=flt(plan.get("rate", 0)),
            ))

        invoice.save()
        return invoice
