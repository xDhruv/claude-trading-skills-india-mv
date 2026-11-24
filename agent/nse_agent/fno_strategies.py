"""Option-strategy library: payoff maths, the NSE booklet's strategy shapes, and a planner for non-directional views.

Everything is per unit of underlying unless a function says "lot". A leg is `SLeg(action, kind, strike, price)`;
kind is CE, PE or FUT (for FUT the strike is the entry price and the price is 0).
The desk only auto-suggests defined-risk structures. Naked shorts and futures combinations are catalogued for study.
"""
import math
from dataclasses import dataclass

from .analysis import net_pnl
from .fno_pricing import bs_price


@dataclass
class SLeg:
    action: str  # BUY | SELL
    kind: str  # CE | PE | FUT
    strike: float
    price: float
    iv: float = 0.0
    ratio: int = 1
    slip: float = 0.0  # full bid-ask spread, charged once round trip

    @property
    def sign(self) -> int:
        return 1 if self.action == "BUY" else -1


# ---- catalogue ------------------------------------------------------------------------------------------
# view: what you expect. iv: when it is cheap to hold. risk: defined | unlimited | futures margin.
CATALOGUE = {
    "long_call": ("Long call", "bullish", "low IV", "defined", True, "Buy a call: profit grows as the market rises, loss is the premium."),
    "short_call": ("Short call", "bearish / capped", "high IV", "unlimited", False, "Sell a call: keep the premium if the market stays below the strike; loss is open-ended above."),
    "long_put": ("Long put", "bearish", "low IV", "defined", True, "Buy a put: profit grows as the market falls, loss is the premium."),
    "short_put": ("Short put", "bullish / stable", "high IV", "unlimited", False, "Sell a put: keep the premium if the market stays above the strike; large loss on a crash."),
    "bull_call": ("Bull call spread", "moderately bullish", "any", "defined", True, "Buy a lower call, sell a higher call: cheaper than a long call, profit capped at the upper strike."),
    "bear_put": ("Bear put spread", "moderately bearish", "any", "defined", True, "Buy a higher put, sell a lower put: cheaper than a long put, profit capped at the lower strike."),
    "bull_put": ("Bull put spread", "moderately bullish", "high IV", "defined", True, "Sell a put, buy a lower put: collect a credit that is kept if the market holds above the short strike."),
    "bear_call": ("Bear call spread", "moderately bearish", "high IV", "defined", True, "Sell a call, buy a higher call: collect a credit that is kept if the market holds below the short strike."),
    "long_straddle": ("Long straddle", "big move, either way", "low IV", "defined", True, "Buy a call and a put at the same strike: profits if the market moves far enough in either direction."),
    "short_straddle": ("Short straddle", "stays put", "high IV", "unlimited", False, "Sell a call and a put at the same strike: best if the market ends exactly there; unlimited loss either way."),
    "long_strangle": ("Long strangle", "big move, either way", "low IV", "defined", True, "Buy an out-of-the-money call and put: cheaper than a straddle, needs a bigger move."),
    "short_strangle": ("Short strangle", "stays in a range", "high IV", "unlimited", False, "Sell an out-of-the-money call and put: wider profit zone than a straddle, unlimited loss beyond it."),
    "long_butterfly": ("Long butterfly", "pins near the middle strike", "high IV", "defined", True, "Buy two outer options, sell two middle ones: small cost, best payoff if the market ends at the middle strike."),
    "short_butterfly": ("Short butterfly", "leaves the middle strike", "low IV", "defined", True, "Sell two outer options, buy two middle ones: credit now, profits if the market moves away from the middle."),
    "long_condor": ("Long condor", "stays in a range", "high IV", "defined", True, "Like a butterfly with a flat top: wider profit zone, smaller peak."),
    "short_condor": ("Short condor", "leaves a range", "low IV", "defined", True, "Credit now, profits if the market moves out of the inner strikes."),
    "iron_condor": ("Iron condor", "stays in a range", "high IV", "defined", True, "Sell an out-of-the-money put spread and call spread together: collect a credit if the market stays between the short strikes."),
    "long_box": ("Long box", "none (arbitrage)", "any", "defined", False, "Bull call spread plus bear put spread at the same strikes: locks a fixed payoff; only useful when mispriced."),
    "short_box": ("Short box", "none (arbitrage)", "any", "defined", False, "Reverse box: borrows at a fixed cost; only useful when mispriced."),
    "protective_put": ("Future + long put", "bullish, hedged", "low IV", "futures margin", False, "Hold a future and buy a put: upside open, downside floored at the put strike."),
    "covered_call": ("Future + short call", "mildly bullish", "high IV", "futures margin", False, "Hold a future and sell a call: earn the premium, give up the upside above the strike."),
    "collar": ("Collar", "hedged holding", "any", "futures margin", False, "Future plus a long put and a short call: both downside and upside are limited, often for little cost."),
    "risk_reversal": ("Risk reversal", "bullish", "any", "unlimited", False, "Sell an out-of-the-money put to pay for an out-of-the-money call: bullish with risk on a fall."),
    "protective_call": ("Short future + long call", "bearish, hedged", "low IV", "futures margin", False, "Short a future and buy a call: downside open, loss on a rise capped at the call strike."),
    "covered_put": ("Short future + short put", "mildly bearish", "high IV", "futures margin", False, "Short a future and sell a put: earn the premium, give up the gains below the strike."),
}


