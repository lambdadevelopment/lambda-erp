"""Microsoft sign-in: which tokens are accepted and which e-mail is trusted.

Tokens are signed here with a throwaway key that stands in for Microsoft's;
no network, synthetic identities only. Runs on SQLite (temp file) or on
Postgres when LAMBDA_ERP_TEST_DB is set.
"""
import os
import time
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from jose import jwk, jwt

os.environ.setdefault("JWT_SECRET_KEY", "synthetic-microsoft-login-test-secret")

from api import auth, oauth  # noqa: E402
from lambda_erp.database import setup  # noqa: E402

CLIENT = "synthetic-client-id"
ORG = "11111111-2222-3333-4444-555555555555"
OTHER_ORG = "99999999-8888-7777-6666-555555555555"
PERSONAL = oauth.MICROSOFT_CONSUMER_TENANT
USER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_pem = _private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode()
_public = jwk.construct(_private.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode(), "RS256").to_dict()
JWKS = {"keys": [{**_public, "kid": "test-key", "use": "sig"}]}


def token(tid=ORG, nonce="n-1", **claims):
    now = int(time.time())
    body = {"aud": CLIENT, "iss": f"https://login.microsoftonline.com/{tid}/v2.0", "tid": tid, "oid": USER,
            "sub": "pairwise-subject", "iat": now, "exp": now + 600, "nonce": nonce,
            "preferred_username": "anna@clinic.example", "name": "Anna Example"}
    body.update(claims)
    body = {key: value for key, value in body.items() if value is not None}
    return jwt.encode(body, _pem, algorithm="RS256", headers={"kid": "test-key"})


