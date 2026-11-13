"""The company's default taxes for a new document.

A company names a default sales and a default purchase Tax Template. A new
document that says nothing about taxes gets the rows of the template for its
side, whoever creates it: the document service (form, REST, chat, MCP) or the
core itself when it writes an invoice (consignment withdrawal, subscription).

    taxes not given      the company's default rows, if it has a template
    taxes given as []    deliberately no tax (export, exempt): left empty
    taxes given          kept as they are, nothing added

The rows are copied into the document. From then on they are the document's
own: a later change of the template or of the company default reaches new
documents only.
"""
from lambda_erp.database import get_db

# The taxes child table decides the side, so every document type with a
# taxes table is covered without a list of types.
TEMPLATE_FIELDS = {
    "Sales Taxes and Charges": "default_sales_tax_template",
    "Purchase Taxes and Charges": "default_purchase_tax_template",
}


def default_tax_rows(document_class, company):
    """The tax rows a new document of this class takes from its company's
    default Tax Template, or None when the class has no taxes, the company
    names no template for its side, or the template is empty."""
    taxes_child = getattr(document_class, "CHILD_TABLES", {}).get("taxes")
    field = TEMPLATE_FIELDS.get(taxes_child[0]) if taxes_child else None
    if not field or not company:
        return None
    db = get_db()
    template = db.get_value("Company", company, field)
    if not template:
        return None
    # A template of another company is not this company's default.
    owner = db.get_value("Tax Template", template, "company")
    if owner and owner != company:
        return None
    details = db.get_all("Tax Template Detail", filters={"parent": template}, fields=["*"], order_by="idx")
    if not details:
        return None
    return [{
        "charge_type": detail.get("charge_type") or "On Net Total",
        "account_head": detail.get("account_head"),
        "rate": detail.get("rate") or 0,
        "description": detail.get("description") or template,
        "add_deduct_tax": "Add",
    } for detail in details]


def apply_default_taxes(doc):
    """Give a document the core has just built, and that carries no tax rows,
    its company's default taxes. For invoices written from something that has
    no taxes of its own; a document made from another one copies that one's
    rows instead. Returns True when rows were added."""
    if doc.get("taxes"):
        return False
    rows = default_tax_rows(type(doc), doc.get("company"))
    if not rows:
        return False
    from lambda_erp.utils import _dict
    for row in rows:
        doc.append("taxes", _dict(row))
    return True