def shape(name: str, atm: float, a: float, w: float) -> list[tuple]:
    """Template legs (action, kind, strike, ratio). atm = centre strike, a = OTM distance, w = wing width."""
    B, S = "BUY", "SELL"
    t = {
        "long_call": [(B, "CE", atm, 1)], "short_call": [(S, "CE", atm, 1)],
        "long_put": [(B, "PE", atm, 1)], "short_put": [(S, "PE", atm, 1)],
        "bull_call": [(B, "CE", atm, 1), (S, "CE", atm + w, 1)],
        "bear_put": [(B, "PE", atm, 1), (S, "PE", atm - w, 1)],
        "bull_put": [(S, "PE", atm - a, 1), (B, "PE", atm - a - w, 1)],
        "bear_call": [(S, "CE", atm + a, 1), (B, "CE", atm + a + w, 1)],
        "long_straddle": [(B, "CE", atm, 1), (B, "PE", atm, 1)],
        "short_straddle": [(S, "CE", atm, 1), (S, "PE", atm, 1)],
        "long_strangle": [(B, "CE", atm + a, 1), (B, "PE", atm - a, 1)],
        "short_strangle": [(S, "CE", atm + a, 1), (S, "PE", atm - a, 1)],
        "long_butterfly": [(B, "CE", atm - w, 1), (S, "CE", atm, 2), (B, "CE", atm + w, 1)],
        "short_butterfly": [(S, "CE", atm - w, 1), (B, "CE", atm, 2), (S, "CE", atm + w, 1)],
        "long_condor": [(B, "CE", atm - a - w, 1), (S, "CE", atm - a, 1), (S, "CE", atm + a, 1), (B, "CE", atm + a + w, 1)],
        "short_condor": [(S, "CE", atm - a - w, 1), (B, "CE", atm - a, 1), (B, "CE", atm + a, 1), (S, "CE", atm + a + w, 1)],
        "iron_condor": [(S, "PE", atm - a, 1), (B, "PE", atm - a - w, 1), (S, "CE", atm + a, 1), (B, "CE", atm + a + w, 1)],
        "long_box": [(B, "CE", atm, 1), (S, "CE", atm + w, 1), (B, "PE", atm + w, 1), (S, "PE", atm, 1)],
        "short_box": [(S, "CE", atm, 1), (B, "CE", atm + w, 1), (S, "PE", atm + w, 1), (B, "PE", atm, 1)],
        "protective_put": [(B, "FUT", atm, 1), (B, "PE", atm - a, 1)],
        "covered_call": [(B, "FUT", atm, 1), (S, "CE", atm + a, 1)],
        "collar": [(B, "FUT", atm, 1), (B, "PE", atm - a, 1), (S, "CE", atm + a, 1)],
        "risk_reversal": [(S, "PE", atm - a, 1), (B, "CE", atm + a, 1)],
        "protective_call": [(S, "FUT", atm, 1), (B, "CE", atm + a, 1)],
        "covered_put": [(S, "FUT", atm, 1), (S, "PE", atm - a, 1)],
    }
    return t[name]