class MicrosoftLogin(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "MICROSOFT_OAUTH_CLIENT_ID": CLIENT, "MICROSOFT_OAUTH_CLIENT_SECRET": "synthetic-secret"})
        self.env.start()
        os.environ.pop("MICROSOFT_OAUTH_TENANT", None)
        self.keys = patch.object(oauth, "_jwks", return_value=JWKS)
        self.keys.start()

    def tearDown(self):
        self.keys.stop()
        self.env.stop()

    def verify(self, **claims):
        return oauth._verify_id_token("microsoft", token(**claims), "n-1")

    def refused(self, status=400, **claims):
        with self.assertRaises(HTTPException) as caught:
            self.verify(**claims)
        self.assertEqual(caught.exception.status_code, status)
        return caught.exception.detail

    def test_configured_by_its_two_variables(self):
        self.assertIn("microsoft", oauth.configured_providers())
        with patch.dict(os.environ, {"MICROSOFT_OAUTH_CLIENT_SECRET": ""}):
            self.assertNotIn("microsoft", oauth.configured_providers())
        self.assertEqual(oauth._discovery_url("microsoft"),
                         "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration")

    def test_organisation_account(self):
        got = self.verify(email="anna@clinic.example")
        self.assertEqual((got["sub"], got["email"], got["email_verified"]),
                         (f"{ORG}:{USER}", "anna@clinic.example", True))
        # An alias in the tenant's own domain is the mailbox; the sign-in name is the fallback.
        self.assertEqual(self.verify(email="Anna.Example@clinic.example")["email"], "anna.example@clinic.example")
        self.assertEqual(self.verify(email=None)["email"], "anna@clinic.example")

    def test_an_email_claim_from_another_domain_is_not_believed(self):
        # The tenant's admin wrote someone else's address into the profile.
        got = self.verify(email="ceo@victim.example")
        self.assertEqual((got["email"], got["email_verified"]), ("anna@clinic.example", True))
        # A guest of the tenant has no address that tenant can vouch for.
        got = self.verify(email="ceo@victim.example",
                          preferred_username="ceo_victim.example#EXT#@clinic.onmicrosoft.example")
        self.assertEqual((got["email"], got["email_verified"]), (None, False))

    def test_personal_account(self):
        got = self.verify(tid=PERSONAL, email="anna@outlook.example", preferred_username="anna@outlook.example")
        self.assertEqual((got["sub"], got["email"], got["email_verified"]),
                         (f"{PERSONAL}:{USER}", "anna@outlook.example", True))
        self.assertEqual(self.verify(tid=PERSONAL, oid=None, email="anna@outlook.example")["sub"], "pairwise-subject")

    def test_tokens_that_are_refused(self):
        self.assertIn("issuer does not match", self.refused(iss=f"https://login.microsoftonline.com/{OTHER_ORG}/v2.0"))
        self.assertIn("issuer does not match", self.refused(iss="https://login.example/common/v2.0"))
        self.assertIn("no tenant", self.refused(tid=None, iss="https://login.microsoftonline.com/common/v2.0"))
        self.assertIn("rejected", self.refused(aud="another-app"))
        self.assertIn("rejected", self.refused(exp=int(time.time()) - 60))
        self.assertIn("nonce", self.refused(nonce="n-2"))
        # Signed by someone else.
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        forged = jwt.encode(jwt.get_unverified_claims(token()), other, algorithm="RS256", headers={"kid": "test-key"})
        with self.assertRaises(HTTPException):
            oauth._verify_id_token("microsoft", forged, "n-1")

    def test_who_may_sign_in_is_configurable(self):
        for tenant, org, personal in (("common", True, True), ("organizations", True, False),
                                      ("consumers", False, True), (ORG, True, False), (OTHER_ORG, False, False)):
            with patch.dict(os.environ, {"MICROSOFT_OAUTH_TENANT": tenant}):
                for tid, allowed in ((ORG, org), (PERSONAL, personal)):
                    if allowed:
                        self.verify(tid=tid)
                    else:
                        self.refused(403, tid=tid)
        with patch.dict(os.environ, {"MICROSOFT_OAUTH_TENANT": "not a tenant"}):
            self.refused(500)

    def test_sign_in_end_to_end(self):
        from tests.test_rest_api import _reset_db
        db = setup(_reset_db())
        self.addCleanup(db.conn.close)
        app = FastAPI()
        app.include_router(auth.router, prefix="/api")
        app.include_router(oauth.router, prefix="/api")
        discovery = {"authorization_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                     "token_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/token"}

        class Answer:
            status_code = 200

            def __init__(self, id_token):
                self.id_token = id_token

            def json(self):
                return {"id_token": self.id_token}

        def sign_in(client, **claims):
            start = client.get("/api/auth/microsoft/login", follow_redirects=False)
            self.assertEqual(start.status_code, 303)
            location = start.headers["location"]
            self.assertTrue(location.startswith(discovery["authorization_endpoint"]))
            query = dict(part.split("=", 1) for part in location.split("?", 1)[1].split("&"))
            self.assertEqual(query["client_id"], CLIENT)
            self.assertTrue(query["redirect_uri"].endswith("%2Fapi%2Fauth%2Fmicrosoft%2Fcallback"))
            nonce = db.sql('SELECT nonce FROM "OAuth Flow" ORDER BY creation DESC, expires_at DESC LIMIT 1')[0]["nonce"]
            from urllib.parse import unquote
            with patch.object(oauth.httpx, "post", return_value=Answer(token(nonce=nonce, **claims))):
                return client.get("/api/auth/microsoft/callback",
                                  params={"code": "synthetic", "state": unquote(query["state"])},
                                  follow_redirects=False)

        with patch.object(oauth, "_discovery", return_value=discovery):
            self.assertEqual([p["id"] for p in TestClient(app).get("/api/auth/oauth/providers").json()["providers"]],
                             ["microsoft"])
            # The first user of an empty installation becomes its admin.
            first = TestClient(app)
            done = sign_in(first, email="anna@clinic.example")
            self.assertEqual((done.status_code, done.headers["location"]), (303, "/"))
            me = first.get("/api/auth/me").json()
            self.assertEqual((me["email"], me["role"]), ("anna@clinic.example", "admin"))
            # The same Microsoft user again, from another browser: the same ERP user.
            again = TestClient(app)
            sign_in(again, email="anna@clinic.example")
            self.assertEqual(again.get("/api/auth/me").json()["name"], me["name"])
            # A different Microsoft user without an invite is not let in...
            other = TestClient(app)
            done = sign_in(other, oid="ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee", preferred_username="ben@clinic.example")
            self.assertTrue(done.headers["location"].endswith("oauth_error=registration_closed"))
            # ...and claiming the admin's address from another tenant does not make them the admin.
            done = sign_in(other, tid=OTHER_ORG, oid="ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee",
                           email="anna@clinic.example", preferred_username="mallory@attacker.example")
            self.assertTrue(done.headers["location"].endswith("oauth_error=registration_closed"))
            self.assertEqual(other.get("/api/auth/me").status_code, 401)
            self.assertEqual(db.sql('SELECT COUNT(*) AS n FROM "User"')[0]["n"], 1)


if __name__ == "__main__":
    unittest.main()
