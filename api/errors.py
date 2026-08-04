"""Map lambda_erp exceptions to HTTP responses."""

import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from lambda_erp.exceptions import (
    ValidationError,
    MandatoryError,
    DocumentStatusError,
    DebitCreditNotEqual,
    NegativeStockError,
    InvalidAccountError,
    InvalidCurrency,
    InsufficientFunds,
)


# --- DB integrity violations -------------------------------------------------
# A unique/foreign-key violation raised by the driver (psycopg on Postgres,
# sqlite3 on SQLite) has no handler below, so it fell through to FastAPI's opaque
# 500 "Internal Server Error" — which a form may show as a stray red line or
# swallow entirely (e.g. "saving the UID does nothing, no warning"). Map it to a
# clear 409 with the colliding field/value so the UI can actually explain it.

_PG_KEY_RE = re.compile(r"Key \(([^)]+)\)=\(([^)]*)\)")
_SQLITE_UNIQUE_RE = re.compile(r"UNIQUE constraint failed: [^.]+\.([^\s,]+)")


def _integrity_error_types() -> list:
    """The driver IntegrityError classes that are actually installed (SQLite
    always; psycopg only with the [postgres] extra). Import defensively so a
    SQLite-only deployment doesn't need psycopg."""
    types: list = []
    try:
        import sqlite3
        types.append(sqlite3.IntegrityError)
    except Exception:
        pass
    for mod_name in ("psycopg", "psycopg2"):
        try:
            mod = __import__(mod_name)
            err = getattr(mod, "IntegrityError", None) or getattr(
                getattr(mod, "errors", None), "IntegrityError", None
            )
            if err is not None:
                types.append(err)
        except Exception:
            pass
    return types


def _friendly_integrity(msg: str) -> str:
    m = _PG_KEY_RE.search(msg or "")
    if m:
        return f"A record with {m.group(1)} = “{m.group(2)}” already exists."
    m = _SQLITE_UNIQUE_RE.search(msg or "")
    if m:
        return f"A record with this {m.group(1)} already exists."
    if "foreign key" in (msg or "").lower():
        return "This links to a record that doesn't exist."
    return "A record with these details already exists (a unique field is duplicated)."


async def _integrity_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=409, content={"detail": _friendly_integrity(str(exc))})


def register_exception_handlers(app: FastAPI):

    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(DocumentStatusError)
    async def document_status_error(request: Request, exc: DocumentStatusError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ValidationError)
    async def validation_error(request: Request, exc: ValidationError):
        msg = str(exc)
        if msg.endswith("not found"):
            return JSONResponse(status_code=404, content={"detail": msg})
        return JSONResponse(status_code=422, content={"detail": msg})

    @app.exception_handler(MandatoryError)
    async def mandatory_error(request: Request, exc: MandatoryError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(DebitCreditNotEqual)
    async def debit_credit_error(request: Request, exc: DebitCreditNotEqual):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(NegativeStockError)
    async def negative_stock_error(request: Request, exc: NegativeStockError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(InvalidAccountError)
    async def invalid_account_error(request: Request, exc: InvalidAccountError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(InvalidCurrency)
    async def invalid_currency_error(request: Request, exc: InvalidCurrency):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(InsufficientFunds)
    async def insufficient_funds_error(request: Request, exc: InsufficientFunds):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    for _exc_type in _integrity_error_types():
        app.add_exception_handler(_exc_type, _integrity_handler)
