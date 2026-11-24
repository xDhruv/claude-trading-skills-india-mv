"""Plain-text F&O reports: the full view with its evidence graph, and a short pulse across underlyings."""
from datetime import date, datetime

from .render import inr, stamp

FNO_WARNING = ("F&O is high risk: SEBI studies found roughly 9 in 10 individual traders lost money in it. Educational analysis "
               "from delayed NSE data, not investment advice and not a SEBI-registered adviser. Margins are not computed: "
               "check them, and the live bid-ask, in your broker. This agent never places orders.")

ARROW = {"bullish": "BULLISH", "bearish": "BEARISH", "neutral": "NEUTRAL", "conflicted": "CONFLICTED"}


def _evidence(view) -> list[str]:
    sign = 1 if view.score > 0 else -1
    out = []
    for n in view.nodes:
        if n.kind != "direction":
            continue
        tag = "·" if abs(n.score) < 0.5 else "supports" if n.score * sign > 0 else "opposes "
        out.append(f"  {tag:8} {n.score:+.1f} x{n.weight:g}  {n.label}: {n.value}")
    return out


def graph_text(view) -> str:
    """The knowledge graph as a readable tree: evidence feeds the verdict, levels feed the stop and target."""
    sign = 1 if view.score > 0 else -1
    lines = [f"{view.symbol} {view.spot:,.2f}", f"└─ verdict: {ARROW.get(view.direction, view.direction)} "
             f"(score {view.score:+.0f}/100, confidence {view.confidence})"]
    direction = [n for n in view.nodes if n.kind == "direction"]
    for i, n in enumerate(direction):
        branch = "   ├─" if i < len(direction) - 1 or view.plan else "   └─"
        rel = "~" if abs(n.score) < 0.5 else "+" if n.score * sign > 0 else "-"
        lines.append(f"{branch} [{rel}] {n.label}: {n.value}")
    ctx = [n for n in view.nodes if n.kind == "context"]
    if view.plan:
        lv = view.plan["levels"]
        lines.append(f"   └─ plan: {view.plan['structure']}")
        for n in ctx:
            lines.append(f"       ├─ chosen with: {n.label}: {n.value}")
        lines.append(f"       ├─ stop {lv['stop']:,.2f} ({lv['stop_why']})")
        lines.append(f"       └─ target {lv['target']:,.2f} ({lv['target_why']})")
    elif ctx:
        for n in ctx:
            lines.append(f"   · context: {n.label}: {n.value}")
    return "\n".join(lines)


def _levels(m: dict) -> list[str]:
    out = ["KEY LEVELS"]
    if m.get("put_wall"):
        out.append(f"  Support    {m['put_wall']:,.0f}  (highest put OI {m['put_wall_oi']:,.0f})")
    if m.get("call_wall"):
        out.append(f"  Resistance {m['call_wall']:,.0f}  (highest call OI {m['call_wall_oi']:,.0f})")
    if m.get("max_pain"):
        out.append(f"  Max pain   {m['max_pain']:,.0f}")
    if m.get("implied_move_pct"):
        out.append(f"  Straddle implies about ±{m['implied_move_pct']:.1f}% by expiry (ATM IV {m['atm_iv']:.1f}%)")
    if m.get("pcr_oi") is not None:
        out.append(f"  PCR (OI) {m['pcr_oi']:.2f}, PCR (volume) {m['pcr_vol'] or 0:.2f}")
    return out


def _exp(view) -> str:
    return date.fromisoformat(view.expiry).strftime("%d %b %Y")


def _plan(view) -> list[str]:
    p, lv = view.plan, view.plan["levels"]
    out = [f"TRADE: {p['structure']}   (lot {view.lot}, {p['lots']} lot{'s' if p['lots'] > 1 else ''} = qty {p['qty']})"]
    for leg in p["legs"]:
        out.append(f"  {leg['action']:4} {view.symbol} {_exp(view)} {leg['strike']:,.0f} {leg['kind']}  "
                   f"last ₹{leg['ltp']:,.2f}, IV {leg['iv']:.1f}%")
    if "net_debit" in p:
        out.append(f"  Net debit ≈ ₹{p['net_debit']:.1f} per unit; max loss ₹{p['max_loss']:,}, max profit ₹{p['max_profit']:,}")
    elif "net_credit" in p:
        out.append(f"  Net credit ≈ ₹{p['net_credit']:.1f} per unit; max profit ₹{p['max_profit']:,}, max loss ₹{p['max_loss']:,}")
    else:
        out.append(f"  Expect to pay about ₹{p['legs'][0]['entry_est']:.1f} once the trigger hits (use a limit order); "
                   f"outlay ₹{p['outlay']:,} = your max loss")
    out += [f"  When: underlying trades {'above' if lv['sg'] > 0 else 'below'} {lv['trigger']:,.2f}; skip if it already "
            f"went past {lv['chase']:,.2f}",
            f"  Stop: underlying {lv['stop']:,.2f} ({lv['stop_why']})",
            f"  Target: underlying {lv['target']:,.2f} ({lv['target_why']})"]
    if "net_credit" in p:
        out.append(f"  If stopped ₹{p['risk_rs']:,}; if you take profit at 50% of the credit ₹{p['reward_rs']:,}; "
                   f"reward:risk after charges {p['rr_net']}; chance of profit about {p['pop']:.0%}")
    else:
        out.append(f"  If stopped ₹{p['risk_rs']:,}; if target ₹{p['reward_rs']:,}; reward:risk after charges {p['rr_net']}")
    out.append(f"  Break-even at expiry {p['breakeven']:,.1f}")
    if p.get("theta_rs_day") is not None:
        out.append(f"  Position delta {p['delta']:+.2f} per unit, time decay "
                   f"{'earns' if p['theta_rs_day'] > 0 else 'costs'} about ₹{abs(p['theta_rs_day']):,}/day")
    out += [f"  Why this structure: {p['reason']}",
            "  Exit: at the stop or target, or with 1 day to expiry. Do not average down; do not hold through expiry."]
    return out


