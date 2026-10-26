"""Timestamp validity is separate from the age of an otherwise valid reading."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Freshness:
    age_seconds: float | None
    stale: bool | None
    error_code: str | None = None


def assess_timestamp(
    timestamp: datetime | None,
    now: datetime,
    max_age_seconds: int,
    max_future_skew_seconds: int,
) -> Freshness:
    if not isinstance(timestamp, datetime) or timestamp.utcoffset() is None:
        return Freshness(None, None, "invalid_timestamp")
    age = (now.astimezone(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds()
    if age < -max_future_skew_seconds:
        return Freshness(None, None, "future_timestamp")
    return Freshness(max(0.0, age), age > max_age_seconds)
