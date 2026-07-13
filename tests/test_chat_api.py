#!/usr/bin/env python3
"""Functional tests for the programmatic chat API (POST /api/v1/chat, Bearer keys).

Exercises the real FastAPI app end-to-end (register admin -> enable -> issue key
-> chat), stubbing only the LLM loop (``run_thinking_loop`` / ``generate_title``)
so no OpenAI call is made. Verifies:

  - the `chat_api_enabled` flag gates the whole surface (404 when off)
  - Bearer-key auth (missing / bad / revoked -> 401)
  - admin-only key management (create returns the token once, list, revoke)
  - session semantics: rolling audit session, delete -> a fresh one, per-owner
    isolation (404 on another key's session)
  - the stateless-vs-replay knob: no session_id -> only the current message is in
    the LLM context; with session_id -> prior turns are replayed

Run:  python -m tests.test_chat_api
      LAMBDA_ERP_TEST_DB=postgresql://... python -m tests.test_chat_api   # CI runs both
"""
import os
import sys


def _reset_db():
    """Return a db_path for setup(); reset Postgres to a clean schema."""
    url = os.environ.get("LAMBDA_ERP_TEST_DB")
    if not url:
        return ":memory:"
    import psycopg
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    return url


# Captures the `messages` list handed to the (stubbed) agent loop on the most
# recent call, so tests can assert what context the model would have seen.
_LAST = {}


def _install_llm_stubs():
    """Replace the agent loop + title generation with deterministic stubs."""
    import api.chat as chat

    async def fake_loop(messages, on_event, session_id=None, max_iterations=8,
                        user_info=None, client_ip=None):
        _LAST["messages"] = [dict(m) for m in messages]
        _LAST["user_info"] = user_info
        _LAST["system"] = messages[0]["content"] if messages and messages[0].get("role") == "system" else ""
        last_user = next((m.get("content") for m in reversed(messages)
                          if m.get("role") == "user" and isinstance(m.get("content"), str)), "")
        # Emit a document PDF link on demand so the structured-`documents` extraction
        # can be exercised; otherwise the canonical "stub-reply" other checks assert on.
        if "pdf" in (last_user or "").lower():
            reply = "Here is the latest quotation: /api/documents/quotation/QTN-2298/pdf — I've attached it."
        else:
            reply = "stub-reply"
        messages.append({"role": "assistant", "content": reply})

    async def fake_title(*args, **kwargs):
        return None

    chat.run_thinking_loop = fake_loop
    chat.generate_title = fake_title


