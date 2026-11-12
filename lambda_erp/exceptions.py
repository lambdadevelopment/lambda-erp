"""Custom exceptions for Lambda ERP."""


class ValidationError(Exception):
    pass


class MandatoryError(ValidationError):
    pass


class ConflictError(ValidationError):
    """The request conflicts with what is already recorded (HTTP 409)."""


class PermissionDenied(ValidationError):
    """The caller's access profile does not allow this (HTTP 403)."""


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
