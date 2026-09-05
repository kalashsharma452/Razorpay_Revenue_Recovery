from datetime import datetime, timezone, timedelta

# Offset added to real wall-clock time.
# Set to a positive timedelta to fast-forward the simulation clock.
# e.g. _offset = timedelta(hours=24) makes "now" appear 24 hours ahead.
_offset: timedelta = timedelta(0)


def get_simulated_now() -> datetime:
    return datetime.now(timezone.utc) + _offset


def advance_clock(hours: float = 0, minutes: float = 0) -> datetime:
    """Move the simulation clock forward. Returns the new simulated now."""
    global _offset
    _offset += timedelta(hours=hours, minutes=minutes)
    return get_simulated_now()


def reset_clock() -> None:
    global _offset
    _offset = timedelta(0)


def get_offset_hours() -> float:
    return _offset.total_seconds() / 3600
