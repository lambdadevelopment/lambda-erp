"""Preview and atomically import CAMT statements into Bank Transactions."""

from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
import json

from lambda_erp.accounting.bank_transaction import BankTransaction
from lambda_erp.accounting.camt import CamtDocument, CamtEntry, CamtStatement, mask_iban
from lambda_erp.database import get_db
from lambda_erp.exceptions import ValidationError
from lambda_erp.utils import new_name, now


_IN_QUERY_CHUNK_SIZE = 500


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def statement_key(document: CamtDocument, statement: CamtStatement) -> str:
    value = f"{document.source_sha256}:{statement.index}".encode()
    return sha256(value).hexdigest()


def entry_external_id(statement: CamtStatement, entry: CamtEntry) -> tuple[str, bool]:
    """Return a stable entry ID and whether it had to use a weaker fallback."""
    reference_type = None
    reference = None
    if entry.account_service_reference:
        reference_type, reference = "ASR", entry.account_service_reference
    elif entry.entry_reference:
        reference_type, reference = "NTRY", entry.entry_reference
    elif len(entry.details) == 1 and entry.details[0].uetr:
        reference_type, reference = "UETR", entry.details[0].uetr

    weak = reference is None
    if weak:
        # The statement position prevents two genuinely identical, reference-
        # less entries in one statement from collapsing. A later re-export may
        # not deduplicate this fallback; the preview explicitly warns about it.
        reference_type = "POSITION"
        reference = f"{statement.statement_id}:{entry.index}"
    material = f"{statement.account_iban}|{reference_type}|{reference}".encode()
    return f"camt:{sha256(material).hexdigest()}", weak


def _existing_external_ids(external_ids: list[str]) -> set[str]:
    """Load duplicate identifiers in bounded, set-based queries.

    CAMT files can contain thousands of entries. Querying once per entry makes
    previews unnecessarily slow and creates avoidable database traffic. The
    conservative chunk size also stays below SQLite's common parameter limit.
    """
    if not external_ids:
        return set()
    db = get_db()
    existing: set[str] = set()
    unique_ids = list(dict.fromkeys(external_ids))
    for offset in range(0, len(unique_ids), _IN_QUERY_CHUNK_SIZE):
        chunk = unique_ids[offset:offset + _IN_QUERY_CHUNK_SIZE]
        placeholders = ", ".join("?" for _ in chunk)
        rows = db.sql(
            f'SELECT external_id FROM "Bank Transaction" '
            f'WHERE external_id IN ({placeholders})',
            chunk,
        )
        existing.update(row["external_id"] for row in rows)
    return existing


def _matched_bank_account(iban: str):
    rows = get_db().sql(
        'SELECT name, account_name, company, account, iban, currency, bank_name '
        'FROM "Bank Account" WHERE iban = ? AND COALESCE(disabled, 0) = 0 LIMIT 1',
        [iban],
    )
    if not rows:
        return None
    result = dict(rows[0])
    result["iban_masked"] = mask_iban(result.pop("iban"))
    return result


