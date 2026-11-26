"""Mail through Microsoft 365 (api/mail.py) against a fake Microsoft: no
network, no real mailbox, synthetic addresses only.

Runs on SQLite (temp file) or on Postgres when LAMBDA_ERP_TEST_DB is set.
"""
import base64
import json
import os
import unittest
from unittest import mock

import httpx

from tests import test_document_requirements as fixtures
from api import mail
from api.mail import MailError, send_mail, update_settings

ENV = {"MAIL_GRAPH_TENANT_ID": "tenant-synthetic", "MICROSOFT_OAUTH_CLIENT_ID": "client-synthetic",
       "MICROSOFT_OAUTH_CLIENT_SECRET": "secret-synthetic"}


class FakeMicrosoft:
    """Answers the token and sendMail calls the way Microsoft does."""

    def __init__(self):
        self.tokens = 0
        self.sent = []
        self.next_send = None  # an exception to raise, or a status code to answer

    def post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        if "/oauth2/v2.0/token" in url:
            self.tokens += 1
            assert kwargs["data"]["grant_type"] == "client_credentials"
            assert kwargs["data"]["scope"] == "https://graph.microsoft.com/.default"
            return httpx.Response(200, json={"access_token": f"token-{self.tokens}", "expires_in": 3599}, request=request)
        assert url.endswith("/sendMail"), url
        outcome, self.next_send = self.next_send, None
        if isinstance(outcome, Exception):
            raise outcome
        if outcome:
            return httpx.Response(outcome, json={"error": {"code": "ErrorAccessDenied", "message": "Access is denied."}},
                                  request=request)
        self.sent.append({"url": url, "token": kwargs["headers"]["Authorization"], **kwargs["json"]})
        return httpx.Response(202, request=request)


