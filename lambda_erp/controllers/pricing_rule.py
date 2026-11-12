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

Rules match on the document's own date (posting_date / transaction_date), not
on today. A Rate or Discount Amount rule is a number in one currency
(`currency`, defaulting to the company's); it only applies to documents in
that currency. A Discount Percentage rule applies in any currency.

What the rules never override: a rate the user changed on a saved line (the
line is then marked `ignore_pricing_rule`), lines carried over from a source
document by a conversion (their price was agreed there), free lines and lines
with a written deduction. A Rate rule sets the line rate but leaves
`price_list_rate` at the list price, so the base stays honest when the rule is
later disabled; a line whose rule no longer matches falls back to its list
price.
"""

from lambda_erp.model import Document
from lambda_erp.utils import _dict, flt, getdate, nowdate
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from lambda_erp.controllers.item_price import normalize_pricing_number, pricing_date, _validity_rank
from lambda_erp.controllers import price_resolution

APPLY_ON = ("Item Code", "Item Group")
APPLICABLE_FOR = ("Customer", "Customer Group", "Territory", "Supplier", "Supplier Group")

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
SELLING_DOCTYPES = {"Quotation", "Sales Order", "Sales Invoice", "POS Invoice", "Delivery Note"}
BUYING_DOCTYPES = {"Purchase Order", "Purchase Invoice", "Purchase Receipt"}


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
        # Forms send blank optional numeric fields. Persist real zeroes so both
        # SQL backends match unbounded rules consistently (Postgres rejects '').
        for field in ("rate", "discount_percentage", "discount_amount", "min_qty", "max_qty", "min_amt", "max_amt"):
            normalize_pricing_number(self, field)
        for field in ("priority", "selling", "buying", "enabled"):
            normalize_pricing_number(self, field, 1 if field == "enabled" else 0, integer=True)
        if not self.title:
            raise ValidationError("Title is required")
        self._set_currency()

        # Deployments add their own match keys (price families, purchasing
        # groups, ...) with price_resolution.register_price_dimension.
        item_dimensions = price_resolution.dimensions("item")
        party_dimensions = price_resolution.dimensions("party")

        apply_on = self.apply_on or "Item Code"
        if apply_on not in APPLY_ON and apply_on not in item_dimensions:
            raise ValidationError(
                f"Apply On must be one of {', '.join([*APPLY_ON, *item_dimensions])}")
        self._data["apply_on"] = apply_on
        if apply_on == "Item Code" and not self.item_code:
            raise ValidationError("Item Code is required when Apply On is Item Code")
        if apply_on == "Item Group" and not self.item_group:
            raise ValidationError("Item Group is required when Apply On is Item Group")
        if apply_on in item_dimensions and not str(self._data.get("apply_on_value") or "").strip():
            raise ValidationError(f"apply_on_value is required when Apply On is {apply_on}")

        applicable_for = self.applicable_for or None
        if applicable_for is not None and applicable_for not in APPLICABLE_FOR and applicable_for not in party_dimensions:
            raise ValidationError(
                f"Applicable For must be empty or one of {', '.join([*APPLICABLE_FOR, *party_dimensions])}")
        required_for = {
            "Customer": "customer",
            "Customer Group": "customer_group",
            "Territory": "territory",
            "Supplier": "supplier",
            "Supplier Group": "supplier_group",
        }
        if applicable_for in required_for and not self._data.get(required_for[applicable_for]):
            raise ValidationError(
                f"{required_for[applicable_for]} is required when Applicable For is {applicable_for}")
        if applicable_for in party_dimensions and not str(self._data.get("applicable_for_value") or "").strip():
            raise ValidationError(f"applicable_for_value is required when Applicable For is {applicable_for}")

        if not flt(self._data.get("selling")) and not flt(self._data.get("buying")):
            raise ValidationError("At least one of Selling or Buying must be enabled")
        party_type = party_dimensions[applicable_for].party_type if applicable_for in party_dimensions else None
        if (applicable_for in ("Customer", "Customer Group", "Territory") or party_type == "Customer") \
                and not flt(self._data.get("selling")):
            raise ValidationError(f"Applicable For {applicable_for} requires Selling")
        if (applicable_for in ("Supplier", "Supplier Group") or party_type == "Supplier") \
                and not flt(self._data.get("buying")):
            raise ValidationError(f"Applicable For {applicable_for} requires Buying")

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


    def _set_currency(self):
        """A Rate / Discount Amount is a number in a currency: default it to
        the company's, or to the one currency all companies share. A global
        rule across companies with different currencies stays empty and then
        counts in each document's company currency."""
        if self._data.get("currency"):
            return
        db = get_db()
        if self.company:
            self._data["currency"] = db.get_value("Company", self.company, "default_currency")
            return
        if (self.rate_or_discount or "Discount Percentage") == "Discount Percentage":
            return
        currencies = {r["default_currency"] for r in db.sql(
            'SELECT DISTINCT default_currency FROM "Company" WHERE default_currency IS NOT NULL')}
        if len(currencies) == 1:
            self._data["currency"] = currencies.pop()


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
    empty = {"customer": None, "customer_group": None, "territory": None, "supplier": None, "supplier_group": None}
    if is_selling:
        customer = doc._data.get("customer")
        if not customer:
            return empty
        row = db.get_value("Customer", customer, ["customer_group", "territory"])
        return {
            **empty,
            "customer": customer,
            "customer_group": row.customer_group if row else None,
            "territory": row.territory if row else None,
        }
    supplier = doc._data.get("supplier")
    return {**empty, "supplier": supplier,
            "supplier_group": db.get_value("Supplier", supplier, "supplier_group") if supplier else None}


def _specificity(rule):
    """How narrowly a rule is targeted. Breaks ties after priority.

    A rule naming one customer beats one naming their group, which beats one
    that applies to everybody; a rule naming an item beats one naming its
    group. Without this, two equally-prioritised rules would be separated by
    name, and nobody could explain the resulting price.
    """
    applicable_for = rule.get("applicable_for") or None
    party = {
        "Customer": 3, "Supplier": 3,
        "Customer Group": 2, "Territory": 2, "Supplier Group": 2,
    }.get(applicable_for, 0)
    party_dimensions = price_resolution.dimensions("party")
    if applicable_for in party_dimensions:
        party = party_dimensions[applicable_for].specificity
    apply_on = rule.get("apply_on") or "Item Code"
    item = 2 if apply_on == "Item Code" else 1
    item_dimensions = price_resolution.dimensions("item")
    if apply_on in item_dimensions:
        item = item_dimensions[apply_on].specificity
    return party * 10 + item


def _rule_order(rule):
    """Which of several matching rules applies. In this order: the higher
    priority; the more specific match; the higher min_qty; the narrower
    validity window; the newer valid_from; the name."""
    narrow, newest = _validity_rank(rule)
    return (-int(rule.get("priority") or 0), -_specificity(rule), -flt(rule.get("min_qty")),
            -narrow, -newest, str(rule.get("name")))


def _dimension_clause(kind, column, value_column, context):
    """SQL for rules that match through a registered dimension."""
    clauses, params = [], []
    for name, dimension in price_resolution.dimensions(kind).items():
        values = sorted({str(v) for v in (dimension.resolve(context) or ()) if v not in (None, "")})
        if not values:
            continue
        clauses.append(f" OR ({column} = ? AND {value_column} IN ({', '.join('?' for _ in values)}))")
        params += [name, *values]
    return "".join(clauses), params


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
    on_date = pricing_date(doc)
    is_selling, is_buying = _trade_side(doc)
    if not (is_selling or is_buying):
        return
    party = _party_context(db, doc, is_selling)
    company_currency = db.get_value("Company", doc.get("company"), "default_currency") if doc.get("company") else None
    doc_currency = doc._data.get("currency") or company_currency
    stored = _stored_lines(db, doc)
    stamps = "price_source" in db._get_table_columns(doc.CHILD_TABLES["items"][0]) \
        if "items" in getattr(doc, "CHILD_TABLES", {}) else False
    strict = price_resolution.strict_conflicts()

    for item in doc.get("items") or []:
        saved = stored.get(item.get("name"))
        if stamps and not item.get("price_source"):
            # A line saved again without its provenance keeps what it had; a
            # rate that arrived with the line was entered by the caller.
            if saved and saved.get("item_code") == item.get("item_code") and saved.get("price_source"):
                item["price_source"], item["price_reference"] = saved["price_source"], saved.get("price_reference")
            elif item.get("rate") is not None:
                item["price_source"], item["price_reference"] = "Entered", None
        if flt(item.get("ignore_pricing_rule")):
            continue
        if _rate_changed_by_user(item, saved):
            _keep_manual_rate(item)
            if stamps:
                item["price_source"], item["price_reference"] = "Manual", None
            continue
        # An explicit free line must not acquire a price from a rule.
        if item.get('rate') is not None and flt(item['rate']) == 0:
            continue
        # A deduction written on the line (with its text) is the price agreed.
        if item.get("discount_label"):
            continue
        item_code = item.get("item_code")
        if not item_code:
            continue

        qty = flt(item.get("qty", 0))
        base_rate = flt(item.get("price_list_rate") or item.get("rate", 0))
        amount = flt(qty * base_rate, 2)
        item_group = db.get_value("Item", item_code, "item_group")
        context = {**party, "item_code": item_code, "item_group": item_group, "company": doc.get("company"),
                   "currency": doc_currency, "date": on_date, "side": "selling" if is_selling else "buying"}
        item_sql, item_params = _dimension_clause("item", "apply_on", "apply_on_value", context)
        party_sql, party_params = _dimension_clause("party", "applicable_for", "applicable_for_value", context)

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
                 """ + item_sql + """
              )
              AND (
                    applicable_for IS NULL OR applicable_for = ''
                 OR (applicable_for = 'Customer'       AND customer       = ?)
                 OR (applicable_for = 'Customer Group' AND customer_group = ?)
                 OR (applicable_for = 'Territory'      AND territory      = ?)
                 OR (applicable_for = 'Supplier'       AND supplier       = ?)
                 OR (applicable_for = 'Supplier Group' AND supplier_group = ?)
                 """ + party_sql + """
              )
            """,
            [
                doc.get('company'),
                int(bool(is_selling)), int(bool(is_buying)),
                on_date, on_date,
                qty, qty,
                amount, amount,
                item_code, item_group, *item_params,
                party["customer"], party["customer_group"],
                party["territory"], party["supplier"], party["supplier_group"], *party_params,
            ],
        )

        rules = [r for r in rules
                 if (r.get("rate_or_discount") or "Discount Percentage") == "Discount Percentage"
                 or (r.get("currency") or company_currency) == doc_currency]
        if not rules:
            if item.get("pricing_rule"):
                _drop_rule(item)
            price_resolution.trace_rules(item, [], None, [])
            continue

        # Priority is the explicit override; specificity is the sensible
        # default ordering underneath it; the rest makes the choice between
        # equals deterministic (see _rule_order).
        rules = sorted(rules, key=_rule_order)
        rule = rules[0]
        tied = [r for r in rules[1:] if _rule_order(r)[:2] == _rule_order(rule)[:2]]
        price_resolution.trace_rules(item, rules, rule, tied)
        if strict and tied:
            raise ValidationError(
                f"Pricing rules {rule.get('name')} and {', '.join(str(r.get('name')) for r in tied)} apply to "
                f"{item_code} with the same priority and the same specificity. Give one a higher priority "
                f"(strict price conflicts are switched on)")

        rtype = rule.get("rate_or_discount", "Discount Percentage")

        if rtype == "Rate":
            item["rate"] = flt(rule["rate"])
            if base_rate:
                item["price_list_rate"] = base_rate
            item["discount_percentage"] = 0
            item["discount_amount"] = 0
        elif rtype == "Discount Percentage":
            pct = flt(rule["discount_percentage"])
            item["price_list_rate"] = base_rate
            item["discount_percentage"] = pct
            item["discount_amount"] = 0
            item["rate"] = flt(base_rate * (1 - pct / 100), 2)
        elif rtype == "Discount Amount":
            amt = flt(rule["discount_amount"])
            item["price_list_rate"] = base_rate
            item["discount_percentage"] = 0
            item["discount_amount"] = amt
            item["rate"] = flt(base_rate - amt, 2)

        item["pricing_rule"] = rule.get("name")


def _stored_lines(db, doc):
    """The saved state of this document's lines, by row name (empty for a new
    document). Lets pricing tell a user's rate edit from a rule's output."""
    if not getattr(doc, "name", None) or "items" not in getattr(doc, "CHILD_TABLES", {}):
        return {}
    table = doc.CHILD_TABLES["items"][0]
    columns = "name, item_code, rate, price_list_rate"
    if "price_source" in db._get_table_columns(table):
        columns += ", price_source, price_reference"
    rows = db.sql(f'SELECT {columns} FROM "{table}" WHERE parent = ?', [doc.name])
    return {r["name"]: r for r in rows}


def _rate_changed_by_user(item, saved):
    """True when a saved line comes back with a different rate but the same
    item and list price: the user typed a price and it must survive."""
    if not saved or item.get("rate") is None:
        return False
    if item.get("item_code") != saved.get("item_code"):
        return False
    if flt(item.get("price_list_rate"), 2) != flt(saved.get("price_list_rate"), 2):
        return False
    return flt(item.get("rate"), 2) != flt(saved.get("rate"), 2)


def _keep_manual_rate(item):
    """Freeze a user-set rate: no rule applies to this line any more, and the
    discount fields say how far it is below the list price (totals would
    otherwise recompute the rate from a leftover discount percentage)."""
    rate = flt(item.get("rate"))
    list_rate = flt(item.get("price_list_rate"))
    item["ignore_pricing_rule"] = 1
    item["pricing_rule"] = None
    item["discount_percentage"] = 0
    item["discount_amount"] = flt(list_rate - rate, 2) if list_rate > rate else 0


def _drop_rule(item):
    """The line's rule no longer matches (disabled, expired, other currency,
    quantity changed): back to the list price."""
    item["pricing_rule"] = None
    item["discount_percentage"] = 0
    item["discount_amount"] = 0
    if flt(item.get("price_list_rate")):
        item["rate"] = flt(item["price_list_rate"])