# ---- payoff maths ---------------------------------------------------------------------------------------
def _leg_value(l: SLeg, s: float) -> float:
    if l.kind == "FUT":
        return l.sign * l.ratio * (s - l.strike)
    intrinsic = max(s - l.strike, 0.0) if l.kind == "CE" else max(l.strike - s, 0.0)
    return l.sign * l.ratio * (intrinsic - l.price)


def payoff(legs, s: float) -> float:
    """Profit or loss per unit at expiry, before charges."""
    return sum(_leg_value(l, s) for l in legs)


def net_premium(legs) -> float:
    """Positive = debit paid, negative = credit received (per unit)."""
    return sum(l.sign * l.ratio * l.price for l in legs if l.kind != "FUT")


def profile(legs) -> dict:
    """Max profit/loss, breakevens and net premium. max_* is None when unbounded."""
    strikes = sorted({l.strike for l in legs})
    far = max(strikes) * 4
    knots = [0.0] + strikes + [far]
    vals = [payoff(legs, k) for k in knots]
    slope = payoff(legs, far + 1) - payoff(legs, far)
    bes = []
    for (x0, y0), (x1, y1) in zip(zip(knots, vals), zip(knots[1:], vals[1:])):
        if y0 == 0 and x0 > 0:
            bes.append(x0)
        elif y0 * y1 < 0:
            bes.append(x0 + (0 - y0) * (x1 - x0) / (y1 - y0))
    bes = sorted({round(b, 2) for b in bes})
    prem = net_premium(legs)
    return {"max_profit": None if slope > 1e-9 else max(vals), "max_loss": None if slope < -1e-9 else max(-min(vals), 0.0),
            "breakevens": bes, "net_premium": prem, "debit": prem > 0}


def expected(legs, spot: float, days: float, vol_pct: float | None = None, rets=None) -> dict:
    """Probability of profit and expected value per unit at expiry.

    `rets` is a sample of return fractions over the holding period (empirical); otherwise a lognormal at `vol_pct`.
    """
    pts: list[tuple[float, float]] = []
    if rets:
        w = 1 / len(rets)
        pts = [(spot * (1 + r), w) for r in rets]
    elif vol_pct and days > 0:
        sig = vol_pct / 100 * math.sqrt(days / 365)
        raw = [(z / 10, math.exp(-((z / 10) ** 2) / 2)) for z in range(-40, 41)]
        tot = sum(w for _, w in raw)
        pts = [(spot * math.exp(-sig * sig / 2 + sig * z), w / tot) for z, w in raw]
    if not pts:
        return {"pop": None, "ev": None}
    return {"pop": sum(w for s, w in pts if payoff(legs, s) > 0), "ev": sum(w * payoff(legs, s) for s, w in pts)}


def costs_per_lot(legs, lot: int, broker: str) -> float:
    """Round-trip charges plus the bid-ask spread for one lot of the whole structure."""
    tot = 0.0
    for l in legs:
        if l.kind == "FUT":
            continue
        q = lot * l.ratio
        p = max(l.price, 0.05)
        tot += -net_pnl("options", p, p, q, broker) + l.slip * q
    return tot


