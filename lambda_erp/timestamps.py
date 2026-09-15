"""Strict timestamp handling for stored ERP instants and time-window queries."""

import re
from datetime import datetime, timezone


_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?$")
_DUPLICATED_TIME = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)"
    r"T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00$"
)


def parse_timestamp(value, *, require_timezone=False):
    """Parse an instant; historical naive ERP timestamps are explicitly UTC."""
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and _ISO.fullmatch(value):
        offset = re.search(r"[+-](\d{2}):(\d{2})$", value)
        if offset and (int(offset[1]) > 15 or int(offset[2]) > 59):
            raise ValueError("Invalid timezone offset")
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("Expected an ISO timestamp with date and time")
    if result.tzinfo is None:
        if require_timezone:
            raise ValueError("Timestamp must include a timezone (Z or UTC offset)")
        result = result.replace(tzinfo=timezone.utc)
    try:
        return result.astimezone(timezone.utc)
    except OverflowError as exc:
        raise ValueError("Timestamp is outside the supported date range") from exc


def normalize_timestamp(value):
    return parse_timestamp(value).isoformat(timespec="microseconds")


def timestamp_epoch(value):
    """SQLite SQL function: malformed/absent instants are counted as unknown."""
    try:
        return parse_timestamp(value).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def repair_duplicated_time(value):
    """Propose the known CRM space+appended-time repair; never guess other forms.

    Preserve the original first clock reading, treating the historical naive
    timestamp as UTC. The appended clock was the CRM validator's save time.
    Application is a separate, explicit maintenance operation.
    """
    match = _DUPLICATED_TIME.fullmatch(str(value))
    if not match:
        return None
    try:
        return normalize_timestamp(f"{match[1]}T{match[2]}+00:00")
    except ValueError:
        return None


POSTGRES_TIMESTAMP_FUNCTION = r"""
CREATE OR REPLACE FUNCTION erp_timestamp_epoch(value text)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE STRICT AS $$
BEGIN
    IF value !~ '^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d{1,6}){0,1}(Z|[+-]\d{2}:\d{2}){0,1}$'
       OR substr(value, 12, 2)::integer > 23
       OR substr(value, 15, 2)::integer > 59
       OR substr(value, 18, 2)::integer > 59 THEN
        RETURN NULL;
    END IF;
    IF value !~ '(Z|[+-]\d{2}:\d{2})$' THEN
        value := value || '+00:00';
    ELSIF value ~ '[+-]\d{2}:\d{2}$' AND
          (substr(value, length(value)-4, 2)::integer > 15 OR
           right(value, 2)::integer > 59) THEN
        RETURN NULL;
    END IF;
    RETURN extract(epoch FROM value::timestamptz)::double precision;
EXCEPTION WHEN data_exception THEN
    RETURN NULL;
END;
$$;
"""
