"""Bank-account master used to map an external IBAN to a GL account."""

import re

from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from lambda_erp.model import Document


_IBAN_RE = re.compile(r"^[A-Z]{2}[0-9A-Z]{13,32}$")


def normalize_iban(value: str | None) -> str:
    """Return a compact uppercase IBAN and validate its checksum.

    IBANs are identifiers, not display strings. Persisting one canonical form
    makes account lookup and CAMT imports deterministic even when users paste
    spaces or lowercase characters from a bank document.
    """
    iban = re.sub(r"\s+", "", value or "").upper()
    if not _IBAN_RE.fullmatch(iban):
        raise ValidationError("IBAN has an invalid format")
    rearranged = iban[4:] + iban[:4]
    numeric = "".join(str(ord(ch) - 55) if ch.isalpha() else ch for ch in rearranged)
    remainder = 0
    for offset in range(0, len(numeric), 9):
        remainder = int(str(remainder) + numeric[offset:offset + 9]) % 97
    if remainder != 1:
        raise ValidationError("IBAN checksum is invalid")
    return iban


class BankAccount(Document):
    """A real-world bank account mapped to one ledger Bank account."""

    DOCTYPE = "Bank Account"
    CHILD_TABLES = {}
    PREFIX = "BANK"

    LINK_FIELDS = {
        "company": "Company",
        "account": "Account",
    }
    ACCOUNT_TYPE_CONSTRAINTS = {
        "account": {"account_type": "Bank"},
    }

    def validate(self):
        if not self.account_name:
            raise ValidationError("Bank Account Name is required")
        if not self.company:
            raise ValidationError("Company is required")
        if not self.account:
            raise ValidationError("GL Account is required")

        self._data["iban"] = normalize_iban(self.iban)
        account = get_db().get_value(
            "Account", self.account, ["company", "account_currency", "account_type"]
        )
        if account:
            if account.get("company") != self.company:
                raise ValidationError("GL Account belongs to a different company")
            account_currency = (account.get("account_currency") or "").upper()
            requested_currency = (self.currency or account_currency).upper()
            if account_currency and requested_currency != account_currency:
                raise ValidationError(
                    f"Bank Account currency {requested_currency} does not match "
                    f"GL Account currency {account_currency}"
                )
            self._data["currency"] = requested_currency

        existing = get_db().sql(
            'SELECT name FROM "Bank Account" WHERE iban = ? AND name <> ? LIMIT 1',
            [self.iban, self.name],
        )
        if existing:
            raise ValidationError("This IBAN is already mapped to another Bank Account")
