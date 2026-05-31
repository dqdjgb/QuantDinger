"""
Time / time-zone helpers for serializing datetimes to the frontend.

Background
----------
Most ``qd_*`` tables use ``TIMESTAMP WITHOUT TIME ZONE`` columns.  Combined with
``NOW()`` and a container ``TZ`` (e.g. ``Asia/Shanghai``), PostgreSQL stores a
*naive* wall-clock value in the server's time zone.  When the backend then
serializes that ``datetime`` with ``.isoformat()`` the result has **no time
zone suffix** (e.g. ``"2026-05-08T19:36:00"``).

The frontend uses ``new Date(text)`` to parse it; modern browsers interpret a
naive ISO string as the *browser's local time*, which yields wrong values for
any user whose browser time zone differs from the server's.

To fix this we always serialize timestamps as **UTC ISO 8601 with a ``Z``
suffix**.  The browser then renders them in whatever locale the user is in,
without any further work on the frontend.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

try:
    from zoneinfo import ZoneInfo  # py>=3.9
except Exception:  # pragma: no cover - fallback for very old runtimes
    ZoneInfo = None  # type: ignore[misc,assignment]


def _server_tzinfo() -> timezone:
    """Resolve the server's wall-clock time zone.

    Reads the ``TZ`` env var (set by docker-compose).  Falls back to UTC if the
    name is unknown or zoneinfo is unavailable.
    """
    name = (os.getenv("TZ") or "UTC").strip() or "UTC"
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)  # type: ignore[return-value]
        except Exception:
            pass
    return timezone.utc


def _named_tzinfo(name: str) -> timezone:
    """Resolve an IANA timezone name, falling back to UTC."""
    normalized = (name or "").strip()
    if normalized in ("UTC", "Etc/UTC", "Z"):
        return timezone.utc
    if normalized in ("Asia/Shanghai", "Asia/Chongqing", "PRC", "CST", "UTC+8", "UTC+08:00"):
        return timezone(timedelta(hours=8))
    if ZoneInfo is not None:
        try:
            return ZoneInfo(normalized)  # type: ignore[return-value]
        except Exception:
            pass
    return timezone.utc


def _coerce_datetime(value: Any, naive_tz: timezone) -> Optional[datetime]:
    """Parse supported timestamp values and attach ``naive_tz`` when needed."""
    if value is None or value == "":
        return None

    dt: Optional[datetime] = None

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        try:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            normalized = s.replace("Z", "+00:00") if s.endswith("Z") else s
            if " " in normalized and "T" not in normalized:
                normalized = normalized.replace(" ", "T", 1)
            dt = datetime.fromisoformat(normalized)
        except Exception:
            return None
    else:
        return None

    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=naive_tz)
    return dt


def to_utc_iso(value: Any) -> Optional[str]:
    """Convert a value to a UTC ISO 8601 string with a ``Z`` suffix.

    Accepts ``datetime``, ISO strings, numeric epoch seconds, or ``None``.
    Returns ``None`` for falsy inputs that aren't valid timestamps.

    Rules
    -----
    * Aware ``datetime`` → converted to UTC.
    * Naive ``datetime`` → assumed to be in the server's wall-clock time zone
      (``TZ`` env var), then converted to UTC.  This matches how PostgreSQL
      ``NOW()`` writes ``TIMESTAMP WITHOUT TIME ZONE`` columns when the
      container ``TZ`` is set.
    * Numeric input → treated as epoch seconds (or milliseconds when too large).
    * String input that parses as ISO 8601 → re-emitted in UTC.  If the string
      has no time-zone designator we treat it as server local time.
    * Anything else → ``None`` (the route can decide to fall back to ``str()``).
    """
    dt = _coerce_datetime(value, _server_tzinfo())
    if dt is None:
        return None
    dt_utc = dt.astimezone(timezone.utc)
    # Always emit with trailing Z and second-precision (drop microseconds for
    # smaller, cleaner payloads).  ISO 8601 with Z is unambiguous for all
    # browsers.
    return dt_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def to_timezone_iso(value: Any, tz_name: str, assume_naive_tz: str = "UTC") -> Optional[str]:
    """Convert a timestamp to an ISO string in ``tz_name``.

    ``assume_naive_tz`` matters for PostgreSQL ``TIMESTAMP`` values.  The
    backend connection forces PostgreSQL sessions to UTC, so strategy runtime
    log rows are naive UTC values and should not be interpreted as server-local
    wall clock.
    """
    dt = _coerce_datetime(value, _named_tzinfo(assume_naive_tz))
    if dt is None:
        return None
    target = _named_tzinfo(tz_name)
    return dt.astimezone(target).replace(microsecond=0).isoformat()


__all__ = ["to_utc_iso", "to_timezone_iso"]
