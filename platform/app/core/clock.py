"""UTC clock boundary. Simulations inject a clock; naive timestamps are rejected."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Protocol


class ClockPort(Protocol):
    def now(self) -> datetime: ...


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Clock timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class CallableClock:
    def __init__(self, source: Callable[[], datetime]):
        self.source = source

    def now(self) -> datetime:
        return aware_utc(self.source())