def preview_documents(documents: list[CamtDocument]) -> dict:
    db = get_db()
    previews = []
    for document in documents:
        for statement in document.statements:
            key = statement_key(document, statement)
            existing_import = db.sql(
                'SELECT name FROM "Bank Statement Import" '
                'WHERE source_sha256 = ? AND statement_index = ? LIMIT 1',
                [document.source_sha256, statement.index],
            )
            duplicate_entries = 0
            weak_entries = 0
            booked_entries = [entry for entry in statement.entries if entry.status == "BOOK"]
            identifiers = [entry_external_id(statement, entry) for entry in booked_entries]
            existing_external_ids = _existing_external_ids([item[0] for item in identifiers])
            for external_id, weak in identifiers:
                weak_entries += int(weak)
                duplicate_entries += int(external_id in existing_external_ids)

            credits = sum(
                (entry.amount for entry in booked_entries if entry.credit_debit_indicator == "CRDT"),
                Decimal("0"),
            )
            debits = sum(
                (entry.amount for entry in booked_entries if entry.credit_debit_indicator == "DBIT"),
                Decimal("0"),
            )
            details = [detail for entry in booked_entries for detail in entry.details]
            warnings = list(statement.warnings)
            if weak_entries:
                warnings.append(
                    f"{weak_entries} entries have no stable bank reference; repeat-export "
                    "deduplication cannot be guaranteed for them"
                )
            if duplicate_entries:
                warnings.append(f"{duplicate_entries} entries already exist and will be skipped")

            previews.append({
                "statement_key": key,
                "file_name": document.source_name,
                "schema_version": statement.schema_version,
                "statement_id": statement.statement_id,
                "account_iban_masked": mask_iban(statement.account_iban),
                "account_name": statement.account_name,
                "account_currency": statement.account_currency,
                "bank_name": statement.bank_name,
                "bank_bic": statement.bank_bic,
                "from_date": statement.from_date,
                "to_date": statement.to_date,
                "opening_balance": _decimal_text(statement.opening_balance),
                "closing_balance": _decimal_text(statement.closing_balance),
                "credit_total": _decimal_text(credits),
                "debit_total": _decimal_text(debits),
                "entry_count": len(statement.entries),
                "booked_entry_count": len(booked_entries),
                "detail_count": len(details),
                "batch_entry_count": sum(len(entry.details) > 1 for entry in booked_entries),
                "qr_reference_count": sum(
                    detail.creditor_reference_type == "QRR" and bool(detail.creditor_reference)
                    for detail in details
                ),
                "duplicate_entry_count": duplicate_entries,
                "already_imported": bool(existing_import),
                "existing_import": existing_import[0]["name"] if existing_import else None,
                "matched_bank_account": _matched_bank_account(statement.account_iban),
                "warnings": warnings,
            })
    return {"statements": previews}


def _load_and_validate_mapping(bank_account_name: str, statement: CamtStatement) -> dict:
    account = get_db().get_value(
        "Bank Account",
        bank_account_name,
        ["name", "account_name", "company", "account", "iban", "currency", "disabled"],
    )
    if not account or account.get("disabled"):
        raise ValidationError(f"Bank Account {bank_account_name!r} is unavailable")
    if account.get("iban") != statement.account_iban:
        raise ValidationError(
            f"Bank Account {bank_account_name!r} does not match statement IBAN "
            f"{mask_iban(statement.account_iban)}"
        )
    if (account.get("currency") or "").upper() != statement.account_currency:
        raise ValidationError(
            f"Bank Account {bank_account_name!r} currency does not match the statement"
        )
    return dict(account)


def _detail_row(parent: str, index: int, detail, parent_external_id: str) -> dict:
    return {
        "name": new_name("BTD"),
        "parent": parent,
        "idx": index,
        "external_id": f"{parent_external_id}:{detail.uetr or index}",
        "amount": float(detail.amount),
        "currency": detail.currency,
        "credit_debit_indicator": detail.credit_debit_indicator,
        "bank_transaction_code": detail.bank_transaction_code,
        "proprietary_bank_code": detail.proprietary_bank_code,
        "payment_information_id": detail.payment_information_id,
        "instruction_id": detail.instruction_id,
        "end_to_end_id": detail.end_to_end_id,
        "uetr": detail.uetr,
        "transaction_id": detail.transaction_id,
        "mandate_id": detail.mandate_id,
        "debtor_name": detail.debtor_name,
        "debtor_iban": detail.debtor_iban,
        "creditor_name": detail.creditor_name,
        "creditor_iban": detail.creditor_iban,
        "ultimate_debtor_name": detail.ultimate_debtor_name,
        "ultimate_creditor_name": detail.ultimate_creditor_name,
        "creditor_reference_type": detail.creditor_reference_type,
        "creditor_reference": detail.creditor_reference,
        "remittance_information": detail.remittance_information,
    }


