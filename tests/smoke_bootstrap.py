"""End-to-end smoke of the demo bootstrap path.

Verifies that bootstrap_demo():
  - Creates the company, runs the simulator, adds the public_manager user.
  - Creates the chat-referenced Quotation + Purchase Order.
  - Writes the Settings rows that load_demo_script() substitutes into.
  - Is idempotent (a second run does nothing).
And that load_demo_script() returns content with the placeholders resolved.
"""

from lambda_erp.database import setup
from api.bootstrap import bootstrap_demo
from api.chat import load_demo_script


def main():
    setup()  # fresh in-memory DB

    print("=" * 60)
    print("  First bootstrap run")
    print("=" * 60)
    bootstrap_demo()

    from lambda_erp.database import get_db
    db = get_db()

    qtn_count = db.sql('SELECT COUNT(*) as cnt FROM "Quotation"')[0]["cnt"]
    po_count = db.sql('SELECT COUNT(*) as cnt FROM "Purchase Order"')[0]["cnt"]
    sinv_count = db.sql('SELECT COUNT(*) as cnt FROM "Sales Invoice"')[0]["cnt"]
    pe_count = db.sql('SELECT COUNT(*) as cnt FROM "Payment Entry"')[0]["cnt"]
    pub_users = db.sql('SELECT name FROM "User" WHERE role = "public_manager"')

    print(f"  Quotations:      {qtn_count}")
    print(f"  Purchase Orders: {po_count}")
    print(f"  Sales Invoices:  {sinv_count}")
    print(f"  Payment Entries: {pe_count}")
    print(f"  public_manager:  {pub_users[0]['name'] if pub_users else 'MISSING'}")

    settings = dict(
        (r["key"], r["value"]) for r in db.sql('SELECT key, value FROM "Settings" WHERE key LIKE "demo_chat_%"')
    )
    print(f"\n  Demo chat settings:")
    for k, v in sorted(settings.items()):
        print(f"    {k:<38} {v}")

    assert qtn_count > 2000, f"expected >2000 quotations, got {qtn_count}"
    assert pub_users, "public_manager not created"
    assert settings.get("demo_chat_quotation"), "demo_chat_quotation missing"
    assert settings.get("demo_chat_purchase_order"), "demo_chat_purchase_order missing"
    assert settings.get("demo_chat_top_customer"), "demo_chat_top_customer missing"
    assert db.exists("Quotation", settings["demo_chat_quotation"])
    assert db.exists("Purchase Order", settings["demo_chat_purchase_order"])

    print("\n" + "=" * 60)
    print("  Rendered demo script (post-substitution)")
    print("=" * 60)
    script = load_demo_script()
    for i, entry in enumerate(script, 1):
        content = entry["content"]
        preview = content if len(content) <= 120 else content[:117] + "…"
        print(f"  [{i:02d}] {entry['role']:>9}: {preview}")

    for entry in script:
        assert "{{" not in entry["content"], f"unsubstituted placeholder: {entry['content']}"

    print("\n" + "=" * 60)
    print("  Second bootstrap run (idempotence)")
    print("=" * 60)
    bootstrap_demo()
    new_qtn_count = db.sql('SELECT COUNT(*) as cnt FROM "Quotation"')[0]["cnt"]
    new_po_count = db.sql('SELECT COUNT(*) as cnt FROM "Purchase Order"')[0]["cnt"]
    print(f"  Quotations:      {new_qtn_count} (was {qtn_count})")
    print(f"  Purchase Orders: {new_po_count} (was {po_count})")
    assert new_qtn_count == qtn_count, "simulator re-ran on second bootstrap"
    assert new_po_count == po_count, "chat PO re-created on second bootstrap"

    print("\n  PASSED.")


if __name__ == "__main__":
    main()
