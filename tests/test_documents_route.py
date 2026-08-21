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

        # an unknown filter field IS a 400 — filters affect the query, so stay strict
        r = client.get("/api/masters/customer?nonsense_col=x", headers=h)
        assert r.status_code == 400, f"unknown filter field must 400: {r.status_code} {r.text[:150]}"
        print("  masters: search + filters + validated deterministic sorting")

    print("PASS")


if __name__ == "__main__":
    check_documents_route()
