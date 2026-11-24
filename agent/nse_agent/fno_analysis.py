"""F&O analysis: option-chain metrics, evidence nodes (a small knowledge graph), a verdict, and a defined-risk trade.

Pure functions over fetched data, so everything here is testable offline. The verdict is a weighted vote of
independent evidence nodes. A trade is issued only when the nodes agree, nothing strong disagrees, the
structure fits the user's risk budget, and reward to risk clears the bar after charges.
Naked option selling is never suggested: bearish and bullish views use long options or defined-risk spreads.
"""
import math
from dataclasses import dataclass, field
from datetime import datetime

from .analysis import net_pnl
from .clock import IST
from .fno_data import Chain, Future, Leg

RATE = 0.065
HOLD_DAYS = 1.0
MIN_DTE = 2.0


# ---- Black-Scholes -------------------------------------------------------------------------------------
def _cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _pdf(x: float) -> float:
    return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def bs_price(kind: str, s: float, k: float, t: float, iv_pct: float, r: float = RATE) -> float:
    sig = iv_pct / 100
    if t <= 0 or sig <= 0:
        return max(s - k, 0.0) if kind == "C" else max(k - s, 0.0)
    d1 = (math.log(s / k) + (r + sig * sig / 2) * t) / (sig * math.sqrt(t))
    d2 = d1 - sig * math.sqrt(t)
    if kind == "C":
        return s * _cdf(d1) - k * math.exp(-r * t) * _cdf(d2)
    return k * math.exp(-r * t) * _cdf(-d2) - s * _cdf(-d1)


def greeks(kind: str, s: float, k: float, t: float, iv_pct: float, r: float = RATE) -> dict | None:
    sig = iv_pct / 100
    if t <= 0 or sig <= 0:
        return None
    d1 = (math.log(s / k) + (r + sig * sig / 2) * t) / (sig * math.sqrt(t))
    d2 = d1 - sig * math.sqrt(t)
    delta = _cdf(d1) if kind == "C" else _cdf(d1) - 1
    decay = -(s * _pdf(d1) * sig) / (2 * math.sqrt(t))
    carry = -r * k * math.exp(-r * t) * (_cdf(d2) if kind == "C" else -_cdf(-d2))
    return {"delta": delta, "gamma": _pdf(d1) / (s * sig * math.sqrt(t)),
            "theta_day": (decay + carry) / 365, "vega_pt": s * _pdf(d1) * math.sqrt(t) / 100}


def reprice(kind: str, k: float, leg: Leg, s0: float, t0: float, s1: float, t1: float) -> float:
    """Market price now, moved to a new spot and time by the Black-Scholes difference at the leg's own IV."""
    return max(leg.ltp + bs_price(kind, s1, k, t1, leg.iv) - bs_price(kind, s0, k, t0, leg.iv), 0.05)


# ---- data classes ---------------------------------------------------------------------------------------
@dataclass
class Ctx:
    symbol: str
    is_index: bool
    day_pct: float
    sma50: float | None = None
    atr: float | None = None  # daily average true range in price units
    tone: str | None = None
    vix: float | None = None
    vix_pct: float | None = None
    outperf: float | None = None
    negative_news: tuple = ()
    lot: int | None = None
    capital: float = 500000.0
    risk_pct: float = 1.0
    broker: str = "zerodha"
    prev_pcr: float | None = None
    spurt: dict | None = None


@dataclass
class Node:
    id: str
    label: str
    value: str
    score: float = 0.0  # -2 bearish .. +2 bullish
    weight: float = 0.0
    why: str = ""
    kind: str = "direction"  # direction | context | level


@dataclass
class View:
    symbol: str
    spot: float
    expiry: str
    dte: float
    ts: datetime | None
    direction: str  # bullish | bearish | neutral | conflicted
    score: float
    confidence: str
    nodes: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    plan: dict | None = None
    futures_alt: dict | None = None
    notes: list = field(default_factory=list)  # why no trade, caveats
    stale: bool = False
    lot: int | None = None
    source: str = ""

    def graph(self) -> dict:
        return build_graph(self)


# ---- chain metrics --------------------------------------------------------------------------------------
def _step(strikes: list[float], spot: float) -> float:
    near = sorted(sorted(strikes, key=lambda k: abs(k - spot))[:8])
    diffs = [b - a for a, b in zip(near, near[1:]) if b > a]
    return min(diffs) if diffs else 0.0


