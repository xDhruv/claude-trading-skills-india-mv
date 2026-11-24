"""Pure trading rules from the nse-live-desk skill: indicators, setup classification, trade plan."""
import importlib.util
import math
import pathlib
from dataclasses import dataclass, field
from datetime import datetime

from .clock import expected_volume_fraction
from .nse import Bar, Quote

TICK = 0.05
_CHARGES = None


def _charges():
    global _CHARGES
    if _CHARGES is None:
        path = pathlib.Path(__file__).resolve().parents[2] / "skills" / "trading-costs" / "scripts" / "charges.py"
        if path.is_file():
            spec = importlib.util.spec_from_file_location("nse_agent_charges", path)
            _CHARGES = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(_CHARGES)
        else:
            _CHARGES = False
    return _CHARGES


def net_pnl(segment: str, buy: float, sell: float, qty: int, broker: str) -> float:
    """P&L after charges, using the repo's exact calculator when available, else a 0.12% round-trip estimate."""
    mod = _charges()
    if mod:
        return mod.calculate(segment, buy, sell, qty, broker)["net_pnl"]
    return (sell - buy) * qty - 0.0012 * (buy + sell) * qty / 2


def sma(values: list[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def atr(bars: list[Bar], n: int = 14) -> float | None:
    if len(bars) < n + 1:
        return None
    trs = [max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close)) for p, b in zip(bars, bars[1:])]
    return sum(trs[-n:]) / n


def round_up(price: float) -> float:
    return round(math.ceil(price / TICK - 1e-9) * TICK, 2)


def round_down(price: float) -> float:
    return round(math.floor(price / TICK + 1e-9) * TICK, 2)


@dataclass
class Hit:
    symbol: str
    setup: str  # breakout | gap_hold | pullback | washout | risk_alert
    kind: str  # idea | watch | alert
    horizon: str  # intraday | swing
    reasons: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def evaluate(q: Quote, bars: list[Bar], sma200: float | None, outperf: float | None, now: datetime,
             held: bool = False) -> Hit | None:
    """Classify one live quote against its history. `bars` must exclude today's session."""
    if len(bars) < 50 or q.series not in ("EQ", "BE", "BL", "BT", "IL", "IQ", ""):
        return None
    closes = [b.close for b in bars]
    sma20, sma50 = sma(closes, 20), sma(closes, 50)
    sma50_prev = sma(closes[:-5], 50)
    avg_vol = sum(b.volume for b in bars[-20:]) / 20
    if not sma20 or not sma50 or not sma50_prev or avg_vol <= 0:
        return None
    vol_ratio = q.volume / (avg_vol * expected_volume_fraction(now))
    high20 = max(b.high for b in bars[-20:])
    prior_high = bars[-1].high
    dist52 = (q.high52 - q.ltp) / q.high52 * 100 if q.high52 else 99.0
    above200 = sma200 is None or q.ltp > sma200
    metrics = {"pct": q.pct, "vol_ratio": round(vol_ratio, 2), "sma20": round(sma20, 2), "sma50": round(sma50, 2),
               "sma200": round(sma200, 2) if sma200 else None, "dist52": round(dist52, 1), "outperf": outperf}
    stretched = q.ltp > 1.25 * sma50

    if held and ((q.pct <= -4 and vol_ratio >= 1.5) or (q.ltp < sma50 <= bars[-1].close)):
        return Hit(q.symbol, "risk_alert", "alert", "swing",
                   [f"{q.pct:+.1f}% on {vol_ratio:.1f}x volume", f"below 50-day SMA {sma50:.1f}" if q.ltp < sma50
                    else "sharp drop on volume"], metrics)

    if q.open >= q.prev * 1.02 and q.ltp >= q.open and q.ltp >= prior_high and vol_ratio >= 1.5 \
            and above200 and not stretched:
        return Hit(q.symbol, "gap_hold", "idea", "intraday",
                   [f"gapped up {((q.open / q.prev) - 1) * 100:.1f}% and holding above the open",
                    f"above prior high {prior_high:.1f}", f"{vol_ratio:.1f}x pro-rated volume"], metrics)

    if 1.5 <= q.pct <= 5 and (dist52 <= 3 or q.ltp >= high20) and vol_ratio >= 1.5 and q.ltp > sma50 \
            and above200 and not stretched and (outperf is None or outperf > 0):
        why = [f"{q.pct:+.1f}% on {vol_ratio:.1f}x pro-rated volume",
               "at a 20-day high" if q.ltp >= high20 else f"{dist52:.1f}% below 52-week high",
               "above 50 and 200-day SMA" if sma200 else "above 50-day SMA"]
        if outperf is not None:
            why.append(f"beats NIFTY by {outperf:.1f} pts over 3 months")
        return Hit(q.symbol, "breakout", "idea", "swing", why, metrics)

    if above200 and sma50 > sma50_prev and -3 <= q.pct <= -1 and vol_ratio < 1.0 \
            and abs(q.ltp - sma20) / sma20 <= 0.03 and q.ltp > sma50:
        return Hit(q.symbol, "pullback", "idea", "swing",
                   [f"{q.pct:+.1f}% on light volume ({vol_ratio:.1f}x)", f"within 3% of 20-day SMA {sma20:.1f}",
                    "50-day SMA rising, above 200-day SMA"], metrics)

    if q.pct <= -5 and vol_ratio >= 2 and above200:
        return Hit(q.symbol, "washout", "watch", "swing",
                   [f"{q.pct:+.1f}% on {vol_ratio:.1f}x volume, still above 200-day SMA",
                    f"watch for a reclaim of {q.prev:.1f}"], metrics)
    return None