def _insert_transaction(import_name: str, bank_account: dict, statement: CamtStatement,
                        entry: CamtEntry, external_id: str, weak: bool) -> str:
    detail = entry.details[0] if len(entry.details) == 1 else None
    direction = entry.credit_debit_indicator
    counterparty_name = None
    counterparty_iban = None
    if detail:
        if direction == "CRDT":
            counterparty_name, counterparty_iban = detail.debtor_name, detail.debtor_iban
        else:
            counterparty_name, counterparty_iban = detail.creditor_name, detail.creditor_iban

    transaction = BankTransaction({
        "bank_account": bank_account["account"],  # legacy GL-account field
        "bank_account_id": bank_account["name"],
        "bank_statement_import": import_name,
        # Never let Document.validate() silently replace missing bank dates
        # with today's date. Value date is an explicit, auditable fallback.
        "posting_date": entry.booking_date or entry.value_date,
        "value_date": entry.value_date,
        "deposit": float(entry.amount) if direction == "CRDT" else 0,
        "withdrawal": float(entry.amount) if direction == "DBIT" else 0,
        "currency": entry.currency,
        "description": entry.description,
        "remittance_information": detail.remittance_information if detail else None,
        "reference_number": (
            (detail.creditor_reference or detail.end_to_end_id) if detail
            else entry.account_service_reference
        ),
        "external_id": external_id,
        "weak_external_id": int(weak),
        "account_service_reference": entry.account_service_reference,
        "entry_reference": entry.entry_reference,
        "credit_debit_indicator": direction,
        "reversal_indicator": int(entry.reversal),
        "bank_transaction_code": entry.bank_transaction_code,
        "proprietary_bank_code": entry.proprietary_bank_code,
        "batch_transaction_count": entry.batch_transaction_count,
        "counterparty_name": counterparty_name,
        "counterparty_iban": counterparty_iban,
        "end_to_end_id": detail.end_to_end_id if detail else None,
        "payment_information_id": detail.payment_information_id if detail else None,
        "uetr": detail.uetr if detail else None,
        "structured_reference_type": detail.creditor_reference_type if detail else None,
        "structured_reference": detail.creditor_reference if detail else None,
        "exchange_source_currency": entry.exchange_source_currency,
        "exchange_target_currency": entry.exchange_target_currency,
        "exchange_rate": float(entry.exchange_rate) if entry.exchange_rate is not None else None,
        "detail_count": len(entry.details),
        "status": "Unreconciled",
        "details": [],
    })
    transaction._children["details"] = [
        _detail_row(transaction.name, index, row, external_id)
        for index, row in enumerate(entry.details, 1)
    ]
    transaction.validate()
    transaction._validate_links()
    transaction._persist(commit=False)
    return transaction.name