def metrics(ch: Chain, now: datetime, atr: float | None = None) -> dict:
    spot = ch.spot
    strikes = [k for k, _, _ in ch.rows]
    step = _step(strikes, spot)
    atm = min(strikes, key=lambda k: abs(k - spot))
    by = {k: (c, p) for k, c, p in ch.rows}
    ce_tot = sum(c.oi for _, c, _ in ch.rows if c)
    pe_tot = sum(p.oi for _, _, p in ch.rows if p)
    ce_vol = sum(c.volume for _, c, _ in ch.rows if c)
    pe_vol = sum(p.volume for _, _, p in ch.rows if p)
    near = [(k, c, p) for k, c, p in ch.rows if abs(k - spot) <= 0.12 * spot]
    calls = [(c.oi, k) for k, c, _ in near if c and k > spot]
    puts = [(p.oi, k) for k, _, p in near if p and k < spot]
    call_wall = max(calls)[1] if calls and max(calls)[0] > 0 else None
    put_wall = max(puts)[1] if puts and max(puts)[0] > 0 else None

    def pain(x):
        return sum(max(x - k, 0) * c.oi for k, c, _ in ch.rows if c) + sum(max(k - x, 0) * p.oi for k, _, p in ch.rows if p)
    max_pain = min(strikes, key=pain) if ce_tot + pe_tot > 0 else None

    win = [(k, c, p) for k, c, p in ch.rows if step and abs(k - atm) <= 6 * step]
    doi_ce = sum(c.doi for _, c, _ in win if c)
    doi_pe = sum(p.doi for _, _, p in win if p)
    flow_den = abs(doi_ce) + abs(doi_pe)

    ce, pe = by[atm]
    ivs = [x.iv for x in (ce, pe) if x and x.iv > 0]
    atm_iv = sum(ivs) / len(ivs) if ivs else None
    sk_p = by.get(atm - 3 * step, (None, None))[1] if step else None
    sk_c = by.get(atm + 3 * step, (None, None))[0] if step else None
    skew = (sk_p.iv - sk_c.iv) if sk_p and sk_c and sk_p.iv > 0 and sk_c.iv > 0 else None
    straddle = (ce.ltp if ce else 0) + (pe.ltp if pe else 0)
    expiry_dt = datetime(ch.expiry.year, ch.expiry.month, ch.expiry.day, 15, 30, tzinfo=IST)
    dte = max((expiry_dt - now).total_seconds() / 86400, 0.0)
    iv_daily = atm_iv / math.sqrt(252) if atm_iv else None
    realised = atr / spot * 100 if atr and spot else None
    return {
        "atm": atm, "step": step, "pcr_oi": pe_tot / ce_tot if ce_tot else None,
        "pcr_vol": pe_vol / ce_vol if ce_vol else None, "ce_oi": ce_tot, "pe_oi": pe_tot,
        "call_wall": call_wall, "put_wall": put_wall,
        "call_wall_oi": by[call_wall][0].oi if call_wall and by[call_wall][0] else 0,
        "put_wall_oi": by[put_wall][1].oi if put_wall and by[put_wall][1] else 0,
        "max_pain": max_pain, "doi_ce": doi_ce, "doi_pe": doi_pe,
        "flow": (doi_pe - doi_ce) / flow_den if flow_den else 0.0, "flow_size": flow_den / (ce_tot + pe_tot or 1),
        "atm_iv": atm_iv, "skew": skew, "straddle": straddle,
        "implied_move_pct": straddle / spot * 100 if spot else None, "dte": dte,
        "iv_daily_pct": iv_daily, "realised_daily_pct": realised,
        "iv_ratio": iv_daily / realised if iv_daily and realised else None,
    }


def classify_buildup(price_pct: float, oi_pct: float, is_index: bool) -> str:
    p_min = 0.15 if is_index else 0.4
    if abs(price_pct) < p_min or abs(oi_pct) < 1.0:
        return "flat"
    if price_pct > 0:
        return "long buildup" if oi_pct > 0 else "short covering"
    return "short buildup" if oi_pct > 0 else "long unwinding"


_BUILDUP_SCORE = {"long buildup": 2.0, "short covering": 1.0, "flat": 0.0, "long unwinding": -1.0,
                  "short buildup": -2.0}


def _clamp(x: float, lo: float = -2.0, hi: float = 2.0) -> float:
    return max(lo, min(hi, x))


