"""Headline tone and scheduled-event risk for the F&O desk. Keyword rules, no model, so the result is explainable.

headline_bias  turns recent headlines into a small directional bias and the lines that drove it.
event_risk     flags known binary events (policy decisions, budget, elections, results). Event risk does not pick a
               direction; it stops the desk from selling premium into a jump and from buying slow structures.
"""
import re
from datetime import datetime, timedelta, timezone

from . import news

POSITIVE_WORDS = ("rally", "surge", "record high", "jumps", "soars", "gains", "rate cut", "upgrade", "beats estimates",
                  "strong results", "order win", "bags order", "ceasefire", "rebound", "buying by fii", "fii buying",
                  "raises guidance", "stake buy", "outperform", "inflows")
NEGATIVE_MARKET = ("sell-off", "selloff", "crash", "plunge", "slump", "tumbles", "falls", "rate hike", "tariff",
                   "war ", "escalat", "fii selling", "outflows", "profit warning", "misses estimates", "downgrade",
                   "inflation surges", "rupee falls", "slides", "bloodbath", "panic", "drops", "fades", "tanks",
                   "sinks", "dips", "retreats")

EVENT_TAGS = {
    "RBI policy": ("rbi", "mpc", "repo rate", "monetary policy"),
    "US Fed": ("fomc", "federal reserve", "fed rate", "fed meeting", "fed policy", "us fed", "powell"),
    "Union Budget": ("budget",),
    "election": ("election", "exit poll", "vote count", "counting day"),
    "tariffs/trade": ("tariff", "trade deal", "trade talks"),
    "geopolitics": ("war ", "ceasefire", "missile", "strike on", "sanctions"),
    "macro data": ("cpi", "gdp", "inflation data", "iip", "pmi"),
    "earnings": ("q1 results", "q2 results", "q3 results", "q4 results", "earnings", "results today", "results on"),
}
SOON = ("today", "tomorrow", "tonight", "ahead of", "on monday", "on tuesday", "on wednesday", "on thursday",
        "on friday", "this evening", "later today", "d-day")
NEAR = ("expected", "preview", "decision", "this week", "next week", "awaits", "eyes", "outcome", "meet ")
MAX_AGE = timedelta(hours=36)


def _clamp(x: float, lo: float = -2.0, hi: float = 2.0) -> float:
    return max(lo, min(hi, x))


def _age(item, now: datetime) -> timedelta | None:
    if not item.published:
        return None
    pub = item.published if item.published.tzinfo else item.published.replace(tzinfo=timezone.utc)
    return now - pub


def _fresh(items, now: datetime):
    for it in items:
        age = _age(it, now)
        if age is None or age <= MAX_AGE:
            yield it, age


def _weight(age: timedelta | None) -> float:
    if age is None or age <= timedelta(hours=6):
        return 1.0
    return 0.6 if age <= timedelta(hours=24) else 0.3


def _core(title: str) -> str:
    """Headline without the trailing ' - Publisher' that feeds append, so 'India Today' is not read as 'today'."""
    return re.sub(r"\s+[-|\u2013]\s+[^-|\u2013]{2,40}$", "", title).strip()


def polarity(title: str) -> int:
    t = " " + _core(title).lower() + " "
    neg = sum(1 for w in NEGATIVE_MARKET + news.NEGATIVE_WORDS if w in t)
    pos = sum(1 for w in POSITIVE_WORDS if w in t)
    return (pos > neg) - (neg > pos)


def headline_bias(items, now: datetime) -> dict:
    """{'bias': -2..2, 'lines': [...], 'n': headlines used}. Recent headlines count more."""
    total, used, scored = 0.0, 0, []
    for it, age in _fresh(items, now):
        p = polarity(it.title)
        if p == 0:
            continue
        w = _weight(age) * p
        total += w
        used += 1
        scored.append((abs(w), p, it.title))
    scored.sort(key=lambda x: -x[0])
    lines = [("up: " if p > 0 else "down: ") + t[:110] for _, p, t in scored[:3]]
    return {"bias": _clamp(total / 2), "lines": lines, "n": used}


def event_risk(items, now: datetime) -> dict:
    """{'level': 0..2, 'tags': [...], 'lines': [...]}. 2 = the event is today or tomorrow, 1 = it is expected soon."""
    level, tags, lines = 0, [], []
    for it, age in _fresh(items, now):
        t = " " + _core(it.title).lower() + " "
        hit = [name for name, words in EVENT_TAGS.items() if any(w in t for w in words)]
        if not hit:
            continue
        lv = 2 if any(w in t for w in SOON) else 1 if any(w in t for w in NEAR) else 0
        if lv == 0:
            continue
        level = max(level, lv)
        for h in hit:
            if h not in tags:
                tags.append(h)
        if len(lines) < 3:
            lines.append(it.title[:110])
    return {"level": level, "tags": tags, "lines": lines}
