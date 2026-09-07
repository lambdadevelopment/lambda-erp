#!/usr/bin/env python3
"""Regression checks for the September 2026 authorization review.

All records are synthetic and live in a temporary SQLite database. Provider
traffic and LLM calls are mocked; no external system is contacted.
"""

import asyncio
import io
import json
import os
import tempfile
import urllib.parse
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch


def _state_from(response) -> str:
    location = response.headers["location"]
    return urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)["state"][0]


class _TokenResponse:
    status_code = 200

    def json(self):
        return {"id_token": "synthetic-id-token"}


def check_security_regressions():
    with tempfile.TemporaryDirectory(prefix="lambda-security-regressions-") as tmp:
        database_url = os.environ.get("LAMBDA_ERP_TEST_DB")
        if database_url:
            import psycopg
            with psycopg.connect(database_url, autocommit=True) as connection:
                connection.execute("DROP SCHEMA public CASCADE")
                connection.execute("CREATE SCHEMA public")
            database_path = database_url
            backend = "postgres"
        else:
            database_path = str(Path(tmp) / "security.db")
            backend = "sqlite (temp file)"
        os.environ.update({
            "LAMBDA_ERP_DB": database_path,
            "JWT_SECRET_KEY": "synthetic-security-test-secret",
            "LAMBDA_ERP_PLUGINS": "",
            "LAMBDA_ERP_AUTO_DEMO": "0",
            "OPENAI_API_KEY": "synthetic-not-used",
        })

        from fastapi import FastAPI, HTTPException, UploadFile, WebSocketDisconnect
        from fastapi.testclient import TestClient

        from lambda_erp.database import setup
        from lambda_erp.utils import now
        from api import attachments, auth, chat, oauth
        from api.routers import analytics, setup as setup_router

        db = setup(database_path)
        app = FastAPI()
        app.include_router(auth.router, prefix="/api")
        app.include_router(oauth.router, prefix="/api")
        app.include_router(setup_router.router, prefix="/api")
        app.include_router(attachments.router, prefix="/api")
        app.include_router(analytics.router, prefix="/api")

        # Existing password-session shape and every signup mode remain valid.
        admin_client = TestClient(app)
        registered = admin_client.post("/api/auth/register", json={
            "email": "admin@synthetic.invalid",
            "full_name": "SYNTHETIC_PRIVATE_MARKER",
            "password": "admin-old-password",
        })
        assert registered.status_code == 200 and registered.json()["role"] == "admin", registered.text
        admin_id = registered.json()["name"]
        original_session_cookie = admin_client.cookies.get(auth.COOKIE_NAME)
        assert original_session_cookie and auth.decode_token(original_session_cookie) == admin_id
        assert admin_client.get("/api/auth/me").status_code == 200

        assert admin_client.put(
            "/api/auth/settings", json={"rest_api_enabled": "1", "allow_public_signup": "1"}
        ).status_code == 200
        public_signup = TestClient(app)
        response = public_signup.post("/api/auth/register", json={
            "email": "viewer@synthetic.invalid",
            "full_name": "Viewer",
            "password": "viewer-password",
        })
        assert response.status_code == 200 and response.json()["role"] == "viewer", response.text

        invite = admin_client.post(
            "/api/auth/invite", json={"email": "manager@synthetic.invalid", "role": "manager"}
        )
        assert invite.status_code == 200, invite.text
        invited_signup = TestClient(app).post("/api/auth/register", json={
            "email": "manager@synthetic.invalid",
            "full_name": "Manager",
            "password": "manager-password",
            "invite_token": invite.json()["token"],
        })
        assert invited_signup.status_code == 200 and invited_signup.json()["role"] == "manager"
        manager_id = invited_signup.json()["name"]

        db.insert("Company", {
            "name": "Synthetic Co",
            "company_name": "Synthetic Co",
            "default_currency": "CHF",
            "email": "private@synthetic.invalid",
            "tax_id": "SYNTHETIC-TAX-ID",
        })
        db.insert("Account", {
            "name": "4000 - SYN", "account_name": "Revenue", "company": "Synthetic Co",
            "root_type": "Income", "report_type": "Profit and Loss", "is_group": 0,
        })
        db.insert("Account", {
            "name": "6000 - SYN", "account_name": "Operating Expense", "company": "Synthetic Co",
            "root_type": "Expense", "report_type": "Profit and Loss", "is_group": 0,
        })
        for name, posting_date, account, debit, credit in (
            ("GLE-INCOME", "2025-01-15", "4000 - SYN", 0, 900),
            ("GLE-EXPENSE", "2025-01-20", "6000 - SYN", 250, 0),
        ):
            db.insert("GL Entry", {
                "name": name, "posting_date": posting_date, "account": account,
                "debit": debit, "credit": credit, "company": "Synthetic Co",
                "is_cancelled": 0,
            })
        db.commit()

        # API keys are unchanged for business/identity resolution, but cannot
        # cross into browser credential or account-administration operations.
        key_response = admin_client.post(
            "/api/auth/api-keys", json={"name": "existing integration", "role": "viewer"}
        )
        assert key_response.status_code == 200, key_response.text
        key = key_response.json()
        key_client = TestClient(app, headers={"Authorization": f"Bearer {key['token']}"})
        assert not key_client.cookies, dict(key_client.cookies)
        me = key_client.get("/api/auth/me")
        assert me.status_code == 200 and me.json()["name"] == admin_id and me.json()["role"] == "viewer"
        assert key_client.get("/api/setup/status").json()["companies"][0]["tax_id"] == "SYNTHETIC-TAX-ID"

        # Model an OAuth-only owner to cover the original set-password exploit.
        db.set_value("User", admin_id, {"hashed_password": oauth.OAUTH_PASSWORD_SENTINEL})
        for method, path, body in (
            ("post", "/api/auth/set-password", {"new_password": "attacker-password"}),
            ("get", "/api/auth/api-keys", None),
            ("get", "/api/auth/users", None),
            ("get", "/api/auth/settings", None),
            ("get", "/api/auth/oauth/identities", None),
        ):
            result = key_client.request(method, path, json=body)
            assert result.status_code == 403, (path, result.status_code, result.text)
        assert db.get_value("User", admin_id, ["hashed_password"])["hashed_password"] == oauth.OAUTH_PASSWORD_SENTINEL

        provider_discovery = {
            "authorization_endpoint": "https://synthetic-provider.invalid/auth",
            "token_endpoint": "https://synthetic-provider.invalid/token",
        }
        identity_claims = {
            "sub": "synthetic-linked-identity",
            "email": "linked@synthetic.invalid",
            "email_verified": True,
        }
        oauth_patches = (
            patch.object(oauth, "_require_provider", return_value=None),
            patch.object(oauth, "_client_id", return_value="synthetic-client"),
            patch.object(oauth, "_client_secret", return_value="synthetic-secret"),
            patch.object(oauth, "_discovery", return_value=provider_discovery),
            patch.object(oauth.httpx, "post", return_value=_TokenResponse()),
            patch.object(oauth, "_verify_id_token", return_value=identity_claims),
        )
        with oauth_patches[0], oauth_patches[1], oauth_patches[2], oauth_patches[3], oauth_patches[4], oauth_patches[5]:
            assert key_client.get(
                "/api/auth/google/login?link=1", follow_redirects=False
            ).status_code == 403

            # The interactive browser can link, but the signed state alone is
            # insufficient in another browser and each flow is single-use.
            link_start = admin_client.get(
                "/api/auth/google/login?link=1", follow_redirects=False
            )
            assert link_start.status_code == 303
            state = _state_from(link_start)
            foreign_callback = TestClient(app).get(
                "/api/auth/google/callback",
                params={"code": "synthetic", "state": state},
                follow_redirects=False,
            )
            assert foreign_callback.status_code == 400
            linked = admin_client.get(
                "/api/auth/google/callback",
                params={"code": "synthetic", "state": state},
                follow_redirects=False,
            )
            assert linked.status_code == 303 and linked.headers["location"].endswith("?linked=google")
            replay = admin_client.get(
                "/api/auth/google/callback",
                params={"code": "synthetic", "state": state},
                follow_redirects=False,
            )
            assert replay.status_code == 400

            oauth_login = TestClient(app)
            login_start = oauth_login.get("/api/auth/google/login", follow_redirects=False)
            login_callback = oauth_login.get(
                "/api/auth/google/callback",
                params={"code": "synthetic", "state": _state_from(login_start)},
                follow_redirects=False,
            )
            assert login_callback.status_code == 303
            assert oauth_login.get("/api/auth/me").json()["name"] == admin_id

        # New OAuth signups use the same browser binding and existing public
        # signup rules, without changing the resulting session format.
        oauth_signup_claims = {
            "sub": "synthetic-new-identity",
            "email": "oauth-new@synthetic.invalid",
            "email_verified": True,
            "name": "OAuth New",
        }
        with patch.object(oauth, "_require_provider", return_value=None), \
             patch.object(oauth, "_client_id", return_value="synthetic-client"), \
             patch.object(oauth, "_client_secret", return_value="synthetic-secret"), \
             patch.object(oauth, "_discovery", return_value=provider_discovery), \
             patch.object(oauth.httpx, "post", return_value=_TokenResponse()), \
             patch.object(oauth, "_verify_id_token", return_value=oauth_signup_claims):
            oauth_signup = TestClient(app)
            signup_start = oauth_signup.get("/api/auth/google/login", follow_redirects=False)
            signup_callback = oauth_signup.get(
                "/api/auth/google/callback",
                params={"code": "synthetic", "state": _state_from(signup_start)},
                follow_redirects=False,
            )
            assert signup_callback.status_code == 303
            assert oauth_signup.get("/api/auth/me").json()["role"] == "viewer"

        # A report measure label is never interpolated as a SQL identifier.
        injected_alias = (
            'n", (SELECT full_name FROM "User" WHERE name=' + repr(admin_id) + ') '
            'AS "private_marker'
        )
        aggregate = analytics.aggregate_semantic_dataset(
            dataset="sales_invoices",
            group_by=[],
            measures={injected_alias: ["count"]},
        )
        assert "private_marker" not in aggregate["rows"][0]
        assert set(aggregate["rows"][0]) == {injected_alias}

        # Stored report drafts accept only the bounded declarative language.
        valid_report = {
            "title": "Safe report",
            "data_requests": [{
                "name": "sales", "dataset": "sales_invoices", "fields": ["customer", "net_total"]
            }],
            "report": {
                "version": 1,
                "tables": [{
                    "id": "main", "title": "Revenue", "source": "sales",
                    "dimensions": [{"field": "customer"}],
                    "measures": [{"key": "revenue", "op": "sum", "field": "net_total"}],
                    "columns": [
                        {"key": "customer", "label": "Customer"},
                        {"key": "revenue", "label": "Revenue", "type": "currency"},
                    ],
                }],
                "charts": [{
                    "title": "Revenue", "type": "bar", "data_table": "main",
                    "x": "customer", "y": "revenue",
                }],
            },
        }
        analytics.ReportDraftPayload.model_validate(valid_report)

        # Accounting data is available to custom reports independently of the
        # invoice tables, with normal-balance income/expense fields suitable
        # for a two-series monthly P&L chart.
        gl_aggregate = analytics.aggregate_semantic_dataset(
            dataset="gl_entries",
            group_by=[],
            measures={
                "income": ["sum", "income_amount"],
                "expenses": ["sum", "expense_amount"],
            },
            filters={"posting_date": {"from": "2025-01-01", "to": "2025-12-31"}},
        )
        assert gl_aggregate["rows"] == [{"income": 900.0, "expenses": 250.0}]

        multi_series_report = {
            "title": "Monthly income and expenses",
            "data_requests": [{
                "name": "ledger", "dataset": "gl_entries",
                "fields": ["posting_date", "income_amount", "expense_amount"],
                "filters": {"posting_date": {"from": "2025-01-01", "to": "2025-12-31"}},
            }],
            "report": {
                "version": 1,
                "tables": [{
                    "id": "monthly", "title": "Monthly P&L", "source": "ledger",
                    "dimensions": [{"field": "posting_date", "key": "month", "bucket": "month"}],
                    "measures": [
                        {"key": "income", "op": "sum", "field": "income_amount", "type": "currency"},
                        {"key": "expenses", "op": "sum", "field": "expense_amount", "type": "currency"},
                    ],
                    "columns": [
                        {"key": "month", "label": "Month"},
                        {"key": "income", "label": "Income", "type": "currency"},
                        {"key": "expenses", "label": "Expenses", "type": "currency"},
                    ],
                    "sort": [{"field": "month", "direction": "asc"}],
                }],
                "charts": [{
                    "title": "Income vs expenses", "type": "bar", "data_table": "monthly",
                    "x": "month",
                    "series": [
                        {"key": "income", "label": "Income"},
                        {"key": "expenses", "label": "Expenses"},
                    ],
                }],
            },
        }
        analytics.ReportDraftPayload.model_validate(multi_series_report)

        # The specialist uses the existing OpenAI credential and Terra model;
        # no Anthropic SDK/key is needed. Provider traffic stays mocked here.
        specialist_call = {}

        def create_specialist_response(**kwargs):
            specialist_call.update(kwargs)
            return NS(
                output_text=json.dumps(multi_series_report),
                output=[],
                usage=NS(input_tokens=100, output_tokens=50, input_tokens_details=None),
            )

        specialist_client = NS(responses=NS(create=create_specialist_response))
        with patch.object(chat, "OpenAI", return_value=specialist_client):
            generated = chat._generate_report_spec_via_openai(
                "Monthly income and expenses for 2025", user_role="admin",
            )
        assert generated["report"]["charts"][0]["series"][1]["key"] == "expenses"
        assert specialist_call["model"] == "gpt-5.6-terra"
        try:
            analytics.ReportDraftPayload.model_validate({
                **valid_report,
                "transform_js": "fetch('/api/auth/users')",
            })
        except Exception:
            pass
        else:
            raise AssertionError("Executable report fields must be rejected")

        # Setup metadata is public only as a boolean; valid authenticated REST
        # callers continue to receive the profile used by the existing UI/API.
        public_setup = TestClient(app).get("/api/setup/status")
        assert public_setup.status_code == 200 and public_setup.json() == {"setup_complete": True}
        assert admin_client.get("/api/setup/status").json()["companies"][0]["name"] == "Synthetic Co"

        # Attachments can only be associated with the caller's own chat session,
        # and ownership is checked before the upload body is read.
        db.insert("Chat Session", {
            "id": "admin-session", "title": "Admin", "user_id": admin_id,
            "created_at": now(), "updated_at": now(),
        })
        viewer_id = response.json()["name"]
        db.insert("Chat Session", {
            "id": "viewer-session", "title": "Viewer", "user_id": viewer_id,
            "created_at": now(), "updated_at": now(),
        })
        db.commit()

        class TrackingUpload(UploadFile):
            was_read = False

            async def read(self, size=-1):
                self.was_read = True
                return await super().read(size)

        foreign_file = TrackingUpload(filename="safe.txt", file=io.BytesIO(b"synthetic"))
        foreign_file.headers = {"content-type": "text/plain"}
        try:
            asyncio.run(attachments.upload_attachment(
                session_id="admin-session",
                file=foreign_file,
                user={"name": viewer_id, "role": "viewer"},
            ))
        except HTTPException as exc:
            assert exc.status_code == 404
        else:
            raise AssertionError("Cross-user attachment association was accepted")
        assert not foreign_file.was_read

        own_file = UploadFile(filename="safe.txt", file=io.BytesIO(b"synthetic"))
        own_file.headers = {"content-type": "text/plain"}
        with patch.object(attachments, "_ensure_upload_dir", return_value=tmp):
            uploaded = asyncio.run(attachments.upload_attachment(
                session_id="viewer-session",
                file=own_file,
                user={"name": viewer_id, "role": "viewer"},
            ))
        stored_upload = db.sql(
            'SELECT session_id FROM "Chat Attachment" WHERE id = ?', [uploaded["id"]]
        )
        assert stored_upload[0]["session_id"] == "viewer-session"

        # A role change that lands while the model is thinking is enforced at
        # the actual tool-dispatch boundary, before any handler can mutate data.
        async def dispatch_after_demotion():
            responses = iter([
                (NS(content="", tool_calls=[NS(
                    id="demotion-check",
                    function=NS(name="create_master", arguments=json.dumps({
                        "master_type": "customer",
                        "data": {"customer_name": "Must Not Exist"},
                    })),
                )]), None),
                (NS(content="Done", tool_calls=[]), None),
            ])
            messages = []

            def orchestrator(*args, **kwargs):
                if not messages:
                    db.set_value("User", manager_id, {"role": "viewer"})
                return next(responses)

            async def inline_thread(fn, *args, **kwargs):
                return fn(*args, **kwargs)

            manager = auth.make_auth_principal({
                "name": manager_id,
                "email": "manager@synthetic.invalid",
                "full_name": "Manager",
                "role": "manager",
                "enabled": 1,
            }, auth.SESSION_CREDENTIAL)
            with patch.object(chat, "OpenAI", return_value=NS()), \
                 patch.object(chat, "_orchestrator_turn", side_effect=orchestrator), \
                 patch.object(chat.asyncio, "to_thread", new=inline_thread), \
                 patch.object(chat.demo_limiter, "settle"), \
                 patch.object(chat.demo_limiter, "reserve", return_value=(None, None)):
                await chat.run_thinking_loop(
                    messages, lambda _event: asyncio.sleep(0),
                    user_info=manager, max_iterations=2,
                )
            return [json.loads(message["content"]) for message in messages if message["role"] == "tool"]

        dispatch_results = asyncio.run(dispatch_after_demotion())
        assert "not available to role 'viewer'" in dispatch_results[0]["error"]
        assert not db.sql(
            'SELECT name FROM "Customer" WHERE customer_name = ?', ["Must Not Exist"]
        )

        # Long-lived WebSocket sessions re-resolve enabled/role state before a
        # message, close disabled users, and never invoke the turn dispatcher.
        seen_turns = []

        async def record_turn(*args, **kwargs):
            seen_turns.append((args, kwargs))

        class SyntheticWebSocket:
            def __init__(self):
                self.sent = []
                self.closed = None

            async def accept(self):
                pass

            async def send_json(self, payload):
                self.sent.append(payload)

            async def receive_text(self):
                db.set_value("User", admin_id, {"enabled": 0, "role": "viewer"})
                return json.dumps({"type": "send_message", "session_id": "admin-session", "content": "write"})

            async def close(self, code, reason):
                self.closed = (code, reason)

        socket = SyntheticWebSocket()
        principal = auth.make_auth_principal({
            "name": admin_id,
            "email": "admin@synthetic.invalid",
            "full_name": "Admin",
            "role": "admin",
            "enabled": 1,
        }, auth.SESSION_CREDENTIAL)
        with patch.object(chat, "run_session_turn", new=record_turn):
            asyncio.run(chat.chat_websocket(socket, user_info=principal))
        assert socket.closed and socket.closed[0] == 4001 and not seen_turns

        print(
            "PASS: API/session compatibility, OAuth binding, SQL aliases, declarative reports, "
            f"setup privacy, attachment ownership, and WebSocket refresh on {backend}"
        )


if __name__ == "__main__":
    check_security_regressions()
