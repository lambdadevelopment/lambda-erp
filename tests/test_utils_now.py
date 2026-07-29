#!/usr/bin/env python3
"""Tests for lambda_erp.utils.now() — timestamps must be unambiguous instants.

now() stamps every document's creation/modified (and plugin timestamps like the
CRM's occurred_at). It returns a timezone-aware UTC ISO string so a client
parsing it (JS `new Date(...)`) reads the correct instant instead of treating a
naive value as browser-local and rendering it shifted by the viewer's offset.

Run:  python -m tests.test_utils_now
"""
import sys
from datetime import datetime, timezone


def check_now():
    from lambda_erp.utils import now, nowdate

    s = now()
    # Round-trips to an aware datetime (has a tzinfo/offset), and that offset is
    # UTC — the whole point is an unambiguous instant, not a naive local string.
    dt = datetime.fromisoformat(s)
    assert dt.tzinfo is not None, f"now() must be timezone-aware, got naive: {s!r}"
    assert dt.utcoffset() == timezone.utc.utcoffset(None), f"now() must be UTC, got {s!r}"
    # The string itself carries the zone designator (what JS keys off of).
    assert s.endswith("+00:00") or s.endswith("Z"), f"now() missing UTC designator: {s!r}"

    # nowdate() is a pure calendar date (no time, no zone) and stays that way —
    # date-only fields (posting_date, due_date) must not grow a timezone.
    d = nowdate()
    assert "T" not in d and "+" not in d, f"nowdate() must be date-only, got {d!r}"
    from datetime import date as _date
    _date.fromisoformat(d)  # parses as a plain date

    print("  [utils.now] timezone-aware UTC timestamp + date-only nowdate OK")


def main():
    print("utils.now() checks")
    check_now()
    print("All utils.now() checks passed.")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\nFAILED: {e}")
        sys.exit(1)
