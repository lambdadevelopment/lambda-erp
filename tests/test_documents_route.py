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

    print("PASS")


if __name__ == "__main__":
    check_documents_route()
