"""Shared default-setting helpers for transactional documents."""

from lambda_erp.database import get_db


def set_default_company(doc):
    """Set company to the first available company if not specified."""
    if doc._data.get("company"):
        return
    db = get_db()
    companies = db.get_all("Company", fields=["name"], limit=1)
    if companies:
        doc._data["company"] = companies[0]["name"]
