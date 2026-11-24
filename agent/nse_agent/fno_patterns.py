"""Chart-pattern reading and historical analogs from daily bars. Pure functions: no network, easy to test.

Two jobs:
  read_chart  names the patterns on the latest bars (breakouts, squeezes, swing structure, candles, RSI) and
              turns them into one directional bias plus context flags such as "coiled".
  analogs     finds past sessions whose recent shape looks like today's and reports what happened next.
              It is statistical memory, not a forecast, so callers give it a small weight and show the sample size.
"""
import math

MIN_BARS = 60


def _clamp(x: float, lo: float = -2.0, hi: float = 2.0) -> float:
    return max(lo, min(hi, x))


# ---- indicator series -----------------------------------------------------------------------------------
def true_ranges(bars) -> list[float]:
    out = []
    for i, b in enumerate(bars):
        pc = bars[i - 1].close if i else b.close
        out.append(max(b.high - b.low, abs(b.high - pc), abs(b.low - pc)))
    return out


def rolling_atr(bars, n: int = 14) -> list[float | None]:
    tr = true_ranges(bars)
    out: list[float | None] = [None] * len(bars)
    for i in range(n - 1, len(bars)):
        out[i] = sum(tr[i + 1 - n:i + 1]) / n
    return out


def rolling_sma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(n - 1, len(values)):
        out[i] = sum(values[i + 1 - n:i + 1]) / n
    return out


def rsi_series(closes: list[float], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(closes)):
        ag = (ag * (n - 1) + gains[i - 1]) / n
        al = (al * (n - 1) + losses[i - 1]) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def bandwidth_series(closes: list[float], n: int = 20) -> list[float | None]:
    """Bollinger bandwidth: (upper - lower) / middle, in percent."""
    out: list[float | None] = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        w = closes[i + 1 - n:i + 1]
        mid = sum(w) / n
        sd = math.sqrt(sum((x - mid) ** 2 for x in w) / n)
        out[i] = 4 * sd / mid * 100 if mid else None
    return out


def realised_vol_ann(closes: list[float], n: int = 20) -> float | None:
    """Close-to-close realised volatility, annualised, in percent."""
    if len(closes) < n + 1:
        return None
    r = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - n, len(closes)) if closes[i - 1] > 0 and closes[i] > 0]
    if len(r) < n // 2:
        return None
    m = sum(r) / len(r)
    return math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1)) * math.sqrt(252) * 100