# ---- evidence nodes -------------------------------------------------------------------------------------
def build_nodes(ch: Chain, m: dict, fut: Future | None, ctx: Ctx, now: datetime) -> list[Node]:
    spot, nodes = ch.spot, []
    scale = 0.4 if ctx.is_index else 1.0
    s = _clamp(ctx.day_pct / scale)
    above = None
    if ctx.sma50:
        above = spot > ctx.sma50
        s = _clamp(s + (0.5 if above else -0.5))
    nodes.append(Node("trend", "Price trend", f"{ctx.day_pct:+.2f}% today" + (
        "" if above is None else f", {'above' if above else 'below'} 50-day SMA {ctx.sma50:,.0f}"), s, 1.5,
        "Direction of today's move and its position against the 50-day average"))

    if fut and ctx.spurt:
        kind = classify_buildup(fut.pct, ctx.spurt["pct"], ctx.is_index)
        nodes.append(Node("buildup", "Futures OI buildup", f"{kind} (price {fut.pct:+.2f}%, OI {ctx.spurt['pct']:+.1f}%)",
                          _BUILDUP_SCORE[kind], 1.5,
                          "Price and open interest together show whether new money or exits drive the move"))
        m["buildup"] = kind
    if fut:
        days = max((fut.expiry - now.date()).days, 1)
        ann = (fut.ltp / spot - 1) * 365 / days * 100 if spot else 0
        m["basis_ann"] = ann
        sc = 1.0 if ann >= 10 else -1.0 if ann <= 3 else 0.0
        nodes.append(Node("basis", "Futures premium", f"{ann:.1f}% annualised ({fut.ltp - spot:+,.0f} pts)", sc, 0.5,
                          "Rich premium shows leveraged long demand; a thin or negative one shows hedging or shorts"))

    if m["pcr_oi"] is not None:
        p = m["pcr_oi"]
        sc = -2.0 if p < 0.6 else -1.0 if p < 0.8 else 0.0 if p < 1.1 else 1.0 if p < 1.4 else 0.5
        trend = ""
        if ctx.prev_pcr:
            d = p - ctx.prev_pcr
            trend = f", {d:+.2f} since the first read today"
            sc = _clamp(sc + (0.5 if d > 0.05 else -0.5 if d < -0.05 else 0))
        nodes.append(Node("pcr", "Put-call ratio (OI)", f"{p:.2f}{trend}", sc, 1.0,
                          "More put than call open interest means writers are defending support (very high is crowded)"))

    cw, pw = m["call_wall"], m["put_wall"]
    if cw and pw:
        dc, dp = cw - spot, spot - pw
        nodes.append(Node("walls", "Position between OI walls", f"support {pw:,.0f} ({dp:,.0f} pts below), "
                          f"resistance {cw:,.0f} ({dc:,.0f} pts above)", _clamp((dc - dp) / (dc + dp) * 2), 1.0,
                          "Price near the put wall has room to rise; near the call wall it has room to fall"))
    if m["flow_size"] > 0:
        size = 1.0 if m["flow_size"] >= 0.02 else 0.5
        side = "put writing" if m["flow"] > 0.15 else "call writing" if m["flow"] < -0.15 else "balanced"
        nodes.append(Node("flow", "OI change near the money", f"{side} (calls {m['doi_ce']:+,.0f}, puts {m['doi_pe']:+,.0f})",
                          _clamp(m["flow"] * 2 * size), 1.5,
                          "Fresh put open interest near spot reads as support; fresh call OI reads as a cap"))
    if m["max_pain"] and m["dte"] <= 3:
        sc = _clamp((m["max_pain"] - spot) / spot * 100 / 0.4, -1, 1)
        nodes.append(Node("maxpain", "Max pain pull", f"{m['max_pain']:,.0f} vs spot {spot:,.0f}", sc,
                          0.5 if m["dte"] <= 2 else 0.25, "Into expiry, price tends to drift toward the strike where option buyers lose most"))
    if ctx.tone:
        nodes.append(Node("tone", "Market tone", ctx.tone, {"trending up": 1.0, "choppy": 0.0, "risk-off": -1.5}.get(ctx.tone, 0.0),
                          1.0, "Broad NIFTY, VIX and breadth backdrop from the cash-market scan"))
    if ctx.outperf is not None and not ctx.is_index:
        nodes.append(Node("rs", "Relative strength", f"{ctx.outperf:+.1f} pts vs NIFTY (3m)", _clamp(ctx.outperf / 5), 0.5,
                          "Stocks that lead the index tend to keep leading"))
    if ctx.negative_news:
        nodes.append(Node("news", "News flow", ctx.negative_news[0][:80], -2.0, 1.0, "Negative headline on this underlying"))

    ivtxt = f"ATM IV {m['atm_iv']:.1f}%" if m["atm_iv"] else "ATM IV n/a"
    if m["iv_ratio"]:
        tag = "cheap" if m["iv_ratio"] <= 1.0 else "rich" if m["iv_ratio"] >= 1.35 else "fair"
        ivtxt += f"; implied daily move {m['iv_daily_pct']:.2f}% vs realised {m['realised_daily_pct']:.2f}% ({tag})"
    nodes.append(Node("iv", "Option pricing", ivtxt, 0, 0, "Decides whether to buy options or use spreads", "context"))
    if ctx.vix is not None:
        nodes.append(Node("vix", "India VIX", f"{ctx.vix:.1f}" + (f" ({ctx.vix_pct:+.1f}% today)" if ctx.vix_pct is not None else ""),
                          0, 0, "Market-wide fear gauge", "context"))
    if m["skew"] is not None:
        nodes.append(Node("skew", "Put-call IV skew", f"{m['skew']:+.1f} vol pts (3 strikes out)", 0, 0,
                          "Rich puts mean the market is paying up for protection", "context"))
    nodes.append(Node("dte", "Days to expiry", f"{m['dte']:.1f}", 0, 0, "Theta and gamma risk rise sharply in the last 2 days", "context"))
    return nodes