def mark(legs, spot0: float, t0: float, s: float, t: float) -> float:
    """Model P&L per unit if the underlying is at `s` with `t` years left, anchored to today's prices."""
    tot = 0.0
    for l in legs:
        if l.kind == "FUT":
            tot += l.sign * l.ratio * (s - l.strike)
            continue
        k = "C" if l.kind == "CE" else "P"
        iv = l.iv or 15.0
        now = max(l.price + bs_price(k, s, l.strike, t, iv) - bs_price(k, spot0, l.strike, t0, iv), 0.05)
        tot += l.sign * l.ratio * (now - l.price)
    return tot


# ---- ASCII payoff chart ---------------------------------------------------------------------------------
def payoff_chart(legs, spot: float, mult: float = 1.0, width: int = 57, height: int = 13) -> str:
    strikes = [l.strike for l in legs]
    lo, hi = min(strikes + [spot]), max(strikes + [spot])
    pad = max((hi - lo) * 0.45, spot * 0.03)
    lo, hi = lo - pad, hi + pad
    xs = [lo + (hi - lo) * i / (width - 1) for i in range(width)]
    ys = [payoff(legs, x) * mult for x in xs]
    ymin, ymax = min(ys + [0.0]), max(ys + [0.0])
    if ymax == ymin:
        ymax = ymin + 1
    row = lambda y: round((ymax - y) / (ymax - ymin) * (height - 1))  # noqa: E731
    grid = [[" "] * width for _ in range(height)]
    zero = row(0.0)
    for c in range(width):
        grid[zero][c] = "-"
    sc = min(range(width), key=lambda i: abs(xs[i] - spot))
    for r in range(height):
        if grid[r][sc] == " ":
            grid[r][sc] = "|"
    prev = None
    for c, y in enumerate(ys):
        r = row(y)
        grid[r][c] = "*"
        if prev is not None:
            for rr in range(min(prev, r) + 1, max(prev, r)):
                grid[rr][c if abs(rr - r) <= abs(rr - prev) else c - 1] = "*"
        prev = r
    out = []
    for r in range(height):
        label = ""
        if r == 0:
            label = f"{ymax:>9,.0f} "
        elif r == height - 1:
            label = f"{ymin:>9,.0f} "
        elif r == zero:
            label = f"{0:>9,.0f} "
        out.append(label.rjust(10) + "".join(grid[r]))
    out.append(" " * 10 + f"{lo:<{width // 2},.0f}{hi:>{width - width // 2},.0f}")
    out.append(" " * 10 + f"(| = spot {spot:,.0f}; amounts in Rs per structure)")
    return "\n".join(out)


# ---- shared helpers -------------------------------------------------------------------------------------
def liquid(leg, is_index: bool) -> bool:
    if not leg or leg.ltp <= 0 or leg.iv <= 0:
        return False
    if leg.volume < (500 if is_index else 50):
        return False
    return not (leg.bid > 0 and leg.ask > 0 and (leg.ask - leg.bid) / leg.ask > 0.04)


