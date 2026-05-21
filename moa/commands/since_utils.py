"""Shared helpers for parsing ``--since`` command-line values."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_RELATIVE_SINCE_RE = re.compile(
    (
        r"(?P<amount>[+-]?\d+)\s*"
        r"(?P<unit>d(?:ays?)?|h(?:ours?)?|m(?:in(?:ute)?s?)?|w(?:eeks?)?)"
    ),
    flags=re.IGNORECASE,
)


def parse_relative_since(value: str, now: datetime | None = None) -> datetime | None:
    """Parse relative offsets like ``-3d`` or ``-4 d`` and return UTC datetimes."""
    match = _RELATIVE_SINCE_RE.fullmatch(value.strip())
    if not match:
        return None
    amount = int(match.group("amount"))
    unit = match.group("unit").lower()
    if unit in {"d", "day", "days"}:
        delta = {"days": amount}
    elif unit in {"h", "hour", "hours"}:
        delta = {"hours": amount}
    elif unit in {"m", "min", "mins", "minute", "minutes"}:
        delta = {"minutes": amount}
    else:
        delta = {"weeks": amount}
    return (now or datetime.now(timezone.utc)) + timedelta(**delta)