# ---- verdict --------------------------------------------------------------------------------------------
def decide(nodes: list[Node], ctx: Ctx) -> tuple[str, float, str, list[str]]:
    d = [n for n in nodes if n.kind == "direction"]
    wsum = sum(n.weight for n in d)
    if not d or wsum == 0:
        return "neutral", 0.0, "none", ["no directional evidence"]
    score = sum(n.weight * n.score for n in d) / wsum * 50
    sign = 1 if score > 0 else -1
    active = [n for n in d if abs(n.score) >= 0.5]
    agree = [n for n in active if n.score * sign > 0]
    share = len(agree) / len(active) if active else 0
    opposed = [n for n in active if n.score * sign <= -1.5]
    notes = []
    if abs(score) < 30:
        return "neutral", score, "none", ["evidence is mixed: no edge"]
    if share < 0.65 or len(agree) < 3:
        return "conflicted", score, "none", [f"only {len(agree)} of {len(active)} signals with a clear reading agree (need 3 and 65%)"]
    if opposed:
        return "conflicted", score, "none", ["strong disagreement: " + ", ".join(n.label for n in opposed)]
    if sign > 0 and ctx.negative_news:
        return "conflicted", score, "none", ["negative news on a bullish setup"]
    conf = "high" if abs(score) >= 55 and share >= 0.8 and len(agree) >= 5 else "medium"
    return ("bullish" if sign > 0 else "bearish"), score, conf, notes


# ---- trade structuring ----------------------------------------------------------------------------------
def _liquid(leg: Leg | None, is_index: bool) -> bool:
    if not leg or leg.ltp <= 0 or leg.iv <= 0:
        return False
    if leg.volume < (500 if is_index else 50):
        return False
    return not (leg.bid > 0 and leg.ask > 0 and (leg.ask - leg.bid) / leg.ask > 0.04)


def _levels(direction: str, spot: float, m: dict, a: float) -> tuple[dict | None, str | None]:
    sg = 1 if direction == "bullish" else -1
    adverse, favour = (m["put_wall"], m["call_wall"]) if sg > 0 else (m["call_wall"], m["put_wall"])
    adv = sg * (spot - adverse) if adverse else None
    fav = sg * (favour - spot) if favour else None
    stop_d, tgt_d = 0.8 * a, 1.8 * a
    stop_why, tgt_why = "0.8 x daily ATR", "1.8 x daily ATR"
    if adv is not None and 0.4 * a <= adv <= 1.5 * a:
        stop_d, stop_why = adv + 0.1 * a, f"just beyond the {'put' if sg > 0 else 'call'} OI wall {adverse:,.0f}"
    if fav is not None:
        if fav < 0.8 * a:
            return None, f"the {'call' if sg > 0 else 'put'} OI wall at {favour:,.0f} is only {fav:,.0f} pts away: no room to run"
        if fav <= 2.5 * a:
            tgt_d, tgt_why = max(0.8 * a, fav - 0.05 * a), f"just before the {'call' if sg > 0 else 'put'} OI wall {favour:,.0f}"
    trig_d = 0.1 * a
    if (tgt_d - trig_d) / (trig_d + stop_d) < 1.5:
        return None, "underlying reward to risk is below 1.5 with the nearest OI wall as stop"
    return {"sg": sg, "trigger": spot + sg * trig_d, "stop": spot - sg * stop_d, "target": spot + sg * tgt_d,
            "chase": spot + sg * 0.35 * a, "stop_why": stop_why, "target_why": tgt_why}, None


