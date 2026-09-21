"""
Pricing Rule.

Pricing Rules are the *modifier* layer: automatic discounts and negotiated
overrides applied on top of a base price. The base price itself comes from the
Price List layer (see controllers/item_price.py), falling back to
Item.standard_rate.

A rule matches on what it applies to (an item code or an item group), who it
applies for (nobody in particular, a customer, a customer group, a territory or
a supplier), a quantity band, an amount band and a date range. It then applies
either a rate override, a discount percentage, or a discount amount.

Selection: highest `priority` wins, then the most specific match, then the
tightest quantity band, then name. Priority is the deliberate override — it is
0 by default, so in the common case specificity decides; setting it lets a
campaign beat a customer's standing rate without rewriting either.

One rule applies per line. Rules do not stack.
"""

from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, getdate, nowdate
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError

APPLY_ON = ("Item Code", "Item Group")
APPLICABLE_FOR = ("Customer", "Customer Group", "Territory", "Supplier")

# Which side of the business a document trades on. Derived from the document
# type rather than from whether a party field happens to be populated.
#
# The old heuristic (`hasattr(doc, "customer") and doc.customer`) gives the
# right answer today only because every one of these documents rejects a blank
# party in validate(). That is an implicit coupling between pricing and an
# unrelated required-field check: `POS Invoice.customer` is nullable in the
# schema, so relaxing that check — for a till that rings up anonymous sales —
# would make both flags false, match no rule, and silently price at base rate
# instead of raising. Trade side is a property of the document type; say so.
SELLING_DOCTYPES = {"Quotation", "Sales Order", "Sales Invoice", "POS Invoice"}
BUYING_DOCTYPES = {"Purchase Order", "Purchase Invoice"}


class PricingRule(Document):
    DOCTYPE = "Pricing Rule"
    CHILD_TABLES = {}
    PREFIX = "PRULE"
    LINK_FIELDS = {
        'item_code': 'Item',
        'company': 'Company',
        'customer': 'Customer',
        'supplier': 'Supplier',
    }
    CONDITIONAL_REQUIREMENTS = (
        'A supplied company limits the rule to that company; omitted/blank company makes it global. Selling/buying, dates, quantity and amount are filtered before selecting the applicable rule.',
        'apply_on decides what identifies the line: "Item Code" requires item_code, "Item Group" requires item_group.',
        'applicable_for narrows the rule to one party dimension and requires its matching field (customer, customer_group, territory or supplier). Leave it empty for a rule that applies to everyone.',
        'Among matching rules the highest priority wins, then the most specific match. Only one rule applies per line; rules do not stack.',
    )

    def validate(self):
        if not self.title:
            raise ValidationError("Title is required")

        apply_on = self.apply_on or "Item Code"
        if apply_on not in APPLY_ON:
            raise ValidationError(
                f"Apply On must be one of {', '.join(APPLY_ON)}")
        self._data["apply_on"] = apply_on
        if apply_on == "Item Code" and not self.item_code:
            raise ValidationError("Item Code is required when Apply On is Item Code")
        if apply_on == "Item Group" and not self.item_group:
            raise ValidationError("Item Group is required when Apply On is Item Group")

        applicable_for = self.applicable_for or None
        if applicable_for is not None and applicable_for not in APPLICABLE_FOR:
            raise ValidationError(
                f"Applicable For must be empty or one of {', '.join(APPLICABLE_FOR)}")
        required_for = {
            "Customer": "customer",
            "Customer Group": "customer_group",
            "Territory": "territory",
            "Supplier": "supplier",
        }
        if applicable_for and not self._data.get(required_for[applicable_for]):
            raise ValidationError(
                f"{required_for[applicable_for]} is required when Applicable For is {applicable_for}")

        if not self._data.get("selling") and not self._data.get("buying"):
            raise ValidationError("At least one of Selling or Buying must be enabled")
        if applicable_for in ("Customer", "Customer Group", "Territory") and not self._data.get("selling"):
            raise ValidationError(f"Applicable For {applicable_for} requires Selling")
        if applicable_for == "Supplier" and not self._data.get("buying"):
            raise ValidationError("Applicable For Supplier requires Buying")

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
        if flt(self.max_qty) and flt(self.min_qty) > flt(self.max_qty):
            raise ValidationError("Min Qty cannot be greater than Max Qty")
        if flt(self.max_amt) and flt(self.min_amt) > flt(self.max_amt):
            raise ValidationError("Min Amt cannot be greater than Max Amt")


def _trade_side(doc):
    """(is_selling, is_buying) for a document, by type."""
    doctype = getattr(doc, "DOCTYPE", None)
    if doctype in SELLING_DOCTYPES:
        return True, False
    if doctype in BUYING_DOCTYPES:
        return False, True
    # Unknown/plugin document: fall back to the party field it carries.
    return (
        bool(getattr(doc, "customer", None)),
        bool(getattr(doc, "supplier", None)),
    )


