"""
Pricing Rule.

Pricing Rules define automatic discounts applied to transaction items.
A rule matches by item_code, quantity threshold, and date range, then
applies either a rate override, a discount percentage, or a discount amount.
"""

from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, getdate, nowdate
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError

class PricingRule(Document):
    DOCTYPE = "Pricing Rule"
    CHILD_TABLES = {}
    PREFIX = "PRULE"

    def validate(self):
        if not self.title:
            raise ValidationError("Title is required")
        if not self.item_code:
            raise ValidationError("Item Code is required")
        if not self._data.get("selling") and not self._data.get("buying"):
            raise ValidationError("At least one of Selling or Buying must be enabled")

        rtype = self.rate_or_discount or "Discount Percentage"
        if rtype == "Rate" and not flt(self.rate):
            raise ValidationError("Rate is required when Rate type is selected")
        if rtype == "Discount Percentage" and not flt(self.discount_percentage):
            raise ValidationError("Discount Percentage is required")
        if rtype == "Discount Amount" and not flt(self.discount_amount):
            raise ValidationError("Discount Amount is required")

        if self.valid_from and self.valid_upto:
            if getdate(self.valid_from) > getdate(self.valid_upto):
                raise ValidationError("Valid From cannot be after Valid Upto")

def apply_pricing_rules(doc):
    """Apply matching pricing rules to a transaction document.

    Called during validate() of Quotation, SO, SI, PO, PI.
    For each item, finds the best matching rule and applies it.
    """
    db = get_db()
    today = nowdate()

    # Determine if selling or buying
    is_selling = hasattr(doc, "customer") and doc.customer
    is_buying = hasattr(doc, "supplier") and doc.supplier

    for item in doc.get("items") or []:
        item_code = item.get("item_code")
        if not item_code:
            continue

        qty = flt(item.get("qty", 0))

        # Query matching rules
        rules = db.sql(
            """
            SELECT * FROM "Pricing Rule"
            WHERE item_code = ?
              AND enabled = 1
              AND (valid_from IS NULL OR valid_from = '' OR valid_from <= ?)
              AND (valid_upto IS NULL OR valid_upto = '' OR valid_upto >= ?)
              AND (min_qty = 0 OR min_qty <= ?)
            ORDER BY priority DESC, min_qty DESC
            LIMIT 1
            """,
            [item_code, today, today, qty],
        )

        if not rules:
            continue

        rule = rules[0]

        # Check selling/buying applicability
        if is_selling and not rule.get("selling"):
            continue
        if is_buying and not rule.get("buying"):
            continue

        rtype = rule.get("rate_or_discount", "Discount Percentage")

        if rtype == "Rate":
            item["rate"] = flt(rule["rate"])
            item["price_list_rate"] = flt(rule["rate"])
            item["discount_percentage"] = 0
            item["discount_amount"] = 0
        elif rtype == "Discount Percentage":
            pct = flt(rule["discount_percentage"])
            item["discount_percentage"] = pct
            rate = flt(item.get("price_list_rate") or item.get("rate", 0))
            item["rate"] = flt(rate * (1 - pct / 100), 2)
        elif rtype == "Discount Amount":
            amt = flt(rule["discount_amount"])
            item["discount_amount"] = amt
            rate = flt(item.get("price_list_rate") or item.get("rate", 0))
            item["rate"] = flt(rate - amt, 2)

        item["pricing_rule"] = rule.get("name")