def _size(loss_lot: float, max_loss_lot: float, outlay_lot: float, ctx: Ctx) -> int:
    budget = ctx.capital * ctx.risk_pct / 100
    if loss_lot <= 0:
        return 0
    return int(min(budget // loss_lot, 3 * budget // max(max_loss_lot, 1e-9), 0.35 * ctx.capital // max(outlay_lot, 1e-9)))


def _plan_long(sg, ch, m, ctx, lv, lot, t0, t1) -> tuple[dict | None, str]:
    kind = "C" if sg > 0 else "P"
    by = {k: (c, p) for k, c, p in ch.rows}
    best = None
    for k in (m["atm"] - m["step"], m["atm"], m["atm"] + m["step"]):
        leg = by.get(k, (None, None))[0 if kind == "C" else 1]
        if not _liquid(leg, ctx.is_index):
            continue
        g = greeks(kind, ch.spot, k, t0, leg.iv)
        if g and (best is None or abs(abs(g["delta"]) - 0.55) < abs(abs(best[2]["delta"]) - 0.55)):
            best = (k, leg, g)
    if not best:
        return None, "no liquid at-the-money option to buy"
    k, leg, g = best
    p_in = reprice(kind, k, leg, ch.spot, t0, lv["trigger"], t0)
    p_stop = reprice(kind, k, leg, ch.spot, t0, lv["stop"], t1)
    p_tgt = reprice(kind, k, leg, ch.spot, t0, lv["target"], t1)
    loss_lot = -net_pnl("options", p_in, p_stop, lot, ctx.broker)
    win_lot = net_pnl("options", p_in, p_tgt, lot, ctx.broker)
    lots = _size(loss_lot, p_in * lot, p_in * lot, ctx)
    if lots < 1:
        return None, (f"1 lot of the {k:,.0f} {'CE' if kind == 'C' else 'PE'} costs ₹{p_in * lot:,.0f} and loses ₹{loss_lot:,.0f} at the stop, "
                      f"beyond your ₹{ctx.capital * ctx.risk_pct / 100:,.0f} risk budget or the 3x gap cap")
    if win_lot <= 0 or win_lot / loss_lot < 1.5:
        return None, f"long option reward to risk after charges is {win_lot / loss_lot if loss_lot else 0:.2f}, below 1.5"
    return {"structure": "Long call" if kind == "C" else "Long put",
            "legs": [{"action": "BUY", "kind": "CE" if kind == "C" else "PE", "strike": k, "ltp": leg.ltp,
                      "entry_est": round(p_in, 2), "iv": leg.iv, "delta": round(g["delta"], 2)}],
            "lots": lots, "qty": lots * lot, "outlay": round(p_in * lot * lots), "max_loss": round(p_in * lot * lots),
            "risk_rs": round(loss_lot * lots), "reward_rs": round(win_lot * lots), "rr_net": round(win_lot / loss_lot, 2),
            "breakeven": k + p_in if kind == "C" else k - p_in, "premium_stop": round(p_stop, 2),
            "premium_target": round(p_tgt, 2), "theta_rs_day": round(g["theta_day"] * lot * lots),
            "vega_rs_pt": round(g["vega_pt"] * lot * lots), "delta": round(g["delta"], 2), "pop": None,
            "reason": "options are cheap versus realised movement, so buying is fair value"}, ""


def _plan_debit(sg, ch, m, ctx, lv, lot, t0, t1) -> tuple[dict | None, str]:
    kind = "C" if sg > 0 else "P"
    by = {k: (c, p) for k, c, p in ch.rows}
    i = 0 if kind == "C" else 1
    k1 = m["atm"]
    far = [k for k, _, _ in ch.rows if sg * (k - k1) >= 2 * m["step"]]
    if not far:
        return None, "no strike far enough out for the short leg"
    k2 = min(far, key=lambda k: abs(k - lv["target"]))
    l1, l2 = by[k1][i], by[k2][i]
    if not (_liquid(l1, ctx.is_index) and _liquid(l2, ctx.is_index)):
        return None, "legs of the debit spread are not liquid enough"
    r = lambda k, leg, s, t: reprice(kind, k, leg, ch.spot, t0, s, t)  # noqa: E731
    d_in = r(k1, l1, lv["trigger"], t0) - r(k2, l2, lv["trigger"], t0)
    width = abs(k2 - k1)
    if d_in <= 0 or d_in >= 0.7 * width:
        return None, f"spread debit ₹{d_in:.1f} is too close to its ₹{width:.0f} width"

    def pnl(s, t):
        a1, a2 = r(k1, l1, s, t), r(k2, l2, s, t)
        return (net_pnl("options", r(k1, l1, lv["trigger"], t0), a1, lot, ctx.broker)
                + net_pnl("options", a2, r(k2, l2, lv["trigger"], t0), lot, ctx.broker)), a1 - a2
    pl_stop, v_stop = pnl(lv["stop"], t1)
    pl_tgt, v_tgt = pnl(lv["target"], t1)
    loss_lot, win_lot = -pl_stop, pl_tgt
    lots = _size(loss_lot, d_in * lot, d_in * lot, ctx)
    if lots < 1:
        return None, f"1 lot of the spread risks ₹{d_in * lot:,.0f} (loss at stop ₹{loss_lot:,.0f}), beyond your risk budget"
    if win_lot <= 0 or win_lot / loss_lot < 1.5:
        return None, f"debit spread reward to risk after charges is {win_lot / loss_lot if loss_lot > 0 else 0:.2f}, below 1.5"
    name = "Bull call spread" if kind == "C" else "Bear put spread"
    tag = "CE" if kind == "C" else "PE"
    g1, g2 = greeks(kind, ch.spot, k1, t0, l1.iv), greeks(kind, ch.spot, k2, t0, l2.iv)
    net = (lambda f: f(g1) - f(g2)) if g1 and g2 else (lambda f: 0.0)  # noqa: E731
    return {"structure": name,
            "legs": [{"action": "BUY", "kind": tag, "strike": k1, "ltp": l1.ltp, "iv": l1.iv},
                     {"action": "SELL", "kind": tag, "strike": k2, "ltp": l2.ltp, "iv": l2.iv}],
            "lots": lots, "qty": lots * lot, "outlay": round(d_in * lot * lots), "max_loss": round(d_in * lot * lots),
            "risk_rs": round(loss_lot * lots), "reward_rs": round(win_lot * lots), "rr_net": round(win_lot / loss_lot, 2),
            "max_profit": round((width - d_in) * lot * lots), "net_debit": round(d_in, 2),
            "breakeven": k1 + d_in if kind == "C" else k1 - d_in, "premium_stop": round(v_stop, 2),
            "premium_target": round(v_tgt, 2), "pop": None, "delta": round(net(lambda g: g["delta"]), 2),
            "theta_rs_day": round(net(lambda g: g["theta_day"]) * lot * lots),
            "reason": "option prices are fair to rich, so the short leg pays for part of the long leg"}, ""


def _plan_credit(sg, ch, m, ctx, lv, lot, t0, t1, a) -> tuple[dict | None, str]:
    kind = "P" if sg > 0 else "C"
    by = {k: (c, p) for k, c, p in ch.rows}
    i = 1 if kind == "P" else 0
    wall = m["put_wall"] if sg > 0 else m["call_wall"]
    ks_all = [k for k, _, _ in ch.rows]
    if wall and sg * (ch.spot - wall) >= 0.6 * a:
        ks = wall
    else:
        ok = [k for k in ks_all if sg * (ch.spot - k) >= 1.0 * a]
        if not ok:
            return None, "no strike far enough from spot for a credit spread"
        ks = max(ok) if sg > 0 else min(ok)
    kl = ks - sg * 2 * m["step"]
    s_leg, l_leg = by.get(ks, (None, None))[i], by.get(kl, (None, None))[i]
    if not (_liquid(s_leg, ctx.is_index) and _liquid(l_leg, ctx.is_index)):
        return None, "legs of the credit spread are not liquid enough"
    width = abs(ks - kl)
    credit = s_leg.ltp - l_leg.ltp
    g = greeks(kind, ch.spot, ks, t0, s_leg.iv)
    pop = 1 - abs(g["delta"]) if g else None
    if credit < 0.25 * width or not pop or pop < 0.65:
        return None, f"credit ₹{credit:.1f} on a ₹{width:.0f} spread (probability about {pop or 0:.0%}) is not worth the risk"
    s_stop = reprice(kind, ks, s_leg, ch.spot, t0, ks, t1)
    l_stop = reprice(kind, kl, l_leg, ch.spot, t0, ks, t1)
    loss_pu = (s_stop - l_stop) - credit
    fees = -(net_pnl("options", s_leg.ltp, s_leg.ltp, lot, ctx.broker) + net_pnl("options", l_leg.ltp, l_leg.ltp, lot, ctx.broker))
    loss_lot = loss_pu * lot + fees
    win_lot = 0.5 * credit * lot - fees
    max_loss_lot = (width - credit) * lot
    lots = _size(loss_lot, max_loss_lot, max_loss_lot, ctx)
    if lots < 1:
        return None, f"1 lot can lose ₹{max_loss_lot:,.0f}, beyond your risk budget"
    if win_lot <= 0 or win_lot / loss_lot < 0.5:
        return None, "credit spread pays too little for the loss at the stop"
    tag = "PE" if kind == "P" else "CE"
    gl = greeks(kind, ch.spot, kl, t0, l_leg.iv)
    net_delta = (-g["delta"] + gl["delta"]) if gl else -g["delta"]
    net_theta = (-g["theta_day"] + (gl["theta_day"] if gl else 0.0)) * lot * lots
    return {"structure": "Bull put spread (credit)" if kind == "P" else "Bear call spread (credit)",
            "legs": [{"action": "SELL", "kind": tag, "strike": ks, "ltp": s_leg.ltp, "iv": s_leg.iv, "delta": round(g["delta"], 2)},
                     {"action": "BUY", "kind": tag, "strike": kl, "ltp": l_leg.ltp, "iv": l_leg.iv}],
            "lots": lots, "qty": lots * lot, "net_credit": round(credit, 2), "outlay": 0,
            "max_loss": round(max_loss_lot * lots), "risk_rs": round(loss_lot * lots), "reward_rs": round(win_lot * lots),
            "max_profit": round(credit * lot * lots), "rr_net": round(win_lot / loss_lot, 2),
            "breakeven": ks - credit if kind == "P" else ks + credit, "pop": round(pop, 2),
            "theta_rs_day": round(net_theta), "premium_stop": None, "premium_target": None,
            "stop_override": ks, "delta": round(net_delta, 2),
            "reason": "options are rich versus realised movement, so selling a defined-risk spread beyond the OI wall earns that premium; "
                      "take profit at 50% of the credit, stop when the underlying reaches the short strike"}, ""


def futures_alt(sg: int, fut: Future | None, lv: dict, lot: int | None, ctx: Ctx, spot: float) -> dict | None:
    if not fut or not lot:
        return None
    off = fut.ltp - spot
    entry, stop, target = lv["trigger"] + off, lv["stop"] + off, lv["target"] + off
    risk_lot = -net_pnl("futures", entry, stop, lot, ctx.broker) if sg > 0 else -net_pnl("futures", stop, entry, lot, ctx.broker)
    win_lot = net_pnl("futures", entry, target, lot, ctx.broker) if sg > 0 else net_pnl("futures", target, entry, lot, ctx.broker)
    budget = ctx.capital * ctx.risk_pct / 100
    return {"contract": f"{fut.symbol} {fut.expiry:%d %b} FUT", "side": "BUY" if sg > 0 else "SELL", "entry": round(entry, 2),
            "stop": round(stop, 2), "target": round(target, 2), "lot": lot, "risk_per_lot": round(risk_lot),
            "reward_per_lot": round(win_lot), "notional": round(entry * lot), "fits_budget": risk_lot <= budget,
            "budget": round(budget)}


def make_plan(direction: str, ch: Chain, m: dict, fut: Future | None, ctx: Ctx, lot: int | None,
              atr: float | None) -> tuple[dict | None, dict | None, list[str]]:
    if not lot:
        return None, None, ["lot size unavailable"]
    if not atr:
        return None, None, ["daily ATR unavailable, so stops cannot be sized"]
    if m["dte"] < MIN_DTE:
        return None, None, [f"only {m['dte']:.1f} days to expiry: too little time for a directional options trade"]
    lv, why = _levels(direction, ch.spot, m, atr)
    if not lv:
        return None, None, [why]
    sg, t0 = lv["sg"], m["dte"] / 365
    t1 = max(t0 - HOLD_DAYS / 365, 1 / 365)
    ratio = m["iv_ratio"]
    order = ["long", "debit"] if ratio and ratio <= 1.0 else ["credit", "debit"] if ratio and ratio >= 1.35 else ["debit", "long"]
    rejects, plan = [], None
    for name in order:
        res = {"long": lambda: _plan_long(sg, ch, m, ctx, lv, ctx.lot or lot, t0, t1),
               "debit": lambda: _plan_debit(sg, ch, m, ctx, lv, lot, t0, t1),
               "credit": lambda: _plan_credit(sg, ch, m, ctx, lv, lot, t0, t1, atr)}[name]()
        if res[0]:
            plan = res[0]
            break
        rejects.append(f"{name}: {res[1]}")
    if not plan:
        return None, futures_alt(sg, fut, lv, lot, ctx, ch.spot), rejects
    plan["levels"] = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in lv.items()}
    if plan.get("stop_override"):
        plan["levels"]["stop"] = float(plan["stop_override"])
        plan["levels"]["stop_why"] = "the short strike: close the spread if the underlying reaches it"
    return plan, futures_alt(sg, fut, lv, lot, ctx, ch.spot), []


# ---- orchestration --------------------------------------------------------------------------------------
def analyse(ch: Chain, fut: Future | None, ctx: Ctx, now: datetime) -> View:
    m = metrics(ch, now, ctx.atr)
    nodes = build_nodes(ch, m, fut, ctx, now)
    direction, score, conf, notes = decide(nodes, ctx)
    view = View(ctx.symbol, ch.spot, ch.expiry.isoformat(), m["dte"], ch.ts, direction, round(score, 1), conf,
                nodes, m, notes=notes)
    if direction in ("bullish", "bearish"):
        view.plan, view.futures_alt, rejects = make_plan(direction, ch, m, fut, ctx, ctx.lot, ctx.atr)
        if not view.plan:
            view.notes += ["no trade passes your rules: " + "; ".join(rejects)] if rejects else []
    return view


def build_graph(v: View) -> dict:
    """Nodes and edges that explain the verdict, ready to render with any graph tool."""
    nodes = [{"id": "underlying", "type": "underlying", "label": f"{v.symbol} {v.spot:,.2f}"},
             {"id": "verdict", "type": "conclusion", "label": f"{v.direction.upper()} ({v.score:+.0f}), confidence {v.confidence}"}]
    edges = [{"from": "underlying", "to": "verdict", "relation": "evaluated_as", "weight": 1}]
    sign = 1 if v.score > 0 else -1
    ctx_edges = []
    for n in v.nodes:
        nodes.append({"id": n.id, "type": "evidence" if n.kind == "direction" else "context", "label": n.label,
                      "value": n.value, "score": n.score, "weight": n.weight})
        if n.kind == "direction":
            rel = "neutral" if abs(n.score) < 0.5 else "supports" if n.score * sign > 0 else "opposes"
            edges.append({"from": n.id, "to": "verdict", "relation": rel, "weight": round(n.weight * n.score, 2)})
        else:
            ctx_edges.append({"from": n.id, "to": "structure", "relation": "selects_structure", "weight": 1})
    m = v.metrics
    for key, label in (("put_wall", "Support (max put OI)"), ("call_wall", "Resistance (max call OI)"), ("max_pain", "Max pain")):
        if m.get(key):
            nodes.append({"id": key, "type": "level", "label": label, "value": f"{m[key]:,.0f}"})
    if v.plan:
        lv = v.plan["levels"]
        nodes += [{"id": "structure", "type": "plan", "label": v.plan["structure"]},
                  {"id": "stop", "type": "plan", "label": "Stop (underlying)", "value": f"{lv['stop']:,.2f}"},
                  {"id": "target", "type": "plan", "label": "Target (underlying)", "value": f"{lv['target']:,.2f}"}]
        edges += ctx_edges + [{"from": "verdict", "to": "structure", "relation": "implies", "weight": 1}]
        adverse, favour = ("put_wall", "call_wall") if lv["sg"] > 0 else ("call_wall", "put_wall")
        if m.get(adverse):
            edges.append({"from": adverse, "to": "stop", "relation": "sets", "weight": 1})
        if m.get(favour):
            edges.append({"from": favour, "to": "target", "relation": "caps", "weight": 1})
    return {"nodes": nodes, "edges": edges}