class Mail(unittest.TestCase):
    tearDown = fixtures.RequirementsTest.tearDown

    def setUp(self):
        fixtures.RequirementsTest.setUp(self)
        self.microsoft = FakeMicrosoft()
        mail._token_cache.clear()
        patches = [mock.patch.dict(os.environ, ENV), mock.patch.object(mail.httpx, "post", self.microsoft.post)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("MAIL_ALLOWED_RECIPIENTS", None)

    def switch_on(self, sender="innendienst@example.test", senders=None):
        return update_settings(sender=sender, senders=senders or [], enabled=True)

    def log(self, name):
        return self.db.get_value("Mail Log", name, ["status", "error", "sender", "subject", "recipients",
                                                    "reference_doctype", "reference_name"])

    # -- settings ------------------------------------------------------------
    def test_settings_and_status_never_expose_the_secret(self):
        state = mail.status()
        self.assertEqual((state["configured"], state["enabled"], state["ready"]), (True, False, False))
        self.assertNotIn("secret-synthetic", json.dumps(state))
        with self.assertRaisesRegex(MailError, "sending mailbox before"):
            update_settings(enabled=True)
        with self.assertRaisesRegex(MailError, "not an email address"):
            update_settings(sender="not-an-address")
        state = self.switch_on(senders=["Buchhaltung@example.test"])
        self.assertEqual((state["ready"], state["sender"], state["senders"]),
                         (True, "innendienst@example.test", ["buchhaltung@example.test"]))
        with mock.patch.dict(os.environ, {"MAIL_GRAPH_TENANT_ID": ""}):
            self.assertEqual((mail.status()["configured"], mail.status()["ready"]), (False, False))

    def test_nothing_is_sent_while_mail_is_off_or_unconfigured(self):
        with self.assertRaisesRegex(MailError, "switched off"):
            send_mail("kunde@example.test", "Subject", "Body")
        self.switch_on()
        with mock.patch.dict(os.environ, {"MAIL_GRAPH_TENANT_ID": ""}):
            with self.assertRaisesRegex(MailError, "not configured"):
                send_mail("kunde@example.test", "Subject", "Body")
        self.assertEqual(self.microsoft.sent, [])

    # -- sending -------------------------------------------------------------
    def test_send_with_attachment_is_logged_and_the_token_reused(self):
        self.switch_on()
        pdf = b"%PDF-1.7 synthetic"
        result = send_mail(["kunde@example.test", "KUNDE@example.test"], "Auftragsbestätigung SO-0001",
                           "<p>Danke</p>", html=True, cc="copy@example.test", reply_to="innendienst@example.test",
                           attachments=[("SO-0001.pdf", pdf, "application/pdf")],
                           reference=("Sales Order", "SO-0001"), actor="user-1")
        self.assertEqual((result["status"], result["replayed"]), ("Sent", False))
        sent, = self.microsoft.sent
        self.assertTrue(sent["url"].endswith("/users/innendienst@example.test/sendMail"))
        self.assertTrue(sent["saveToSentItems"])
        message = sent["message"]
        self.assertEqual([r["emailAddress"]["address"] for r in message["toRecipients"]], ["kunde@example.test"])
        self.assertEqual(message["ccRecipients"][0]["emailAddress"]["address"], "copy@example.test")
        self.assertEqual(message["body"], {"contentType": "HTML", "content": "<p>Danke</p>"})
        self.assertEqual(base64.b64decode(message["attachments"][0]["contentBytes"]), pdf)
        row = self.log(result["name"])
        self.assertEqual((row["status"], row["reference_doctype"], row["reference_name"]), ("Sent", "Sales Order", "SO-0001"))
        self.assertEqual(json.loads(row["recipients"]), {"to": ["kunde@example.test"], "cc": ["copy@example.test"]})
        send_mail("kunde@example.test", "Second", "Body")
        self.assertEqual(self.microsoft.tokens, 1)
        self.assertEqual(len(mail.mail_log()), 2)

    def test_the_same_message_is_sent_once(self):
        self.switch_on()
        first = send_mail("kunde@example.test", "Eingangsbestätigung", "Danke", idempotency_key="intake:OI-1:receipt")
        again = send_mail("kunde@example.test", "Eingangsbestätigung", "Danke", idempotency_key="intake:OI-1:receipt")
        self.assertEqual((again["name"], again["replayed"]), (first["name"], True))
        self.assertEqual(len(self.microsoft.sent), 1)
        with self.assertRaisesRegex(MailError, "different message"):
            send_mail("other@example.test", "Eingangsbestätigung", "Danke", idempotency_key="intake:OI-1:receipt")

    def test_a_refused_send_can_be_retried_an_unknown_one_not(self):
        self.switch_on()
        self.microsoft.next_send = 403
        with self.assertRaisesRegex(MailError, "may not send from this mailbox") as refused:
            send_mail("kunde@example.test", "Rechnung", "Body", idempotency_key="invoice:SINV-1")
        self.assertFalse(refused.exception.retry)
        self.assertEqual(self.log(refused.exception.log)["status"], "Failed")
        retried = send_mail("kunde@example.test", "Rechnung", "Body", idempotency_key="invoice:SINV-1")
        self.assertEqual((retried["name"], retried["status"]), (refused.exception.log, "Sent"))

        self.microsoft.next_send = 429
        with self.assertRaises(MailError) as throttled:
            send_mail("kunde@example.test", "Later", "Body")
        self.assertTrue(throttled.exception.retry)

        self.microsoft.next_send = httpx.ReadTimeout("synthetic timeout")
        with self.assertRaisesRegex(MailError, "Sent Items") as broken:
            send_mail("kunde@example.test", "Lieferschein", "Body", idempotency_key="delivery:DN-1")
        self.assertEqual(self.log(broken.exception.log)["status"], "Unknown")
        with self.assertRaisesRegex(MailError, "may already have been sent"):
            send_mail("kunde@example.test", "Lieferschein", "Body", idempotency_key="delivery:DN-1")
        self.assertEqual(len(self.microsoft.sent), 1)

    def test_senders_recipients_and_limits(self):
        self.switch_on(senders=["buchhaltung@example.test"])
        send_mail("kunde@example.test", "Rechnung", "Body", sender="Buchhaltung@example.test")
        self.assertIn("/users/Buchhaltung@example.test/", self.microsoft.sent[-1]["url"])
        with self.assertRaisesRegex(MailError, "not one of the sending mailboxes"):
            send_mail("kunde@example.test", "Rechnung", "Body", sender="ceo@example.test")
        with self.assertRaisesRegex(MailError, "not an email address"):
            send_mail("kunde at example", "Subject", "Body")
        with self.assertRaisesRegex(MailError, "at least one recipient"):
            send_mail([], "Subject", "Body")
        with self.assertRaisesRegex(MailError, "subject"):
            send_mail("kunde@example.test", " ", "Body")
        with self.assertRaisesRegex(MailError, "at most 3 MB"):
            send_mail("kunde@example.test", "Big", "Body", attachments=[("big.pdf", b"x" * (3 * 1024 * 1024 + 1), "application/pdf")])
        with self.assertRaisesRegex(MailError, "not valid"):
            send_mail("kunde@example.test", "Bad", "Body", attachments=[("../x.pdf", b"x", "application/pdf")])

    def test_a_development_environment_only_mails_its_allowlist(self):
        self.switch_on()
        with mock.patch.dict(os.environ, {"MAIL_ALLOWED_RECIPIENTS": "tester@example.test, @lambda.example"}):
            send_mail("tester@example.test", "Allowed", "Body")
            send_mail("anyone@lambda.example", "Allowed domain", "Body")
            with self.assertRaisesRegex(MailError, "only mails .*refused: spital@hospital.example"):
                send_mail(["tester@example.test"], "Blocked", "Body", cc="spital@hospital.example")
            self.assertEqual(mail.status()["allowed_recipients"], ["tester@example.test", "@lambda.example"])
        self.assertEqual(len(self.microsoft.sent), 2)


if __name__ == "__main__":
    unittest.main()
