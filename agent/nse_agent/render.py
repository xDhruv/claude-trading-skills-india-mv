"""Plain-text email bodies."""
from datetime import datetime

DISCLAIMER = ("Educational analysis from delayed NSE data (1-5 min), not investment advice. Not a SEBI-registered "
              "adviser. Verify prices in your broker before acting. No orders are placed by this agent.")


def inr(x: float) -> str:
    return f"₹{x:,.2f}"


def stamp(now: datetime) -> str:
    return now.strftime("%H:%M IST")


def render_idea(i: dict) -> str:
    exit_line = "\n  Exit by 15:15 IST (intraday)" if i["horizon"] == "intraday" else ""
    lines = [
        f"IDEA: {i['symbol']}  [{i['setup'].replace('_', ' ')}, {i['horizon']}]",
        f"  Trigger: buy above {inr(i['entry'])}",
        f"  Stop: {inr(i['stop'])} (14-day ATR is ₹{i['atr']})   Target: {inr(i['target'])}",
        f"  Qty: {i['qty']}   Risk: ₹{i['risk_rs']:,}   Reward: ₹{i['reward_rs']:,}   "
        f"R:R after costs: {i['rr_net']}",
        "  Why: " + "; ".join(i["reasons"]),
    ]
    if i.get("news"):
        lines.append("  News: " + " | ".join(i["news"]))
    lines.append(f"  Invalid if: price falls to {inr(i['stop'])} before the trigger, or NIFTY turns sharply negative"
                 + exit_line)
    return "\n".join(lines)


def render_scan(now, tone, nifty, vix, ideas, watch, alerts, vetoed) -> str:
    out = [f"Tape: {tone}. NIFTY {nifty.last:,.0f} ({nifty.pct:+.2f}%), VIX {vix.last:.1f} ({vix.pct:+.1f}%).",
           f"Data: NSE CM Live, delayed ~2 min, as of {stamp(now)}.", ""]
    out += [render_idea(i) + "\n" for i in ideas]
    for w in watch:
        out.append(f"WATCH: {w['symbol']} - " + "; ".join(w["reasons"]))
    for a in alerts:
        out.append(f"RISK ALERT: {a['symbol']} - " + "; ".join(a["reasons"]))
    if vetoed:
        out.append("Skipped on negative news: " + ", ".join(vetoed))
    out += ["", DISCLAIMER]
    return "\n".join(out)


def render_updates(events: list[tuple[dict, str, float]], now) -> str:
    out = [f"As of {stamp(now)} (delayed quotes):", ""]
    for idea, event, price in events:
        label = {"triggered": "TRIGGERED", "target": "TARGET HIT", "stopped": "STOP HIT",
                 "invalidated": "INVALIDATED", "expired": "EXPIRED (not triggered)",
                 "exit_reminder": "EXIT REMINDER (intraday, exit by 15:15)"}[event]
        out.append(f"{idea['symbol']}: {label} near {inr(price)}. Entry {inr(idea['entry'])}, "
                   f"stop {inr(idea['stop'])}, target {inr(idea['target'])}.")
    out += ["", DISCLAIMER]
    return "\n".join(out)


def render_news(items, summary: str | None, now, context: str) -> str:
    out = [f"{context}  (as of {stamp(now)})", ""]
    if summary:
        out += ["Read: " + summary, ""]
    for it in items:
        tag = f" [{', '.join(it.symbols)}]" if it.symbols else ""
        out.append(f"- {it.title}{tag}\n  {it.source} {it.link}")
    out += ["", "Headlines are from public RSS feeds and are not verified. " + DISCLAIMER]
    return "\n".join(out)


def render_market_alert(lines: list[str], now) -> str:
    return "\n".join([f"As of {stamp(now)}:", ""] + lines + ["", DISCLAIMER])


def render_preopen(nifty_prev: str, vix_line: str, breadth: str, sectors: str, news_lines: list[str],
                   summary: str | None) -> str:
    out = ["Previous session", nifty_prev, vix_line, breadth, sectors, ""]
    if summary:
        out += ["Read: " + summary, ""]
    if news_lines:
        out += ["Headlines to watch"] + news_lines + [""]
    out += ["Live scans start about 09:45 IST. GIFT Nifty and global cues are not covered by NSE's MCPs.",
            "", DISCLAIMER]
    return "\n".join(out)


def render_close(now, nifty, ideas: list[dict], watch: list[str], breadth: str | None) -> str:
    out = [f"NIFTY closed {nifty.last:,.0f} ({nifty.pct:+.2f}%)." + (f" {breadth}" if breadth else ""), ""]
    if ideas:
        out.append("Today's ideas")
        for i in ideas:
            out.append(f"- {i['symbol']} ({i['setup'].replace('_', ' ')}): {i['status'].replace('_', ' ')}")
    else:
        out.append("No trade ideas passed the filters today.")
    if watch:
        out += ["", "Watchlist for tomorrow: " + ", ".join(watch)]
    out += ["", DISCLAIMER]
    return "\n".join(out)