def _futures(view) -> list[str]:
    f = view.futures_alt
    if not f:
        return []
    fit = "fits" if f["fits_budget"] else f"does NOT fit (budget ₹{f['budget']:,})"
    return ["", f"FUTURES ALTERNATIVE: {f['side']} {f['contract']} entry {f['entry']:,.2f}, stop {f['stop']:,.2f}, "
            f"target {f['target']:,.2f}", f"  1 lot ({f['lot']}) risks ₹{f['risk_per_lot']:,}, pays ₹{f['reward_per_lot']:,}, "
            f"notional ₹{f['notional']:,}; risk {fit}. Futures carry unlimited-loss gaps and need SPAN margin."]


def render_view(view, now: datetime) -> str:
    head = f"F&O VIEW: {view.symbol}   {ARROW.get(view.direction, view.direction)}   score {view.score:+.0f}/100   confidence {view.confidence}"
    out = [head,
           f"As of {stamp(now)}; NSE chain stamp {view.ts:%d %b %H:%M}" if view.ts else f"As of {stamp(now)}",
           f"Expiry {_exp(view)} ({view.metrics['dte']:.1f} days), spot {view.spot:,.2f}, lot size {view.lot or 'unknown'}.",
           ""]
    if view.stale:
        out += ["WARNING: NSE's data is not from the current live session (market closed, holiday or stale). "
                "Treat this as the last available snapshot, not a live signal.", ""]
    if view.plan:
        out += _plan(view) + _futures(view)
    else:
        verdict = {"neutral": "NO TRADE: the evidence is mixed.", "conflicted": "NO TRADE: the signals conflict."}.get(
            view.direction, "NO TRADE: a setup exists but nothing passes your risk rules.")
        out += [verdict] + [f"  - {n}" for n in view.notes] + _futures(view)
    out += ["", "EVIDENCE (score x weight)"] + _evidence(view) + [""] + _levels(view.metrics)
    ctx = [n for n in view.nodes if n.kind == "context"]
    if ctx:
        out += ["", "CONTEXT"] + [f"  {n.label}: {n.value}" for n in ctx]
    out += ["", "KNOWLEDGE GRAPH", graph_text(view), "", FNO_WARNING]
    return "\n".join(out)


def render_pulse(views: list, errors: list[str], now: datetime) -> str:
    out = [f"F&O pulse as of {stamp(now)}", ""]
    for v in views:
        plan = f"  -> {v.plan['structure']}" if v.plan else ""
        out.append(f"{v.symbol}: {ARROW.get(v.direction, v.direction)} ({v.score:+.0f}), PCR {v.metrics.get('pcr_oi') or 0:.2f}, "
                   f"support {v.metrics.get('put_wall') or 0:,.0f}, resistance {v.metrics.get('call_wall') or 0:,.0f}, "
                   f"max pain {v.metrics.get('max_pain') or 0:,.0f}{plan}")
    for v in views:
        out += ["", graph_text(v)]
    for e in errors:
        out.append(f"(skipped: {e})")
    out += ["", FNO_WARNING]
    return "\n".join(out)


def render_update(idea: dict, event: str, price: float, now: datetime) -> str:
    label = {"triggered": "TRIGGERED: the underlying reached the trigger", "target": "TARGET HIT: book profit",
             "stopped": "STOP HIT: close the position", "invalidated": "INVALIDATED before trigger",
             "expired": "EXPIRED", "exit_reminder": "EXIT REMINDER: the plan assumes about a one-day hold, so review "
             "the position before the close instead of carrying it blindly"}.get(event, event)
    return (f"{idea['symbol']} {idea['structure']}: {label}\nUnderlying near {inr(price)} (trigger {inr(idea['entry'])}, "
            f"stop {inr(idea['stop'])}, target {inr(idea['target'])}).\nAs of {stamp(now)}, delayed.")