def swings(bars, k: int = 3) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Pivot highs and lows: a bar that is the extreme of the k bars on each side."""
    highs, lows = [], []
    for i in range(k, len(bars) - k):
        win = bars[i - k:i + k + 1]
        if bars[i].high >= max(b.high for b in win) and bars[i].high > bars[i - 1].high:
            highs.append((i, bars[i].high))
        if bars[i].low <= min(b.low for b in win) and bars[i].low < bars[i - 1].low:
            lows.append((i, bars[i].low))
    return highs, lows


# ---- chart reading --------------------------------------------------------------------------------------
def _pattern(name: str, bias: float, text: str) -> dict:
    return {"name": name, "bias": bias, "text": text}


def read_chart(bars) -> dict:
    """Patterns on the latest bar. Returns {} when there is too little history."""
    if len(bars) < MIN_BARS:
        return {}
    closes = [b.close for b in bars]
    n = len(bars)
    last, prev = bars[-1], bars[-2]
    atrs = rolling_atr(bars)
    atr = atrs[-1] or 0.0
    s20, s50 = rolling_sma(closes, 20), rolling_sma(closes, 50)
    rsi = rsi_series(closes)[-1]
    bw = bandwidth_series(closes)
    pats: list[dict] = []

    prior = bars[-21:-1]
    hi20, lo20 = max(b.high for b in prior), min(b.low for b in prior)
    if last.close > hi20:
        pats.append(_pattern("20-day breakout", 1.5, f"closed above its prior 20-day high {hi20:,.0f}"))
    elif last.close < lo20:
        pats.append(_pattern("20-day breakdown", -1.5, f"closed below its prior 20-day low {lo20:,.0f}"))

    highs, lows = swings(bars[-80:])
    if len(highs) >= 2 and len(lows) >= 2:
        hh, hl = highs[-1][1] > highs[-2][1], lows[-1][1] > lows[-2][1]
        lh, ll = highs[-1][1] < highs[-2][1], lows[-1][1] < lows[-2][1]
        if hh and hl:
            pats.append(_pattern("higher highs, higher lows", 1.0, "the last two swing highs and lows are both rising"))
        elif lh and ll:
            pats.append(_pattern("lower highs, lower lows", -1.0, "the last two swing highs and lows are both falling"))

    if s20[-1] and s50[-1]:
        if last.close > s20[-1] > s50[-1]:
            pats.append(_pattern("bullish average stack", 0.5, f"close above the 20-day {s20[-1]:,.0f} and 50-day {s50[-1]:,.0f} averages"))
        elif last.close < s20[-1] < s50[-1]:
            pats.append(_pattern("bearish average stack", -0.5, f"close below the 20-day {s20[-1]:,.0f} and 50-day {s50[-1]:,.0f} averages"))
        if s20[-6] and s50[-6]:
            if s20[-6] <= s50[-6] and s20[-1] > s50[-1]:
                pats.append(_pattern("20/50 bullish cross", 0.5, "the 20-day average crossed above the 50-day in the last 5 sessions"))
            elif s20[-6] >= s50[-6] and s20[-1] < s50[-1]:
                pats.append(_pattern("20/50 bearish cross", -0.5, "the 20-day average crossed below the 50-day in the last 5 sessions"))

    if rsi is not None:
        if rsi >= 75:
            pats.append(_pattern("overbought", -0.5, f"RSI {rsi:.0f}: stretched, pullbacks are common"))
        elif rsi <= 25:
            pats.append(_pattern("oversold", 0.5, f"RSI {rsi:.0f}: stretched down, bounces are common"))

    body = abs(last.close - last.open)
    rng = last.high - last.low
    if s20[-1] and body > 0:
        if (last.close > last.open and prev.close < prev.open and last.close >= prev.open and last.open <= prev.close
                and body > abs(prev.close - prev.open) and last.close < s20[-1]):
            pats.append(_pattern("bullish engulfing", 0.75, "a large up candle swallowed the prior down candle after a decline"))
        elif (last.close < last.open and prev.close > prev.open and last.close <= prev.open and last.open >= prev.close
                and body > abs(prev.close - prev.open) and last.close > s20[-1]):
            pats.append(_pattern("bearish engulfing", -0.75, "a large down candle swallowed the prior up candle after a rise"))
    if rng > 0 and atr:
        low10 = min(b.low for b in bars[-11:-1])
        high10 = max(b.high for b in bars[-11:-1])
        lower_wick = min(last.open, last.close) - last.low
        upper_wick = last.high - max(last.open, last.close)
        if lower_wick >= 2 * max(body, 0.1 * rng) and upper_wick <= 0.35 * rng and last.low <= low10:
            pats.append(_pattern("hammer at lows", 0.5, "a long lower wick at a 10-day low: sellers were rejected"))
        elif upper_wick >= 2 * max(body, 0.1 * rng) and lower_wick <= 0.35 * rng and last.high >= high10:
            pats.append(_pattern("shooting star at highs", -0.5, "a long upper wick at a 10-day high: buyers were rejected"))

    bw_now = bw[-1]
    hist = [x for x in bw[-120:] if x is not None]
    bw_pct = sum(1 for x in hist if x <= bw_now) / len(hist) * 100 if bw_now is not None and hist else None
    squeeze = bw_pct is not None and bw_pct <= 20
    ranges = true_ranges(bars)
    nr7 = rng > 0 and rng <= min(ranges[-7:])
    inside = last.high < prev.high and last.low > prev.low
    if squeeze:
        pats.append(_pattern("volatility squeeze", 0.0, f"Bollinger bandwidth is in its lowest {max(bw_pct, 1):.0f}% of the last 120 sessions: a larger move is building, direction unknown"))
    if nr7 or inside:
        pats.append(_pattern("narrow-range day" if nr7 else "inside day", 0.0, "the narrowest range of the last 7 sessions" if nr7 else "the range sits inside the prior day's range"))

    sup = [p for i, p in swings(bars[-60:])[1] if p < last.close]
    res = [p for i, p in swings(bars[-60:])[0] if p > last.close]
    bias = _clamp(sum(p["bias"] for p in pats))
    if s20[-1] and s50[-1]:
        trend = "up" if last.close > s20[-1] > s50[-1] else "down" if last.close < s20[-1] < s50[-1] else "range"
    else:
        trend = "range"
    return {"patterns": pats, "bias": bias, "rsi": rsi, "bw_pct": bw_pct, "squeeze": squeeze,
            "coiled": squeeze or nr7 or inside, "support": max(sup) if sup else None,
            "resistance": min(res) if res else None, "rv_ann": realised_vol_ann(closes), "trend": trend,
            "last": last.close, "date": last.date}


def chart_text(chart: dict) -> str:
    names = [p["name"] for p in chart.get("patterns", []) if p["bias"] != 0 or p["name"] == "volatility squeeze"]
    return ", ".join(names) if names else "no clear pattern"


# ---- historical analogs ---------------------------------------------------------------------------------
def _features(closes, atrs, s20, rsi, i):
    a = atrs[i]
    if not a or s20[i] is None or rsi[i] is None or i < 20:
        return None
    return ((closes[i] - closes[i - 5]) / a, (closes[i] - closes[i - 20]) / a, (closes[i] - s20[i]) / a,
            (rsi[i] - 50) / 20, a / closes[i] * 100)


def analogs(bars, horizon: int = 3, k: int = 24, min_bars: int = 150) -> dict | None:
    """What followed the past sessions that looked most like the latest one.

    Features: 5- and 20-day move and distance from the 20-day average (all in ATRs), RSI, and the volatility level.
    Neighbours are at least `horizon` sessions apart so the same episode is not counted twice.
    """
    n = len(bars)
    if n < min_bars or horizon < 1:
        return None
    closes = [b.close for b in bars]
    atrs, s20, rsi = rolling_atr(bars), rolling_sma(closes, 20), rsi_series(closes)
    query = _features(closes, atrs, s20, rsi, n - 1)
    if not query:
        return None
    feats = {}
    for i in range(35, n - 1 - horizon):
        f = _features(closes, atrs, s20, rsi, i)
        if f:
            feats[i] = f
    if len(feats) < 100:
        return None
    dims = len(query)
    sd = []
    for d in range(dims):
        col = [f[d] for f in feats.values()]
        m = sum(col) / len(col)
        sd.append(math.sqrt(sum((x - m) ** 2 for x in col) / len(col)) or 1.0)
    ranked = sorted(feats, key=lambda i: sum(((feats[i][d] - query[d]) / sd[d]) ** 2 for d in range(dims)))
    chosen: list[int] = []
    for i in ranked:
        if all(abs(i - j) >= horizon for j in chosen):
            chosen.append(i)
        if len(chosen) >= k:
            break
    if len(chosen) < 10:
        return None
    rets = sorted(closes[i + horizon] / closes[i] - 1 for i in chosen)
    up = sum(1 for r in rets if r > 0) / len(rets)
    mean = sum(rets) / len(rets)
    edge = up - 0.5
    bias = 0.0 if abs(edge) < 0.1 or edge * mean <= 0 else _clamp(edge * 5)
    pick = lambda q: rets[min(int(q * len(rets)), len(rets) - 1)]  # noqa: E731
    return {"n": len(rets), "horizon": horizon, "up_rate": up, "mean_pct": mean * 100, "median_pct": pick(0.5) * 100,
            "p10_pct": pick(0.1) * 100, "p90_pct": pick(0.9) * 100, "avg_abs_pct": sum(abs(r) for r in rets) / len(rets) * 100,
            "rets": rets, "bias": bias, "pool": len(feats),
            "dates": [bars[i].date for i in sorted(chosen, reverse=True)[:3]]}


def empirical_returns(bars, horizon: int) -> list[float]:
    """Every overlapping `horizon`-session return in the history: the unconditional distribution."""
    closes = [b.close for b in bars]
    return [closes[i + horizon] / closes[i] - 1 for i in range(len(closes) - horizon)] if horizon >= 1 else []


def trading_days(calendar_days: float) -> int:
    return max(1, min(10, round(calendar_days * 5 / 7)))


def analog_text(a: dict) -> str:
    return (f"{a['n']} similar past sessions (of {a['pool']}): {a['up_rate']:.0%} were higher {a['horizon']} sessions later, "
            f"average {a['mean_pct']:+.2f}%, typical range {a['p10_pct']:+.1f}% to {a['p90_pct']:+.1f}%")
