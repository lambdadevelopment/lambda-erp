"""Map lambda_erp exceptions to HTTP responses."""

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
