#!/usr/bin/env python3
"""The document-list route must treat the `fields` projection as ADVISORY —
unknown columns are dropped, never a 400. The frontend appends display-only
fields (currency, party_type) that not every doctype has; a 400 there broke every
list that lacked them (regression guard for the 0.8.7 breakage).

Run:  python -m tests.test_documents_route
      LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_documents_route
"""
import os


def _reset_db():
    url = os.environ.get("LAMBDA_ERP_TEST_DB")
    if not url:
        return ":memory:"
    import psycopg
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    return url


def check_documents_route():
    db_path = _reset_db()
    os.environ["LAMBDA_ERP_DB"] = db_path
    os.environ["LAMBDA_ERP_AUTO_DEMO"] = "0"
    os.environ.setdefault("LAMBDA_ERP_PLUGINS", "")
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret-not-for-prod")
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used")

    from fastapi.testclient import TestClient
    from api.main import app

    with TestClient(app) as client:
        r = client.post("/api/auth/register", json={
            "email": "admin@example.com", "full_name": "Admin",
            "password": "test-password-123"})
        assert r.status_code == 200, r.text[:300]
        r = client.put("/api/auth/settings", json={"rest_api_enabled": "1"})
        assert r.status_code == 200, r.text[:200]
        r = client.post("/api/auth/api-keys", json={"name": "rest", "role": "manager"})
        assert r.status_code == 200, r.text[:300]
        h = {"Authorization": f"Bearer {r.json()['token']}"}

        # Quotation has no `party_type` column — the projection must drop it, 200.
        r = client.get("/api/documents/quotation?limit=1&fields=party_type", headers=h)
        assert r.status_code == 200, f"unknown projection field must be dropped, not 400: {r.status_code} {r.text[:200]}"

        # A realistic mix (real + display-only) also succeeds and narrows the row.
        r = client.get(
            "/api/documents/quotation?limit=1&fields=customer_name,party_type,currency",
            headers=h)
        assert r.status_code == 200, r.text[:200]
        rows = r.json().get("rows", [])
        if rows:
            assert "party_type" not in rows[0], "unknown field must not appear"
            assert set(rows[0].keys()) <= {"name", "customer_name", "currency"}, rows[0].keys()
        print("  route: unknown projection fields dropped (no 400); known ones projected")

        # --- Masters list: search + field filter + filter-values + strict field. ---
        # Seed via the shared db singleton (same instance the app serves) — avoids
        # any create-time validation; we're testing the list route, not creation.
        from lambda_erp.database import get_db
        db = get_db()
        db.insert("Customer", {"name": "CUST-T1", "customer_name": "Muster Test AG",
                               "customer_group": "Commercial", "territory": "Zurich", "disabled": 0})
        db.insert("Customer", {"name": "CUST-T2", "customer_name": "Other GmbH",
                               "customer_group": "Retail", "disabled": 0})
        db.insert("Customer", {"name": "CUST-S3", "customer_name": "Zulu AG",
                               "customer_group": "Sort Test", "territory": None, "disabled": 0})
        db.insert("Customer", {"name": "CUST-S1", "customer_name": "Alpha AG",
                               "customer_group": "Sort Test", "territory": "Zurich", "disabled": 0})
        db.insert("Customer", {"name": "CUST-S2", "customer_name": "Alpha AG",
                               "customer_group": "Sort Test", "territory": "Aargau", "disabled": 0})
        db.conn.commit()

        # free-text search matches a text column (customer_name), excludes others
        r = client.get("/api/masters/customer?search=Muster", headers=h)
        assert r.status_code == 200, r.text[:200]
        names = [row.get("customer_name") for row in r.json()["rows"]]
        assert any("Muster" in (n or "") for n in names) and all("Other" not in (n or "") for n in names), names
        assert "customer_name" in r.json()["text_fields"]
        assert "credit_limit" not in r.json()["text_fields"]

        # Smart Search makes text-field intent explicit in the URL. Contains is
        # a literal, case-insensitive substring; plain field=value stays exact.
        r = client.get("/api/masters/customer?customer_name__contains=uStEr", headers=h)
        assert r.status_code == 200, r.text[:200]
        assert [row["name"] for row in r.json()["rows"]] == ["CUST-T1"], r.text[:300]
        r = client.get("/api/masters/customer?customer_name=Muster%20Test%20AG", headers=h)
        assert r.status_code == 200 and r.json()["total"] == 1, r.text[:200]
        assert client.get(
            "/api/masters/customer?credit_limit__contains=1", headers=h
        ).status_code == 400

        # equality field filter narrows to the group
        r = client.get("/api/masters/customer?customer_group=Retail", headers=h)
        assert r.status_code == 200 and r.json()["total"] >= 1, r.text[:200]
        assert all(row.get("customer_group") == "Retail" for row in r.json()["rows"]), r.text[:200]

        # sortable headers are backed by validated, deterministic server-side
        # ordering. Ties use name in the same direction; NULL stays last.
        def sorted_test(order_by, order="asc"):
            r = client.get(
                f"/api/masters/customer?customer_group=Sort%20Test&order_by={order_by}&order={order}",
                headers=h,
            )
            assert r.status_code == 200, r.text[:200]
            return [row["name"] for row in r.json()["rows"]]

        assert sorted_test("customer_name") == ["CUST-S1", "CUST-S2", "CUST-S3"]
        assert sorted_test("customer_name", "desc") == ["CUST-S3", "CUST-S2", "CUST-S1"]
        assert sorted_test("territory") == ["CUST-S2", "CUST-S1", "CUST-S3"]
        assert client.get("/api/masters/customer?order_by=nope", headers=h).status_code == 400
        assert client.get("/api/masters/customer?order_by=name&order=sideways", headers=h).status_code == 400
        assert client.get(
            "/api/masters/customer?search=x&search_fields=nope", headers=h
        ).status_code == 400

        # distinct filter-values for the dropdown
        r = client.get("/api/masters/customer/filter-values?field=customer_group", headers=h)
        assert r.status_code == 200, r.text[:200]
        vals = r.json()["values"]
        assert "Commercial" in vals and "Retail" in vals, vals

        # Smart-list autocomplete narrows distinct values by prefix and keeps
        # the response bounded. It works for both text and numeric columns.
        r = client.get(
            "/api/masters/customer/filter-values?field=customer_name&q=Mus&limit=1",
            headers=h,
        )
        assert r.status_code == 200, r.text[:200]
        assert r.json()["values"] == ["Muster Test AG"], r.json()
        assert client.get(
            "/api/masters/customer/filter-values?field=nope&q=x", headers=h
        ).status_code == 400

        db.insert("Quotation", {
            "name": "QTN-FILTER-1", "customer": "CUST-T1",
            "customer_name": "Muster Test AG", "grand_total": 150.0,
            "status": "Draft", "docstatus": 0, "discarded": 0,
        })
        db.insert("Quotation", {
            "name": "QTN-FILTER-2", "customer": "CUST-T2",
            "customer_name": "Other GmbH", "grand_total": 250.0,
            "status": "Submitted", "docstatus": 1, "discarded": 0,
        })
        db.conn.commit()
        r = client.get(
            "/api/documents/quotation/filter-values?field=customer_name&q=Mus&limit=12",
            headers=h,
        )
        assert r.status_code == 200, r.text[:200]
        assert r.json()["values"] == ["Muster Test AG"], r.json()
        r = client.get(
            "/api/documents/quotation/filter-values?field=grand_total&q=1&limit=12",
            headers=h,
        )
        assert r.status_code == 200 and r.json()["values"] == [150.0], r.text[:200]
        assert client.get(
            "/api/documents/quotation/filter-values?field=nope", headers=h
        ).status_code == 400

        r = client.get(
            "/api/documents/quotation?customer_name__contains=UsTeR", headers=h
        )
        assert r.status_code == 200, r.text[:200]
        assert [row["name"] for row in r.json()["rows"]] == ["QTN-FILTER-1"], r.text[:300]
        assert "customer_name" in r.json()["text_fields"]
        assert "grand_total" not in r.json()["text_fields"]
        assert client.get(
            "/api/documents/quotation?grand_total__contains=15", headers=h
        ).status_code == 400

        # Bare free-text search has deterministic schema-derived defaults. It
        # must never degrade to the newest unfiltered rows merely because a REST
        # or MCP caller omitted search_fields.
        r = client.get("/api/documents/quotation?search=mUsTeR", headers=h)
        assert r.status_code == 200, r.text[:200]
        assert [row["name"] for row in r.json()["rows"]] == ["QTN-FILTER-1"], r.text[:300]
        assert r.json()["total"] == 1, r.text[:300]
        r = client.get("/api/documents/quotation?search=no-such-customer", headers=h)
        assert r.status_code == 200 and r.json()["rows"] == [] and r.json()["total"] == 0, r.text[:300]

        # an unknown filter field IS a 400 — filters affect the query, so stay strict
        r = client.get("/api/masters/customer?nonsense_col=x", headers=h)
        assert r.status_code == 400, f"unknown filter field must 400: {r.status_code} {r.text[:150]}"
        assert client.get(
            "/api/masters/customer?nonsense_col__contains=x", headers=h
        ).status_code == 400
        print("  lists: search + filters + field-aware value suggestions")

    print("PASS")


if __name__ == "__main__":
    check_documents_route()
