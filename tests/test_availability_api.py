#!/usr/bin/env python3
"""Functional test for the rental availability endpoint (GET /api/availability).

Runs the real FastAPI app end-to-end (register admin -> enable rest_api ->
issue a manager Bearer key -> create an asset-tracked item + 2 units + 1
booking -> assert availability). The availability ENGINE is covered by
test_erp_validation; this pins the HTTP surface. See docs/RENTAL_UI_PLAN.md.

Run:  python -m tests.test_availability_api
      LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_availability_api
"""
import os
import sys


def _reset_db():
    url = os.environ.get("LAMBDA_ERP_TEST_DB")
    if not url:
        return ":memory:"
    import psycopg
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    return url


def _rows(payload):
    """The document list endpoint returns {rows, total, ...}; normalise."""
    if isinstance(payload, dict) and "rows" in payload:
        return payload["rows"]
    return payload


def check_availability_api():
    db_path = _reset_db()
    backend = "postgres" if db_path.startswith("postgres") else "sqlite (:memory:)"
    os.environ["LAMBDA_ERP_DB"] = db_path
    os.environ["LAMBDA_ERP_AUTO_DEMO"] = "0"
    os.environ.setdefault("LAMBDA_ERP_PLUGINS", "")
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret-not-for-prod")
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used")

    from fastapi.testclient import TestClient
    from api.main import app

    print(f"  backend: {backend}")
    with TestClient(app) as client:
        # --- admin (first registrant) + turn the REST API on for Bearer keys ---
        r = client.post("/api/auth/register", json={
            "email": "admin@example.com", "full_name": "Admin",
            "password": "test-password-123"})
        assert r.status_code == 200 and r.json()["role"] == "admin", r.text[:300]

        r = client.put("/api/auth/settings", json={"rest_api_enabled": "1"})
        assert r.status_code == 200 and r.json().get("rest_api_enabled") == "1", r.text[:200]

        r = client.post("/api/auth/api-keys", json={"name": "rest", "role": "manager"})
        assert r.status_code == 200, r.text[:300]
        token = r.json()["token"]
        h = {"Authorization": f"Bearer {token}"}

        # Drop the admin session cookie so everything below is a genuine
        # Bearer-key + rest_api_enabled path (how connectors/the frontend call it).
        client.cookies.clear()
        assert client.get("/api/availability",
                          params={"item_code": "EXC-17", "from": "2026-08-14", "to": "2026-08-15"}
                          ).status_code == 401, "no key must be rejected"

        # Seed a company + yard (FK targets for Asset.warehouse) at the model
        # layer — same shared DB as the app; the accounting Chart of Accounts is
        # not needed for assets/reservations (they post nothing to the GL).
        from lambda_erp.database import get_db
        db = get_db()
        db.insert("Company", {"name": "Test Co", "company_name": "Test Co", "default_currency": "CHF"})
        db.insert("Warehouse", {"name": "YARD-SG", "warehouse_name": "Yard SG", "company": "Test Co"})
        db.conn.commit()

        # --- asset-tracked item + two physical units ---------------------------
        r = client.post("/api/masters/item", headers=h, json={
            "item_code": "EXC-17", "item_name": "17t Excavator",
            "is_stock_item": 0, "is_asset_tracked": 1, "standard_rate": 90})
        assert r.status_code == 200, r.text[:300]
        for tag in ("U-01", "U-02"):
            r = client.post("/api/documents/asset", headers=h, json={
                "item_code": "EXC-17", "asset_tag": tag, "warehouse": "YARD-SG", "status": "Available"})
            assert r.status_code == 200, r.text[:300]

        units = _rows(client.get("/api/documents/asset?item_code=EXC-17", headers=h).json())
        u01 = next(a["name"] for a in units if a["asset_tag"] == "U-01")

        # book U-01 for 14.-16. (half-open)
        r = client.post("/api/documents/reservation", headers=h, json={
            "asset": u01, "from_datetime": "2026-08-14", "to_datetime": "2026-08-16",
            "status": "Reserved"})
        assert r.status_code == 200, r.text[:300]

        # --- overlapping window: one unit committed, one free ------------------
        r = client.get("/api/availability", headers=h,
                       params={"item_code": "EXC-17", "from": "2026-08-14", "to": "2026-08-15"})
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["capacity"] == 2, d
        assert d["committed"] == 1, d
        assert d["available_qty"] == 1 and d["available"] is True, d
        assert [a["asset_tag"] for a in d["available_assets"]] == ["U-02"], d
        assert len(d["overlapping"]) == 1, d
        print("  overlapping window: capacity=2 committed=1 free=[U-02] OK")

        # --- non-overlapping window: both units free (half-open abutment) ------
        # The hire ends 2026-08-16 00:00; a window starting exactly then must NOT clash.
        r = client.get("/api/availability", headers=h,
                       params={"item_code": "EXC-17", "from": "2026-08-16", "to": "2026-08-18"})
        d2 = r.json()
        assert d2["available_qty"] == 2, d2
        assert sorted(a["asset_tag"] for a in d2["available_assets"]) == ["U-01", "U-02"], d2
        print("  abutting window (from == prior to): both free OK")

        # --- bad datetime -> 422, not 500 -------------------------------------
        r = client.get("/api/availability", headers=h,
                       params={"item_code": "EXC-17", "from": "not-a-date", "to": "2026-08-18"})
        assert r.status_code == 422, r.text[:200]
        print("  bad datetime -> 422 OK")

    print("PASS")


if __name__ == "__main__":
    check_availability_api()
