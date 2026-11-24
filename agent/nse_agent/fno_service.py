"""Glue between the data sources and the F&O analysis. Used by the agent loop, inbox replies and the MCP server."""
import json
import math
import time
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path

from . import analysis, fno_news, fno_patterns, news
from .clock import IST
from .fno_analysis import MIN_DTE, Ctx, View, analyse
from .fno_data import INDEX_SYMBOLS, FnoError, NseFno
from .nse import Bar, McpError

HISTORY_ERRORS = (FnoError, McpError, KeyError, TypeError, ValueError, OSError)
NEWS_TTL = 600  # seconds
CLOSED = ("target", "stopped", "invalidated", "expired")


def _journal_path(state_dir: str) -> Path:
    return Path(state_dir) / "fno_journal.jsonl"


def journal_add(state_dir: str, idea: dict, status: str, price: float | None = None) -> None:
    """Append a closed F&O idea to a file that outlives the daily state reset."""
    row = {"date": idea.get("id", "")[:10], "symbol": idea.get("symbol"), "structure": idea.get("structure"),
           "mode": idea.get("mode") or "directional", "status": status, "price": price}
    try:
        p = _journal_path(state_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


def journal_stats(state_dir: str) -> dict | None:
    """Hit rate (target reached) of closed ideas overall and per structure, or None with no history."""
    try:
        rows = [json.loads(x) for x in _journal_path(state_dir).read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return None
    rows = [r for r in rows if r.get("status") in CLOSED]
    if not rows:
        return None
    by: dict[str, dict] = {}
    for r in rows:
        s = by.setdefault(r.get("structure") or "?", {"n": 0, "hit": 0})
        s["n"] += 1
        s["hit"] += r["status"] == "target"
    return {"n": len(rows), "hit": sum(1 for r in rows if r["status"] == "target") / len(rows),
            "by": {k: {"n": v["n"], "hit": v["hit"] / v["n"]} for k, v in by.items()}}


class FnoService:
    def __init__(self, cfg, fno: NseFno, data, cache: dict | None = None):
        self.cfg, self.fno, self.data = cfg, fno, data
        self.cache = cache if cache is not None else {}
        self._spurts: tuple[float, dict] | None = None
        self._news: dict[str, tuple[float, list]] = {}

    # ---- price history -----------------------------------------------------------------------
    def stats(self, symbol: str, today: date) -> dict:
        key = f"fno:{symbol}:{today}"
        if key in self.cache:
            return self.cache[key]
        if symbol in INDEX_SYMBOLS:
            bars = self.data.index_history(INDEX_SYMBOLS[symbol], today)
        else:
            bars = self.data.history(symbol, today)
        bars = [b for b in bars if b.date < today.isoformat()]
        closes = [b.close for b in bars]
        out = {"sma50": analysis.sma(closes, 50), "atr": analysis.atr(bars)}
        self.cache[key] = out
        return out

    def _hist_path(self, symbol: str, today: date) -> Path:
        safe = "".join(c for c in symbol if c.isalnum())
        return Path(self.cfg.state_dir) / f"hist_{safe}_{today.isoformat()}.json"

    def history(self, symbol: str, today: date) -> list[Bar]:
        """Up to a few years of daily bars before today, paged backwards through the data source and cached per day."""
        path = self._hist_path(symbol, today)
        try:
            return [Bar(**r) for r in json.loads(path.read_text())]
        except (OSError, ValueError, TypeError):
            pass
        index = symbol in INDEX_SYMBOLS
        bars: list[Bar] = []
        end = today
        for _ in range(4 if index else 8):
            try:
                page = self.data.index_history(INDEX_SYMBOLS[symbol], end, 12) if index else self.data.history(symbol, end, 12)
            except HISTORY_ERRORS:
                break
            have = {b.date for b in bars}
            new = [b for b in page if b.date not in have and b.date < today.isoformat()]
            if not new:
                break
            bars = sorted(new + bars, key=lambda b: b.date)
            end = date.fromisoformat(bars[0].date) - timedelta(days=1)
        if bars:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                prefix = path.name.rsplit("_", 1)[0]
                for old in path.parent.glob(f"{prefix}_*.json"):
                    old.unlink(missing_ok=True)
                path.write_text(json.dumps([asdict(b) for b in bars]))
            except OSError:
                pass
        return bars

    def brain(self, symbol: str, today: date, dte: float) -> dict:
        """Chart patterns and historical analogs for one underlying. Empty values if there is too little history."""
        horizon = fno_patterns.trading_days(dte)
        key = f"brain:{symbol}:{today}:{horizon}"
        if key in self.cache:
            return self.cache[key]
        bars = self.history(symbol, today)
        chart = fno_patterns.read_chart(bars)
        analog = fno_patterns.analogs(bars, horizon)
        rets = None
        if analog:
            scale = math.sqrt(max(dte * 5 / 7, 1.0) / horizon)  # stretch to the expiry when the horizon was capped
            rets = [r * scale for r in analog["rets"]]
        out = {"chart": chart or None, "analog": analog, "rets": rets, "rv_ann": chart.get("rv_ann") if chart else None}
        self.cache[key] = out
        return out

    # ---- news and events ---------------------------------------------------------------------
    def _cached(self, key: str, fetch) -> list:
        hit = self._news.get(key)
        if hit and time.time() - hit[0] < NEWS_TTL:
            return hit[1]
        try:
            items = list(fetch())
        except Exception:  # the news sources are best-effort and must never block a trade view
            items = []
        self._news[key] = (time.time(), items)
        return items

    def headlines(self, symbol: str, now: datetime) -> tuple[dict, dict]:
        market = self._cached("market", lambda: news.fetch_feeds(self.cfg.news_feeds))
        own = [] if symbol in INDEX_SYMBOLS else self._cached(f"sym:{symbol}", lambda: news.symbol_news(symbol, now))
        return fno_news.headline_bias(own if own else market, now), fno_news.event_risk(market + own, now)

    # ---- trade journal -----------------------------------------------------------------------
    def journal_add(self, idea: dict, status: str, price: float | None = None) -> None:
        journal_add(self.cfg.state_dir, idea, status, price)

    def journal_stats(self) -> dict | None:
        return journal_stats(self.cfg.state_dir)
    # ---- chain and view ----------------------------------------------------------------------
    def spurts(self) -> dict:
        if not self._spurts or time.time() - self._spurts[0] > 120:
            try:
                self._spurts = (time.time(), self.fno.oi_spurts())
            except FnoError:
                self._spurts = (time.time(), {})
        return self._spurts[1]

    def pick_expiry(self, symbol: str, now: datetime) -> date:
        expiries = self.fno.expiries(symbol)
        for e in expiries:
            if (datetime(e.year, e.month, e.day, 15, 30, tzinfo=IST) - now) >= timedelta(days=MIN_DTE):
                return e
        raise FnoError(f"no expiry of {symbol} with at least {MIN_DTE:.0f} days left")

    def view(self, symbol: str, now: datetime, tone: str | None = None, vix: float | None = None,
             vix_pct: float | None = None, negative_news=(), prev_pcr: float | None = None,
             outperf: float | None = None, expiry: date | None = None) -> View:
        symbol = symbol.upper()
        expiry = expiry or self.pick_expiry(symbol, now)
        ch = self.fno.chain(symbol, expiry)
        if symbol in INDEX_SYMBOLS:
            day_pct = self.data.index_quote(INDEX_SYMBOLS[symbol]).pct
        else:
            day_pct = self.data.stock_quote(symbol).pct
        futs = [f for f in self.fno.futures([symbol]).get(symbol, []) if (f.expiry - now.date()).days >= MIN_DTE]
        st = self.stats(symbol, now.date())
        dte = max((datetime(expiry.year, expiry.month, expiry.day, 15, 30, tzinfo=IST) - now).total_seconds() / 86400, 0.0)
        brain = self.brain(symbol, now.date(), dte) or {}
        bias, event = self.headlines(symbol, now)
        journal = self.journal_stats()
        ctx = Ctx(symbol, symbol in INDEX_SYMBOLS, day_pct, st["sma50"], st["atr"], tone, vix, vix_pct, outperf,
                  tuple(negative_news), self.fno.lot_size(symbol, now.date()), self.cfg.capital, self.cfg.risk_pct,
                  self.cfg.broker, prev_pcr, self.spurts().get(symbol), brain.get("chart"), brain.get("analog"),
                  brain.get("rets"), brain.get("rv_ann"), bias["bias"], tuple(bias["lines"]), event, journal,
                  getattr(self.cfg, "fno_nondirectional", True))
        view = analyse(ch, futs[0] if futs else None, ctx, now)
        view.stale = not (ch.ts and ch.ts.date() == now.date() and now - ch.ts <= timedelta(minutes=25))
        view.lot = ctx.lot
        view.source = ch.source
        view.track = journal
        return view