def check_chat_api():
    db_path = _reset_db()
    backend = "postgres" if db_path.startswith("postgres") else "sqlite (:memory:)"
    os.environ["LAMBDA_ERP_DB"] = db_path
    os.environ["LAMBDA_ERP_AUTO_DEMO"] = "0"
    os.environ.setdefault("LAMBDA_ERP_PLUGINS", "")
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret-not-for-prod")
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used")

    _install_llm_stubs()

    from fastapi.testclient import TestClient
    from api.main import app

    with TestClient(app) as client:
        # --- Feature is OFF by default: the whole surface 404s. -------------
        r = client.post("/api/v1/chat", json={"message": "hi"},
                        headers={"Authorization": "Bearer sk_erp_whatever"})
        assert r.status_code == 404, f"disabled chat -> {r.status_code}: {r.text[:200]}"
        # The document read surface is gated by the same flag.
        assert client.get("/api/v1/documents/sales-invoice/X/pdf",
                          headers={"Authorization": "Bearer sk_erp_whatever"}).status_code == 404

        # --- Become admin (first registrant) + turn the API on. -------------
        r = client.post("/api/auth/register",
                        json={"email": "admin@example.com", "full_name": "Admin",
                              "password": "test-password-123"})
        assert r.status_code == 200 and r.json()["role"] == "admin", r.text[:300]

        r = client.put("/api/auth/settings", json={"chat_api_enabled": "1"})
        assert r.status_code == 200 and r.json().get("chat_api_enabled") == "1", r.text[:200]

        # --- Auth failures (enabled, but no/bad key). -----------------------
        assert client.post("/api/v1/chat", json={"message": "hi"}).status_code == 401
        assert client.post("/api/v1/chat", json={"message": "hi"},
                           headers={"Authorization": "Bearer sk_erp_nope"}).status_code == 401

        # --- Admin issues a key (token returned exactly once). --------------
        r = client.post("/api/auth/api-keys", json={"name": "connector", "role": "manager"})
        assert r.status_code == 200, r.text[:300]
        created = r.json()
        token = created["token"]
        assert token.startswith("sk_erp_"), created
        assert created["role"] == "manager" and created["key_prefix"].startswith("sk_erp_")
        key_id = created["id"]
        auth_h = {"Authorization": f"Bearer {token}"}

        # list must never leak the token
        listed = client.get("/api/auth/api-keys").json()
        assert any(k["id"] == key_id for k in listed) and all("token" not in k for k in listed)

        # invalid role coerces to manager
        r2 = client.post("/api/auth/api-keys", json={"name": "x", "role": "superuser"})
        assert r2.status_code == 200 and r2.json()["role"] == "manager", r2.text[:200]

        # --- Stateless turn: only the current message is in LLM context. ----
        r = client.post("/api/v1/chat", json={"message": "hello"}, headers=auth_h)
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert body["reply"] == "stub-reply" and body["session_id"], body
        sid = body["session_id"]
        msgs = _LAST["messages"]
        assert len(msgs) == 2 and msgs[0]["role"] == "system", msgs
        assert msgs[1] == {"role": "user", "content": "hello"}, msgs  # no history replayed
        assert _LAST["user_info"]["role"] == "manager", _LAST["user_info"]
        # channel="api": the system prompt drops web-UI link guidance and tells the
        # agent its reply is relayed to an external app.
        sysprompt = _LAST["system"]
        assert "external application" in sysprompt, "api-channel prompt not applied"
        assert "Always use markdown links" not in sysprompt, "web-channel link guidance leaked into api reply"
        # No document referenced -> empty structured list.
        assert body.get("documents") == [], body

        # --- Structured documents: a PDF reference becomes a fetchable ref. --
        r = client.post("/api/v1/chat", json={"message": "give me the pdf of the latest offer"}, headers=auth_h)
        assert r.status_code == 200, r.text[:300]
        docs = r.json().get("documents")
        assert docs and len(docs) == 1, docs
        assert docs[0]["doctype"] == "quotation" and docs[0]["name"] == "QTN-2298", docs
        assert docs[0]["pdf_url"].endswith("/api/v1/documents/quotation/QTN-2298/pdf"), docs
        assert docs[0]["pdf_url"].startswith("http"), docs  # absolute, caller-fetchable

        # --- Rolling audit session: a second stateless call reuses it. ------
        r = client.post("/api/v1/chat", json={"message": "again"}, headers=auth_h)
        assert r.status_code == 200 and r.json()["session_id"] == sid, r.text[:200]
        assert _LAST["messages"][-1] == {"role": "user", "content": "again"}  # still stateless

        # --- Opt-in continuity: session_id replays prior turns. -------------
        r = client.post("/api/v1/chat", json={"message": "third", "session_id": sid}, headers=auth_h)
        assert r.status_code == 200, r.text[:200]
        replayed = _LAST["messages"]
        assert len(replayed) > 2, replayed
        assert any(m["role"] == "assistant" for m in replayed), "history not replayed"
        assert any(m.get("content") == "hello" for m in replayed), "earlier user turn missing"

        # --- Sessions list is scoped to the key. ----------------------------
        sessions = client.get("/api/v1/chat/sessions", headers=auth_h).json()
        assert any(s["id"] == sid for s in sessions), sessions

        # --- Per-owner isolation: another key can't touch this session. -----
        other = client.post("/api/auth/api-keys", json={"name": "other"}).json()
        other_h = {"Authorization": f"Bearer {other['token']}"}
        r = client.post("/api/v1/chat", json={"message": "peek", "session_id": sid}, headers=other_h)
        assert r.status_code == 404, f"cross-owner session -> {r.status_code}"

        # --- Delete -> the next stateless call opens a fresh session. -------
        assert client.delete(f"/api/v1/chat/sessions/{sid}", headers=auth_h).status_code == 200
        r = client.post("/api/v1/chat", json={"message": "new"}, headers=auth_h)
        assert r.status_code == 200 and r.json()["session_id"] != sid, "deleted session was reused"

        # --- Document read surface (Bearer-gated PDF + JSON). ---------------
        # No key -> 401 (enabled, but unauthenticated).
        assert client.get("/api/v1/documents/sales-invoice/SINV-0001/pdf").status_code == 401
        # Valid key + unknown doctype -> 422 (ValueError). This proves the route is
        # wired and auth passed — a missing *route* would 404 instead — without
        # rendering a real PDF (WeasyPrint never runs: generate_pdf resolves the
        # doctype/loads the doc first).
        assert client.get("/api/v1/documents/not-a-doctype/X/pdf", headers=auth_h).status_code == 422
        assert client.get("/api/v1/documents/not-a-doctype/X", headers=auth_h).status_code == 422
        # Valid key + known doctype but missing document -> 404 (ValidationError).
        assert client.get("/api/v1/documents/sales-invoice/NOPE-9999/pdf", headers=auth_h).status_code == 404
        assert client.get("/api/v1/documents/sales-invoice/NOPE-9999", headers=auth_h).status_code == 404

        # --- Revoke -> the key stops working. -------------------------------
        assert client.post(f"/api/auth/api-keys/{key_id}/revoke").status_code == 200
        assert client.post("/api/v1/chat", json={"message": "hi"}, headers=auth_h).status_code == 401
        # revoked key also can't read documents
        assert client.get("/api/v1/documents/sales-invoice/X/pdf", headers=auth_h).status_code == 401

    print(f"  [chat api] gating/auth/keys/sessions/stateless-replay OK on {backend}")


def main():
    print("Chat API checks")
    check_chat_api()
    print("All chat API checks passed.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\nFAILED: {e}")
        sys.exit(1)