def size_lots(loss_lot: float, max_loss_lot: float, outlay_lot: float, capital: float, risk_pct: float) -> int:
    budget = capital * risk_pct / 100
    if loss_lot <= 0:
        return 0
    return int(min(budget // loss_lot, 3 * budget // max(max_loss_lot, 1e-9), 0.35 * capital // max(outlay_lot, 1e-9)))


def _price(leg) -> tuple[float, float]:
    """(mid or last price, round-trip spread)."""
    if leg.bid > 0 and leg.ask > 0:
        return (leg.bid + leg.ask) / 2, leg.ask - leg.bid
    return leg.ltp, 0.0


def from_chain(name: str, ch, atm: float, a: float, w: float, is_index: bool, fut_price: float | None = None):
    """Resolve a template against the live chain. Returns (legs, reason); legs is None if any leg is missing or illiquid."""
    by = {k: (c, p) for k, c, p in ch.rows}
    legs = []
    for action, kind, k, ratio in shape(name, atm, a, w):
        if kind == "FUT":
            if fut_price is None:
                return None, "no futures price"
            legs.append(SLeg(action, kind, fut_price, 0.0, 0.0, ratio))
            continue
        leg = by.get(k, (None, None))[0 if kind == "CE" else 1]
        if not liquid(leg, is_index):
            return None, f"the {k:,.0f} {kind} is missing or too illiquid"
        price, slip = _price(leg)
        legs.append(SLeg(action, kind, k, price, leg.iv, ratio, slip))
    return legs, ""


def to_plan_legs(legs) -> list[dict]:
    return [{"action": l.action, "kind": l.kind, "strike": l.strike, "ltp": round(l.price, 2), "iv": l.iv, "ratio": l.ratio}
            for l in legs]


def enrich_plan(plan: dict, spot: float, dte: float, atm_iv: float | None, rets, rv_ann: float | None,
                lot: int, broker: str) -> None:
    """Add payoff statistics and a chart to any plan, directional or not. Sets plan['stats'] and plan['chart']."""
    legs = [SLeg(g["action"], g["kind"], g["strike"], g["ltp"], g.get("iv", 0.0), g.get("ratio", 1))
            for g in plan["legs"]]
    prof = profile(legs)
    mkt = expected(legs, spot, dte, atm_iv) if atm_iv else {"pop": None, "ev": None}
    real = expected(legs, spot, dte, rv_ann, None if not rets else rets) if (rets or rv_ann) else {"pop": None, "ev": None}
    q = plan.get("qty") or lot
    fee = costs_per_lot(legs, lot, broker) * (plan.get("lots") or 1)
    plan["stats"] = {
        "breakevens": prof["breakevens"], "net_premium": round(prof["net_premium"], 2), "debit": prof["debit"],
        "max_profit_rs": None if prof["max_profit"] is None else round(prof["max_profit"] * q),
        "max_loss_rs": None if prof["max_loss"] is None else round(prof["max_loss"] * q),
        "pop_market": mkt["pop"], "pop_history": real["pop"],
        "ev_rs": None if real["ev"] is None else round(real["ev"] * q - fee),
        "ev_basis": "history" if rets else "realised volatility" if rv_ann else None, "charges_rs": round(fee),
    }
    plan["chart"] = payoff_chart(legs, spot, q)


# ---- non-directional planner ----------------------------------------------------------------------------
def _screen(name: str, status: str, why: str) -> dict:
    return {"name": CATALOGUE[name][0], "status": status, "why": why}


def not_suggested(names) -> list[dict]:
    return [_screen(n, "not suggested", "unlimited risk: this desk never recommends it" if CATALOGUE[n][3] == "unlimited"
                    else "needs futures margin or is an arbitrage tool") for n in names]


def plan_nondirectional(mode: str, ch, m: dict, lot: int | None, atr: float | None, capital: float, risk_pct: float,
                        broker: str, is_index: bool, rets=None, rv_ann: float | None = None, squeeze: bool = False,
                        event_level: int = 0, now_ok: bool = True) -> tuple[dict | None, list[dict]]:
    """Range (sell premium with defined risk) or breakout (buy premium) structure, with a screen of what was considered."""
    spot, step, dte = ch.spot, m["step"], m["dte"]
    screen: list[dict] = []
    if not lot or not step or not m.get("atm_iv") or not m.get("straddle"):
        return None, [{"name": "all", "status": "rejected", "why": "lot size, strike step or implied volatility unavailable"}]
    rv = rv_ann or ((m.get("realised_daily_pct") or 0) * math.sqrt(252) or None)
    iv_rv = m["atm_iv"] / rv if rv else None
    em = m["straddle"]  # market-implied move to expiry in points
    t0 = dte / 365
    t1 = max(t0 - 1 / 365, 1 / 365)
    atm = m["atm"]
    names = ["iron_condor", "long_butterfly"] if mode == "range" else ["long_straddle", "long_strangle"]
    cands: list[tuple[dict, dict]] = []

    def gate(why: str) -> None:
        screen.extend(_screen(n, "rejected", why) for n in names)

    if dte < 2:
        gate(f"only {dte:.1f} days to expiry")
        return None, screen
    if mode == "range":
        if event_level >= 2:
            gate("a major event is due today or tomorrow: selling premium into a possible jump")
            return None, screen + not_suggested(["short_straddle", "short_strangle"])
        if iv_rv is None or iv_rv < 1.1:
            gate(f"premium is not rich enough (implied/realised volatility {iv_rv:.2f}, need 1.10)" if iv_rv else "realised volatility unavailable")
            return None, screen + not_suggested(["short_straddle", "short_strangle"])
        if squeeze:
            gate("the chart is in a volatility squeeze: a larger move is building")
            return None, screen + not_suggested(["short_straddle", "short_strangle"])
    else:
        ok_iv = iv_rv is not None and (iv_rv <= 1.0 or (squeeze and iv_rv <= 1.15))
        if not (squeeze or event_level >= 1):
            gate("no squeeze and no scheduled event: no reason to expect a breakout")
            return None, screen
        if not ok_iv:
            gate(f"options are not cheap enough to buy (implied/realised volatility {iv_rv:.2f})" if iv_rv else "realised volatility unavailable")
            return None, screen

    def cand(name: str, a: float, w: float) -> None:
        legs, why = from_chain(name, ch, atm, a, w, is_index)
        if not legs:
            screen.append(_screen(name, "rejected", why))
            return
        prof = profile(legs)
        width = w
        net = prof["net_premium"]
        fee = costs_per_lot(legs, lot, broker)
        sample = rets
        ex = expected(legs, spot, dte, None if sample else (rv or 0.9 * m["atm_iv"]), sample)
        ev_lot = (ex["ev"] or 0) * lot - fee
        if ev_lot <= 0:
            screen.append(_screen(name, "rejected", f"expected value after charges is negative (₹{ev_lot:,.0f} per lot)"))
            return
        if mode == "range":
            credit = -net
            if name == "iron_condor":
                if credit < 0.15 * width or ex["pop"] is None or ex["pop"] < 0.6 or credit >= width:
                    screen.append(_screen(name, "rejected", f"credit ₹{credit:.1f} on ₹{width:.0f} wings, probability {(ex['pop'] or 0):.0%}: too thin"))
                    return
                max_loss_lot = (width - credit) * lot + fee
                lo, hi = atm - a, atm + a
                loss_stop_lot = max(-mark(legs, spot, t0, lo, t1), -mark(legs, spot, t0, hi, t1)) * lot + fee
                win_lot = 0.5 * credit * lot - fee
                rr = win_lot / loss_stop_lot if loss_stop_lot > 0 else 0
                if rr < 0.4:
                    screen.append(_screen(name, "rejected", f"reward to risk {rr:.2f} at the stop is below 0.4"))
                    return
                lots = size_lots(loss_stop_lot, max_loss_lot, max_loss_lot, capital, risk_pct)
                if lots < 1:
                    screen.append(_screen(name, "rejected", f"1 lot can lose ₹{max_loss_lot:,.0f}, beyond your risk budget"))
                    return
                lv = {"mode": "range", "lower": lo, "upper": hi, "stop_why": "close if the underlying touches either short strike",
                      "target_why": "close at 50% of the credit, or at 1 day to expiry"}
                plan = {"structure": "Iron condor (credit)", "net_credit": round(credit, 2), "outlay": 0,
                        "max_loss": round(max_loss_lot * lots), "risk_rs": round(loss_stop_lot * lots),
                        "reward_rs": round(win_lot * lots), "rr_net": round(rr, 2),
                        "reason": "options are rich versus realised movement and the chart is not coiling, so the premium is sold with both sides capped"}
            else:
                cost = net
                if cost <= 0 or cost > 0.4 * width or dte > 6 or (ex["pop"] or 0) < 0.3:
                    screen.append(_screen(name, "rejected", f"cost ₹{cost:.1f} for ₹{width:.0f} wings, probability {(ex['pop'] or 0):.0%}, {dte:.0f} days left: not attractive"))
                    return
                loss_stop_lot = cost * lot + fee
                win_lot = (width - cost) * lot * 0.6 - fee
                rr = win_lot / loss_stop_lot
                if rr < 1.0:
                    screen.append(_screen(name, "rejected", f"reward to risk {rr:.2f} is below 1.0"))
                    return
                lots = size_lots(loss_stop_lot, cost * lot, cost * lot, capital, risk_pct)
                if lots < 1:
                    screen.append(_screen(name, "rejected", f"1 lot costs ₹{cost * lot:,.0f}, beyond your risk budget"))
                    return
                lv = {"mode": "range", "lower": atm - width, "upper": atm + width, "stop_why": "close if the underlying leaves the wings",
                      "target_why": "close when the underlying is near the middle strike with little time left"}
                plan = {"structure": "Long butterfly (debit)", "net_debit": round(cost, 2), "outlay": round(cost * lot * lots),
                        "max_loss": round(cost * lot * lots), "risk_rs": round(loss_stop_lot * lots),
                        "reward_rs": round(win_lot * lots), "rr_net": round(rr, 2),
                        "reason": "expiry is close and premium is rich, so a cheap butterfly centred near spot pays if the market pins"}
        else:
            debit = net
            tgt_up, tgt_dn = spot + 1.5 * (atr or em / 2), spot - 1.5 * (atr or em / 2)
            gain = max(mark(legs, spot, t0, tgt_up, t1), mark(legs, spot, t0, tgt_dn, t1)) * lot - fee
            loss_stop_lot = 0.35 * debit * lot + fee
            rr = gain / loss_stop_lot if loss_stop_lot > 0 else 0
            if debit <= 0 or rr < 1.5 or (ex["pop"] or 0) < 0.3:
                screen.append(_screen(name, "rejected", f"reward to risk {rr:.2f} (need 1.5), probability {(ex['pop'] or 0):.0%} (need 30%)"))
                return
            lots = size_lots(loss_stop_lot, debit * lot, debit * lot, capital, risk_pct)
            if lots < 1:
                screen.append(_screen(name, "rejected", f"1 lot costs ₹{debit * lot:,.0f}, beyond your risk budget"))
                return
            lv = {"mode": "breakout", "lower": tgt_dn, "upper": tgt_up,
                  "stop_why": "exit if the premium falls 35%, or by 15:15 if nothing has happened",
                  "target_why": "exit on a 1.5 x ATR move either way"}
            plan = {"structure": "Long straddle (debit)" if name == "long_straddle" else "Long strangle (debit)",
                    "net_debit": round(debit, 2), "outlay": round(debit * lot * lots), "max_loss": round(debit * lot * lots),
                    "risk_rs": round(loss_stop_lot * lots), "reward_rs": round(gain * lots), "rr_net": round(rr, 2),
                    "reason": "the chart is coiling or an event is near while options are cheap, so buying both sides pays on a big move in either direction"}
        plan.update({"legs": to_plan_legs(legs), "lots": lots, "qty": lots * lot, "levels": lv,
                     "breakeven": None, "pop": round(ex["pop"], 2) if ex["pop"] is not None else None,
                     "premium_stop": None, "premium_target": None, "kind": mode,
                     "ev_rs": round(ev_lot * lots)})
        cands.append((plan, {"name": name, "ev": ev_lot * lots, "risk": max(plan["risk_rs"], 1)}))

    n_otm = max(1, round(0.35 * em / step))
    if mode == "range":
        wall_up, wall_dn = m.get("call_wall"), m.get("put_wall")
        far_up = (wall_up - atm) if wall_up and wall_up - spot >= 0.6 * em else em
        far_dn = (atm - wall_dn) if wall_dn and spot - wall_dn >= 0.6 * em else em
        a = max(step * 2, math.ceil(min(far_up, far_dn) / step) * step)
        cand("iron_condor", a, step * 2)
        centre = m["max_pain"] if m.get("max_pain") and abs(m["max_pain"] - spot) <= 0.5 * em else atm
        cand("long_butterfly", 0, max(step, math.ceil(0.5 * em / step) * step))
        if centre != atm:
            screen.append({"name": "Long butterfly centre", "status": "note", "why": f"max pain {centre:,.0f} is the pin candidate"})
    else:
        cand("long_straddle", 0, 0)
        cand("long_strangle", n_otm * step, 0)

    if not cands:
        return None, screen + not_suggested(["short_straddle", "short_strangle"] if mode == "range" else [])
    cands.sort(key=lambda c: -c[1]["ev"] / c[1]["risk"])
    best, info = cands[0]
    for plan, i in cands:
        screen.append(_screen(i["name"], "selected" if plan is best else "rejected",
                              "highest expected value per rupee at risk" if plan is best else "a better candidate was available"))
    best["iv_rv"] = round(iv_rv, 2) if iv_rv else None
    enrich_plan(best, spot, dte, m["atm_iv"], rets, rv, lot, broker)
    best["ev_rs"] = best["stats"]["ev_rs"] if best["stats"]["ev_rs"] is not None else best["ev_rs"]
    return best, screen + not_suggested(["short_straddle", "short_strangle"] if mode == "range" else [])


# ---- reference guide ------------------------------------------------------------------------------------
def guide(name: str, spot: float = 25000.0, step: float = 50.0, iv: float = 15.0, dte: float = 7.0, lot: int = 75) -> str:
    """Plain-text lesson for one strategy using a theoretical example priced with Black-Scholes."""
    title, view, iv_pref, risk, auto, note = CATALOGUE[name]
    atm = round(spot / step) * step
    legs = []
    for action, kind, k, ratio in shape(name, atm, 2 * step, 2 * step):
        price = 0.0 if kind == "FUT" else bs_price("C" if kind == "CE" else "P", spot, k, dte / 365, iv)
        legs.append(SLeg(action, kind, k if kind != "FUT" else spot, round(price, 2), iv, ratio))
    prof = profile(legs)
    lines = [f"{title}", f"  View: {view}. Best when implied volatility is: {iv_pref}. Risk: {risk}.", f"  {note}", "",
             f"  Example (spot {spot:,.0f}, {dte:.0f} days, IV {iv:.0f}%, lot {lot}):"]
    for l in legs:
        lines.append(f"    {l.action:<4} {l.ratio}x {l.strike:,.0f} {l.kind}" + (f" @ {l.price:,.2f}" if l.kind != "FUT" else f" @ {l.strike:,.0f}"))
    nm = prof["net_premium"]
    lines.append(f"  Net {'debit' if nm > 0 else 'credit'}: {abs(nm):,.2f} per unit (₹{abs(nm) * lot:,.0f} per lot)")
    mp, ml = prof["max_profit"], prof["max_loss"]
    lines.append("  Max profit: " + ("unlimited" if mp is None else f"₹{mp * lot:,.0f}") + " | Max loss: " + ("unlimited" if ml is None else f"₹{ml * lot:,.0f}"))
    lines.append("  Breakeven at expiry: " + (", ".join(f"{b:,.0f}" for b in prof["breakevens"]) or "none"))
    lines.append("  Desk policy: " + ("can be suggested (defined risk)." if auto else "reference only, never suggested automatically."))
    lines += ["", payoff_chart(legs, spot, lot)]
    return "\n".join(lines)


def catalogue_text() -> str:
    rows = [f"{k:<16} {v[0]:<22} {v[1]:<28} IV: {v[2]:<8} risk: {v[3]}" for k, v in CATALOGUE.items()]
    return "\n".join(rows) + "\n\nUse: python -m nse_agent strategies <name> for a worked example and payoff chart."