def _party_context(db, doc, is_selling):
    """The party dimensions a rule may match on, for this document."""
    if is_selling:
        customer = doc._data.get("customer")
        if not customer:
            return {"customer": None, "customer_group": None, "territory": None, "supplier": None}
        row = db.get_value("Customer", customer, ["customer_group", "territory"])
        return {
            "customer": customer,
            "customer_group": row.customer_group if row else None,
            "territory": row.territory if row else None,
            "supplier": None,
        }
    supplier = doc._data.get("supplier")
    return {"customer": None, "customer_group": None, "territory": None, "supplier": supplier}


def _specificity(rule):
    """How narrowly a rule is targeted. Breaks ties after priority.

    A rule naming one customer beats one naming their group, which beats one
    that applies to everybody; a rule naming an item beats one naming its
    group. Without this, two equally-prioritised rules would be separated by
    name, and nobody could explain the resulting price.
    """
    party = {
        "Customer": 3, "Supplier": 3,
        "Customer Group": 2, "Territory": 2,
    }.get(rule.get("applicable_for") or None, 0)
    item = 2 if (rule.get("apply_on") or "Item Code") == "Item Code" else 1
    return party * 10 + item


def apply_pricing_rules(doc):
    """Apply matching pricing rules to a transaction document.

    Called during validate() of Quotation, SO, SI, POS, PO, PI. For each item,
    finds the best matching rule and applies it.

    Skipped entirely when the document declares `ignore_pricing_rule` — its
    rates were agreed elsewhere (a contract, a partner system) and must survive
    validation unchanged. Per-item flags cover mixed documents.
    """
    if flt(doc._data.get("ignore_pricing_rule")):
        return

    db = get_db()
    today = nowdate()
    is_selling, is_buying = _trade_side(doc)
    if not (is_selling or is_buying):
        return
    party = _party_context(db, doc, is_selling)

    for item in doc.get("items") or []:
        if flt(item.get("ignore_pricing_rule")):
            continue
        # An explicit free line must not acquire a price from a rule.
        if item.get('rate') is not None and flt(item['rate']) == 0:
            continue
        item_code = item.get("item_code")
        if not item_code:
            continue

        qty = flt(item.get("qty", 0))
        base_rate = flt(item.get("price_list_rate") or item.get("rate", 0))
        amount = flt(qty * base_rate, 2)
        item_group = db.get_value("Item", item_code, "item_group")

        rules = db.sql(
            """
            SELECT * FROM "Pricing Rule"
            WHERE enabled = 1
              AND (company IS NULL OR company = '' OR company = ?)
              AND ((? = 1 AND selling = 1) OR (? = 1 AND buying = 1))
              AND (valid_from IS NULL OR valid_from = '' OR valid_from <= ?)
              AND (valid_upto IS NULL OR valid_upto = '' OR valid_upto >= ?)
              AND (COALESCE(min_qty, 0) = 0 OR COALESCE(min_qty, 0) <= ?)
              AND (COALESCE(max_qty, 0) = 0 OR COALESCE(max_qty, 0) >= ?)
              AND (COALESCE(min_amt, 0) = 0 OR COALESCE(min_amt, 0) <= ?)
              AND (COALESCE(max_amt, 0) = 0 OR COALESCE(max_amt, 0) >= ?)
              AND (
                    (COALESCE(apply_on, 'Item Code') = 'Item Code' AND item_code = ?)
                 OR (apply_on = 'Item Group' AND item_group IS NOT NULL AND item_group = ?)
              )
              AND (
                    applicable_for IS NULL OR applicable_for = ''
                 OR (applicable_for = 'Customer'       AND customer       = ?)
                 OR (applicable_for = 'Customer Group' AND customer_group = ?)
                 OR (applicable_for = 'Territory'      AND territory      = ?)
                 OR (applicable_for = 'Supplier'       AND supplier       = ?)
              )
            """,
            [
                doc.get('company'),
                int(bool(is_selling)), int(bool(is_buying)),
                today, today,
                qty, qty,
                amount, amount,
                item_code, item_group,
                party["customer"], party["customer_group"],
                party["territory"], party["supplier"],
            ],
        )

        if not rules:
            continue

        # Priority is the explicit override; specificity is the sensible
        # default ordering underneath it.
        rule = sorted(
            rules,
            key=lambda r: (
                -int(r.get("priority") or 0),
                -_specificity(r),
                -flt(r.get("min_qty")),
                str(r.get("name")),
            ),
        )[0]

        rtype = rule.get("rate_or_discount", "Discount Percentage")

        if rtype == "Rate":
            item["rate"] = flt(rule["rate"])
            item["price_list_rate"] = flt(rule["rate"])
            item["discount_percentage"] = 0
            item["discount_amount"] = 0
        elif rtype == "Discount Percentage":
            pct = flt(rule["discount_percentage"])
            item["discount_percentage"] = pct
            item["rate"] = flt(base_rate * (1 - pct / 100), 2)
        elif rtype == "Discount Amount":
            amt = flt(rule["discount_amount"])
            item["discount_amount"] = amt
            item["rate"] = flt(base_rate - amt, 2)

        item["pricing_rule"] = rule.get("name")
