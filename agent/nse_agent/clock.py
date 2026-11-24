"""IST time helpers and NSE session phases."""
from datetime import datetime, time, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
OPEN = time(9, 15)
CLOSE = time(15, 30)
SESSION_MINUTES = 375


def now_ist() -> datetime:
    return datetime.now(IST)


def phase(dt: datetime) -> str:
    """weekend | before | preopen | open | after"""
    if dt.weekday() >= 5:
        return "weekend"
    t = dt.time()
    if t < time(9, 0):
        return "before"
    if t < OPEN:
        return "preopen"
    if t <= CLOSE:
        return "open"
    return "after"


def minutes_since_open(dt: datetime) -> float:
    start = dt.replace(hour=9, minute=15, second=0, microsecond=0)
    return max(0.0, (dt - start).total_seconds() / 60)


def expected_volume_fraction(dt: datetime) -> float:
    """Share of a day's volume normally traded by now. Volume is front-loaded, so this is concave."""
    frac = min(1.0, minutes_since_open(dt) / SESSION_MINUTES)
    return max(0.15, frac ** 0.65)


_FORMATS = ("%Y-%m-%d %H:%M:%S", "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%Y-%m-%dT%H:%M:%S")


def parse_nse_ts(text) -> datetime | None:
    if not text:
        return None
    for fmt in _FORMATS:
        try:
            return datetime.strptime(str(text).strip(), fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None
