"""FastAPI dependency injection."""

from lambda_erp.database import get_db, Database


def get_database() -> Database:
    return get_db()
