"""Safe, namespace-tolerant parsing for ISO 20022 CAMT bank statements.

The importer deliberately models a CAMT ``Ntry`` as the bank transaction and
keeps its optional ``TxDtls`` children underneath it.  Flattening transaction
details would duplicate the bank statement amount for batch bookings and make
the imported movements disagree with the statement balances.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
import re
import zipfile

from defusedxml import ElementTree as SafeET


MAX_XML_BYTES = 20 * 1024 * 1024
MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_ARCHIVE_FILES = 100
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
SUPPORTED_NAMESPACES = {
    "urn:iso:std:iso:20022:tech:xsd:camt.053.001.04",
    "urn:iso:std:iso:20022:tech:xsd:camt.053.001.08",
}
_PLACEHOLDER_REFERENCES = {"NOTPROVIDED", "NOTAVAILABLE", "NOTAPPLICABLE"}


class CamtError(ValueError):
    """Raised when an upload is unsafe, malformed, or unsupported."""


@dataclass
class CamtDetail:
    amount: Decimal
    currency: str
    credit_debit_indicator: str
    bank_transaction_code: str | None = None
    proprietary_bank_code: str | None = None
    payment_information_id: str | None = None
    instruction_id: str | None = None
    end_to_end_id: str | None = None
    uetr: str | None = None
    transaction_id: str | None = None
    mandate_id: str | None = None
    debtor_name: str | None = None
    debtor_iban: str | None = None
    creditor_name: str | None = None
    creditor_iban: str | None = None
    ultimate_debtor_name: str | None = None
    ultimate_creditor_name: str | None = None
    creditor_reference_type: str | None = None
    creditor_reference: str | None = None
    remittance_information: str | None = None


@dataclass
class CamtEntry:
    index: int
    amount: Decimal
    currency: str
    credit_debit_indicator: str
    status: str
    booking_date: str | None
    value_date: str | None
    reversal: bool
    account_service_reference: str | None
    entry_reference: str | None
    description: str | None
    bank_transaction_code: str | None
    proprietary_bank_code: str | None
    batch_transaction_count: int | None
    exchange_source_currency: str | None
    exchange_target_currency: str | None
    exchange_rate: Decimal | None
    details: list[CamtDetail] = field(default_factory=list)


@dataclass
class CamtStatement:
    index: int
    message_id: str | None
    statement_id: str
    electronic_sequence_number: str | None
    schema_version: str
    account_iban: str
    account_currency: str
    account_name: str | None
    bank_name: str | None
    bank_bic: str | None
    from_date: str | None
    to_date: str | None
    opening_balance: Decimal | None
    closing_balance: Decimal | None
    entries: list[CamtEntry]
    warnings: list[str] = field(default_factory=list)


@dataclass
class CamtDocument:
    source_name: str
    source_sha256: str
    raw_xml: bytes
    statements: list[CamtStatement]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(node, name: str):
    if node is None:
        return []
    return [item for item in node if _local(item.tag) == name]


def _child(node, name: str):
    return next(iter(_children(node, name)), None)


def _path(node, *names: str):
    for name in names:
        node = _child(node, name)
        if node is None:
            return None
    return node


def _text(node, *names: str) -> str | None:
    target = _path(node, *names)
    if target is None:
        return None
    value = (target.text or "").strip()
    return value or None


def _texts(node, name: str) -> list[str]:
    return [(item.text or "").strip() for item in _children(node, name) if (item.text or "").strip()]


def _meaningful_reference(value: str | None) -> str | None:
    if not value:
        return None
    if re.sub(r"[\s_-]", "", value).upper() in _PLACEHOLDER_REFERENCES:
        return None
    return value


def _decimal(node, *names: str, required: bool = False) -> Decimal | None:
    value = _text(node, *names)
    if value is None:
        if required:
            raise CamtError(f"Missing amount at {'/'.join(names)}")
        return None
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise CamtError(f"Invalid decimal amount: {value!r}") from exc


def _currency(amount_node, fallback: str | None = None) -> str:
    return ((amount_node.attrib.get("Ccy") if amount_node is not None else None) or fallback or "").upper()


def _date(node, name: str) -> str | None:
    holder = _child(node, name)
    value = _text(holder, "Dt") or _text(holder, "DtTm")
    return value[:10] if value else None


def _bank_code(node) -> tuple[str | None, str | None]:
    holder = _child(node, "BkTxCd")
    parts = [
        _text(holder, "Domn", "Cd"),
        _text(holder, "Domn", "Fmly", "Cd"),
        _text(holder, "Domn", "Fmly", "SubFmlyCd"),
    ]
    standard = "/".join(value for value in parts if value) or None
    proprietary = _text(holder, "Prtry", "Cd")
    return standard, proprietary


def _party_name(related_parties, role: str) -> str | None:
    party = _child(related_parties, role)
    # camt.053.001.08 wraps the party in Pty; .04 commonly places Nm directly.
    return _text(party, "Pty", "Nm") or _text(party, "Nm")


def _party_iban(related_parties, role: str) -> str | None:
    account = _child(related_parties, f"{role}Acct")
    return _text(account, "Id", "IBAN") or _text(account, "Id", "Othr", "Id")


def _parse_detail(node, fallback_currency: str, fallback_direction: str) -> CamtDetail:
    amount_node = _child(node, "Amt")
    if amount_node is None:
        amount_node = _path(node, "AmtDtls", "TxAmt", "Amt")
    if amount_node is None:
        raise CamtError("A transaction detail has no amount")
    try:
        amount = Decimal((amount_node.text or "").strip())
    except InvalidOperation as exc:
        raise CamtError("A transaction detail has an invalid amount") from exc

    refs = _child(node, "Refs")
    parties = _child(node, "RltdPties")
    remittance = _child(node, "RmtInf")
    unstructured = _texts(remittance, "Ustrd")
    structured_notes: list[str] = []
    creditor_reference = None
    creditor_reference_type = None
    for structured in _children(remittance, "Strd"):
        reference_info = _path(structured, "CdtrRefInf")
        reference = _text(reference_info, "Ref")
        if reference and creditor_reference is None:
            creditor_reference = reference
            creditor_reference_type = (
                _text(reference_info, "Tp", "CdOrPrtry", "Cd")
                or _text(reference_info, "Tp", "CdOrPrtry", "Prtry")
            )
        structured_notes.extend(_texts(structured, "AddtlRmtInf"))

    standard_code, proprietary_code = _bank_code(node)
    return CamtDetail(
        amount=amount,
        currency=_currency(amount_node, fallback_currency),
        credit_debit_indicator=_text(node, "CdtDbtInd") or fallback_direction,
        bank_transaction_code=standard_code,
        proprietary_bank_code=proprietary_code,
        payment_information_id=_meaningful_reference(_text(refs, "PmtInfId")),
        instruction_id=_meaningful_reference(_text(refs, "InstrId")),
        end_to_end_id=_meaningful_reference(_text(refs, "EndToEndId")),
        uetr=_meaningful_reference(_text(refs, "UETR")),
        transaction_id=_meaningful_reference(_text(refs, "TxId")),
        mandate_id=_meaningful_reference(_text(refs, "MndtId")),
        debtor_name=_party_name(parties, "Dbtr"),
        debtor_iban=_party_iban(parties, "Dbtr"),
        creditor_name=_party_name(parties, "Cdtr"),
        creditor_iban=_party_iban(parties, "Cdtr"),
        ultimate_debtor_name=_party_name(parties, "UltmtDbtr"),
        ultimate_creditor_name=_party_name(parties, "UltmtCdtr"),
        creditor_reference_type=creditor_reference_type,
        creditor_reference=creditor_reference,
        remittance_information="\n".join(unstructured + structured_notes) or None,
    )


def _parse_entry(node, index: int, account_currency: str) -> CamtEntry:
    amount_node = _child(node, "Amt")
    if amount_node is None:
        raise CamtError(f"Entry {index} has no amount")
    amount = _decimal(node, "Amt", required=True)
    direction = _text(node, "CdtDbtInd") or ""
    if amount is None or amount < 0:
        raise CamtError(f"Entry {index} amount must be non-negative")
    if direction not in {"CRDT", "DBIT"}:
        raise CamtError(f"Entry {index} has unsupported credit/debit indicator {direction!r}")

    details = []
    batch_count = None
    for detail_group in _children(node, "NtryDtls"):
        batch = _child(detail_group, "Btch")
        if batch is not None and _text(batch, "NbOfTxs"):
            try:
                batch_count = int(_text(batch, "NbOfTxs"))
            except (TypeError, ValueError) as exc:
                raise CamtError(f"Entry {index} has an invalid batch transaction count") from exc
        details.extend(
            _parse_detail(detail, _currency(amount_node, account_currency), direction)
            for detail in _children(detail_group, "TxDtls")
        )

    exchange = _path(node, "AmtDtls", "TxAmt", "CcyXchg")
    standard_code, proprietary_code = _bank_code(node)
    return CamtEntry(
        index=index,
        amount=amount,
        currency=_currency(amount_node, account_currency),
        credit_debit_indicator=direction,
        status=_text(node, "Sts", "Cd") or _text(node, "Sts") or "",
        booking_date=_date(node, "BookgDt"),
        value_date=_date(node, "ValDt"),
        reversal=(_text(node, "RvslInd") or "").lower() == "true",
        account_service_reference=_meaningful_reference(_text(node, "AcctSvcrRef")),
        entry_reference=_meaningful_reference(_text(node, "NtryRef")),
        description=_text(node, "AddtlNtryInf"),
        bank_transaction_code=standard_code,
        proprietary_bank_code=proprietary_code,
        batch_transaction_count=batch_count,
        exchange_source_currency=_text(exchange, "SrcCcy"),
        exchange_target_currency=_text(exchange, "TrgtCcy"),
        exchange_rate=_decimal(exchange, "XchgRate"),
        details=details,
    )


def _signed_balance(node) -> Decimal | None:
    amount = _decimal(node, "Amt")
    if amount is None:
        return None
    return amount if _text(node, "CdtDbtInd") == "CRDT" else -amount


def _parse_statement(node, index: int, message_id: str | None, namespace: str) -> CamtStatement:
    account = _child(node, "Acct")
    iban = _text(account, "Id", "IBAN") or _text(account, "Id", "Othr", "Id")
    currency = (_text(account, "Ccy") or "").upper()
    statement_id = _text(node, "Id")
    if not iban or not currency or not statement_id:
        raise CamtError("Statement must contain an ID, account IBAN, and account currency")

    balances = {}
    for balance in _children(node, "Bal"):
        code = (
            _text(balance, "Tp", "CdOrPrtry", "Cd")
            or _text(balance, "Tp", "CdOrPrtry", "Prtry")
        )
        if code:
            balances[code] = _signed_balance(balance)

    entries = [_parse_entry(entry, i, currency) for i, entry in enumerate(_children(node, "Ntry"), 1)]
    warnings = []
    non_booked = sum(entry.status != "BOOK" for entry in entries)
    if non_booked:
        warnings.append(f"{non_booked} non-booked entries will not be imported")
    for entry in entries:
        if entry.status == "BOOK" and not entry.booking_date:
            if entry.value_date:
                warnings.append(
                    f"Entry {entry.index} has no booking date; its value date will be used"
                )
            else:
                warnings.append(
                    f"Entry {entry.index} has neither a booking date nor a value date and cannot be imported"
                )
        if entry.batch_transaction_count is not None and entry.batch_transaction_count != len(entry.details):
            warnings.append(
                f"Entry {entry.index} declares {entry.batch_transaction_count} batch transactions "
                f"but contains {len(entry.details)} details"
            )
        if len(entry.details) > 1:
            detail_total = sum((detail.amount for detail in entry.details), Decimal("0"))
            if detail_total != entry.amount:
                warnings.append(
                    f"Entry {entry.index} amount does not equal the sum of its transaction details"
                )

    opening = balances.get("OPBD")
    closing = balances.get("CLBD")
    if opening is not None and closing is not None:
        movement = sum(
            (entry.amount if entry.credit_debit_indicator == "CRDT" else -entry.amount)
            for entry in entries
            if entry.status == "BOOK"
        )
        if opening + movement != closing:
            warnings.append("Opening balance plus booked movements does not equal closing balance")

    period = _child(node, "FrToDt")
    service = _path(account, "Svcr", "FinInstnId")
    return CamtStatement(
        index=index,
        message_id=message_id,
        statement_id=statement_id,
        electronic_sequence_number=_text(node, "ElctrncSeqNb"),
        schema_version=namespace.rsplit(":", 1)[-1],
        account_iban=re.sub(r"\s+", "", iban).upper(),
        account_currency=currency,
        account_name=_text(account, "Nm"),
        bank_name=_text(service, "Nm"),
        bank_bic=_text(service, "BICFI"),
        from_date=(_text(period, "FrDtTm") or _text(period, "FrDt") or "")[:10] or None,
        to_date=(_text(period, "ToDtTm") or _text(period, "ToDt") or "")[:10] or None,
        opening_balance=opening,
        closing_balance=closing,
        entries=entries,
        warnings=warnings,
    )


def parse_camt_xml(data: bytes, source_name: str) -> CamtDocument:
    if not data:
        raise CamtError("The uploaded XML file is empty")
    if len(data) > MAX_XML_BYTES:
        raise CamtError(f"XML file exceeds the {MAX_XML_BYTES // 1024 // 1024} MiB limit")
    try:
        root = SafeET.fromstring(data)
    except Exception as exc:
        raise CamtError(f"Invalid or unsafe XML: {exc}") from exc
    namespace = root.tag.split("}", 1)[0].lstrip("{") if "}" in root.tag else ""
    if namespace not in SUPPORTED_NAMESPACES:
        raise CamtError(
            "Unsupported CAMT format. Expected camt.053.001.04 or camt.053.001.08"
        )
    document = _child(root, "BkToCstmrStmt")
    if document is None:
        raise CamtError("The XML document does not contain a bank-to-customer statement")
    group_header = _child(document, "GrpHdr")
    statements = [
        _parse_statement(statement, index, _text(group_header, "MsgId"), namespace)
        for index, statement in enumerate(_children(document, "Stmt"), 1)
    ]
    if not statements:
        raise CamtError("The CAMT document contains no statements")
    return CamtDocument(
        source_name=PurePosixPath(source_name.replace("\\", "/")).name or "statement.xml",
        source_sha256=sha256(data).hexdigest(),
        raw_xml=data,
        statements=statements,
    )


def parse_camt_upload(data: bytes, filename: str) -> list[CamtDocument]:
    """Parse one XML document or a bounded ZIP archive of XML documents."""
    if len(data) > MAX_ARCHIVE_BYTES:
        raise CamtError(f"Upload exceeds the {MAX_ARCHIVE_BYTES // 1024 // 1024} MiB limit")
    stream = BytesIO(data)
    if not zipfile.is_zipfile(stream):
        return [parse_camt_xml(data, filename)]

    documents = []
    total_uncompressed = 0
    try:
        with zipfile.ZipFile(stream) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if len(members) > MAX_ARCHIVE_FILES:
                raise CamtError("ZIP archive contains too many files")
            for member in members:
                normalized = PurePosixPath(member.filename.replace("\\", "/"))
                if normalized.is_absolute() or ".." in normalized.parts:
                    raise CamtError("ZIP archive contains an unsafe path")
                if normalized.suffix.lower() != ".xml":
                    continue
                if member.flag_bits & 0x1:
                    raise CamtError("Encrypted ZIP members are not supported")
                total_uncompressed += member.file_size
                if member.file_size > MAX_XML_BYTES or total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    raise CamtError("ZIP archive expands beyond the allowed size")
                if member.compress_size and member.file_size / member.compress_size > MAX_COMPRESSION_RATIO:
                    raise CamtError("ZIP archive has a suspicious compression ratio")
                with archive.open(member) as handle:
                    raw = handle.read(MAX_XML_BYTES + 1)
                documents.append(parse_camt_xml(raw, normalized.name))
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise CamtError(f"Invalid ZIP archive: {exc}") from exc
    if not documents:
        raise CamtError("ZIP archive contains no XML files")
    return documents


def mask_iban(iban: str) -> str:
    return f"{iban[:4]} … {iban[-4:]}" if len(iban) > 8 else iban
