"""Answer `[NSE Ask]` emails you send to yourself. Read-only commands only; replies go to EMAIL_TO."""
import email
import imaplib
import logging
from datetime import timezone
from email.header import decode_header, make_header
from email.utils import parseaddr

from . import news, render
from .nse import McpError

log = logging.getLogger("nse_agent")
TAG = "[NSE Ask]"
HELP = ("Commands (put them in the subject after [NSE Ask] or in the first line of the body):\n"
        "  price RELIANCE      live quote with trend context\n"
        "  index NIFTY BANK    live index (nifty, banknifty, vix are shortcuts)\n"
        "  movers              top gainers and losers now\n"
        "  ideas               today's ideas and their status\n"
        "  news TCS            recent headlines (leave the symbol off for market news)\n")


def _text(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        return ""
    return (msg.get_payload(decode=True) or b"").decode(msg.get_content_charset() or "utf-8", "replace")


def command_from(subject: str, body: str) -> str:
    rest = subject.replace(TAG, "").strip()
    if rest.lower().startswith(("re:", "fwd:")) or not rest:
        rest = next((ln.strip() for ln in body.splitlines() if ln.strip() and not ln.startswith(">")), "")
    return rest


def answer(cmd: str, data, agent, now) -> str:
    words = cmd.split()
    if not words:
        return HELP
    verb, arg = words[0].lower(), " ".join(words[1:]).upper()
    try:
        if verb in ("nifty", "banknifty", "vix") or verb == "index":
            name = {"nifty": "NIFTY 50", "banknifty": "NIFTY BANK", "vix": "INDIA VIX"}.get(verb, arg or "NIFTY 50")
            q = data.index_quote(name)
            return (f"{q.name}: {q.last:,.2f} ({q.pct:+.2f}%), day range {q.low:,.2f} to {q.high:,.2f}, "
                    f"prev close {q.prev:,.2f}. NSE timestamp {q.ts}.")
        if verb in ("price", "quote") or (len(words) == 1 and verb not in ("movers", "ideas", "news", "help")):
            sym = (arg or verb.upper())
            q = data.stock_quote(sym)
            h = agent.history(sym, now.date())
            extra = ""
            if h and h["bars"]:
                closes = [b[4] for b in h["bars"]]
                s50 = sum(closes[-50:]) / min(50, len(closes))
                extra = (f"\n50-day SMA {s50:,.2f}, 200-day SMA {h['sma200']}, "
                         f"3m vs NIFTY {h['outperf']} pts." + (" Recent corporate action: check before trusting % moves."
                                                              if h["split"] else ""))
            return (f"{q.symbol}: {q.ltp:,.2f} ({q.pct:+.2f}%), day {q.low:,.2f} to {q.high:,.2f}, open {q.open:,.2f}, "
                    f"volume {q.volume:,.0f}, 52w {q.low52:,.2f} to {q.high52:,.2f}. As of {q.ts}.{extra}")
        if verb == "movers":
            g, l = data.movers("NIFTY", 5)
            return ("Gainers: " + ", ".join(f"{m.symbol} {m.pct:+.1f}%" for m in g)
                    + "\nLosers: " + ", ".join(f"{m.symbol} {m.pct:+.1f}%" for m in l))
        if verb == "ideas":
            ideas = list(agent.state.data["ideas"].values())
            return "\n".join(f"{i['symbol']}: {i['status']} (entry {i['entry']}, stop {i['stop']}, target {i['target']})"
                             for i in ideas) or "No ideas today yet."
        if verb == "news":
            items = news.symbol_news(arg, now) if arg else news.fresh_relevant(
                news.fetch_feeds(agent.cfg.news_feeds), set(), agent.symbols_of_interest(),
                now.astimezone(timezone.utc), max_age_h=12, min_score=2)
            return "\n".join(f"- {i.title}" for i in items[:6]) or "No recent headlines found."
    except (McpError, KeyError, TypeError, ValueError) as exc:
        return f"Could not fetch that: {exc}"
    return HELP


def check_inbox(cfg, data, agent, limit: int = 5) -> bool:
    from .clock import now_ist
    now = now_ist()
    replied = False
    try:
        box = imaplib.IMAP4_SSL(cfg.imap_host)
        box.login(cfg.smtp_user, cfg.smtp_password)
        box.select("INBOX")
        typ, ids = box.search(None, "UNSEEN", "FROM", f'"{cfg.email_to}"', "SUBJECT", f'"{TAG}"')
        for num in (ids[0].split() if typ == "OK" else [])[:limit]:
            _, parts = box.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(parts[0][1])
            auth = str(msg.get("Authentication-Results", "")).lower()
            if parseaddr(msg.get("From", ""))[1].lower() != cfg.email_to.lower() or "dkim=fail" in auth \
                    or "spf=fail" in auth:
                continue  # only genuine mail from your own address is acted on
            subject = str(make_header(decode_header(msg.get("Subject", ""))))
            cmd = command_from(subject, _text(msg))
            reply = answer(cmd, data, agent, now)
            body = f"{reply}\n\nAs of {render.stamp(now)} (delayed).\n\n{render.DISCLAIMER}"
            agent.mailer.send(f"Re: {subject}", body, msg.get("Message-ID"))
            replied = True
        box.logout()
    except (imaplib.IMAP4.error, OSError) as exc:
        log.warning("inbox check failed: %s", exc)
    return replied