def import_documents(documents: list[CamtDocument], mappings: dict[str, str],
                     imported_by: str | None = None) -> dict:
    """Atomically import all new statements in an upload.

    Exact source re-uploads are skipped. A regenerated export with a different
    file hash still creates an audit record, while stable per-entry references
    prevent duplicate Bank Transactions.
    """
    db = get_db()
    prepared = []
    exact_duplicates = []
    source_results: dict[tuple[str, int], str | None] = {}
    repeated_sources: list[tuple[str, int]] = []
    for document in documents:
        for statement in document.statements:
            source_identity = (document.source_sha256, statement.index)
            if source_identity in source_results:
                repeated_sources.append(source_identity)
                continue
            key = statement_key(document, statement)
            existing = db.sql(
                'SELECT name FROM "Bank Statement Import" '
                'WHERE source_sha256 = ? AND statement_index = ? LIMIT 1',
                [document.source_sha256, statement.index],
            )
            if existing:
                exact_duplicates.append(existing[0]["name"])
                source_results[source_identity] = existing[0]["name"]
                continue
            source_results[source_identity] = None
            bank_account_name = mappings.get(key)
            if not bank_account_name:
                raise ValidationError(
                    f"No Bank Account mapping supplied for {mask_iban(statement.account_iban)}"
                )
            bank_account = _load_and_validate_mapping(bank_account_name, statement)
            prepared.append((document, statement, bank_account))

    imported = []
    db._in_transaction = True
    try:
        for document, statement, bank_account in prepared:
            booked = [entry for entry in statement.entries if entry.status == "BOOK"]
            missing_dates = [
                entry.index for entry in booked
                if not entry.booking_date and not entry.value_date
            ]
            if missing_dates:
                raise ValidationError(
                    "Booked CAMT entries require a booking date or value date; "
                    f"missing for entries {', '.join(map(str, missing_dates[:10]))}"
                )
            credits = sum(
                (entry.amount for entry in booked if entry.credit_debit_indicator == "CRDT"),
                Decimal("0"),
            )
            debits = sum(
                (entry.amount for entry in booked if entry.credit_debit_indicator == "DBIT"),
                Decimal("0"),
            )
            import_name = new_name("BSI")
            source_results[(document.source_sha256, statement.index)] = import_name
            created_at = now()
            import_row = {
                "name": import_name,
                "bank_account": bank_account["name"],
                "company": bank_account["company"],
                "source": "Manual Upload",
                "source_filename": document.source_name,
                "source_sha256": document.source_sha256,
                "schema_version": statement.schema_version,
                "message_id": statement.message_id,
                "statement_id": statement.statement_id,
                "statement_index": statement.index,
                "electronic_sequence_number": statement.electronic_sequence_number,
                "account_iban": statement.account_iban,
                "currency": statement.account_currency,
                "from_date": statement.from_date,
                "to_date": statement.to_date,
                "opening_balance": float(statement.opening_balance) if statement.opening_balance is not None else None,
                "closing_balance": float(statement.closing_balance) if statement.closing_balance is not None else None,
                "credit_total": float(credits),
                "debit_total": float(debits),
                "entry_count": len(statement.entries),
                "booked_entry_count": len(booked),
                "detail_count": sum(len(entry.details) for entry in booked),
                "imported_entry_count": 0,
                "duplicate_entry_count": 0,
                "warning_count": len(statement.warnings),
                "warnings_json": json.dumps(statement.warnings),
                "status": "Imported",
                "imported_by": imported_by,
                "docstatus": 0,
                "creation": created_at,
                "modified": created_at,
            }
            db.insert("Bank Statement Import", import_row)
            db.insert("Bank Statement Source", {
                "import_name": import_name,
                "source_data": document.raw_xml,
            })

            created_transactions = []
            duplicate_count = 0
            weak_count = 0
            identifiers = [entry_external_id(statement, entry) for entry in booked]
            existing_external_ids = _existing_external_ids([item[0] for item in identifiers])
            for entry, (external_id, weak) in zip(booked, identifiers):
                weak_count += int(weak)
                if external_id in existing_external_ids:
                    duplicate_count += 1
                    continue
                transaction_name = _insert_transaction(
                    import_name, bank_account, statement, entry, external_id, weak
                )
                created_transactions.append(transaction_name)
                # Also catches a duplicate reference repeated later in this
                # same upload, before the next database lookup.
                existing_external_ids.add(external_id)

            db.set_value("Bank Statement Import", import_name, {
                "imported_entry_count": len(created_transactions),
                "duplicate_entry_count": duplicate_count,
                "warning_count": len(statement.warnings) + int(bool(weak_count)),
            })
            imported.append({
                "name": import_name,
                "bank_account": bank_account["name"],
                "account_iban_masked": mask_iban(statement.account_iban),
                "imported_entry_count": len(created_transactions),
                "duplicate_entry_count": duplicate_count,
                "skipped_non_booked_count": len(statement.entries) - len(booked),
            })
        db.commit()
    except Exception:
        db.conn.rollback()
        raise
    finally:
        db._in_transaction = False

    repeated_imports = [
        source_results[source_identity]
        for source_identity in repeated_sources
        if source_results.get(source_identity)
    ]
    return {
        "imports": imported,
        "exact_duplicate_imports": list(dict.fromkeys(exact_duplicates + repeated_imports)),
    }
