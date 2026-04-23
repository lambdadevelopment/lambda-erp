"""Custom exceptions for Lambda ERP."""


class ValidationError(Exception):
    pass


class MandatoryError(ValidationError):
    pass


class InvalidCurrency(ValidationError):
    pass


class NegativeStockError(ValidationError):
    pass


class InvalidAccountError(ValidationError):
    pass


class ClosedAccountingPeriod(ValidationError):
    pass


class InsufficientFunds(ValidationError):
    pass


class DebitCreditNotEqual(ValidationError):
    pass


class DocumentStatusError(ValidationError):
    pass