def plan(hit: Hit, q: Quote, bars: list[Bar], capital: float, risk_pct: float, broker: str) -> dict | None:
    """Entry above the day's high, stop 1.2 ATR (or below the day's low if wider), target 2.2R to 3R so that
    R:R is at least 2:1 after charges. None if it fails."""
    a = atr(bars)
    if not a or hit.kind != "idea":
        return None
    entry = round_up(max(q.high, q.ltp) + TICK)
    stop = round_down(min(entry - 1.2 * a, q.low - TICK))
    risk_ps = entry - stop
    if risk_ps <= 0 or risk_ps / entry > 0.06:
        return None
    qty = min(int((capital * risk_pct / 100) // risk_ps), int(capital * 0.25 // entry))
    if qty < 1:
        return None
    segment = "intraday" if hit.horizon == "intraday" else "delivery"
    for mult in (2.2, 2.4, 2.6, 2.8, 3.0):  # smallest target that still clears 2:1 after charges
        target = round_up(entry + mult * risk_ps)
        win = net_pnl(segment, entry, target, qty, broker)
        loss = -net_pnl(segment, entry, stop, qty, broker)
        if win > 0 and loss > 0 and win / loss >= 2.0:
            break
    else:
        return None
    return {"symbol": q.symbol, "setup": hit.setup, "horizon": hit.horizon, "entry": entry, "stop": stop,
            "target": target, "qty": qty, "risk_rs": round(loss), "reward_rs": round(win),
            "rr_net": round(win / loss, 2), "atr": round(a, 2), "reasons": hit.reasons,
            "metrics": hit.metrics, "status": "pending"}


def tone(nifty_pct: float, nifty_last: float, nifty_sma50: float | None, vix_pct: float,
         ad_ratio: float | None) -> str:
    """trending up | choppy | risk-off (rules from the nse-live-desk skill)."""
    below = nifty_sma50 is not None and nifty_last < nifty_sma50
    if vix_pct > 5 or (below and ad_ratio is not None and ad_ratio < 0.7) or nifty_pct <= -1.5:
        return "risk-off"
    if not below and vix_pct <= 0 and nifty_pct >= 0:
        return "trending up"
    return "choppy"
