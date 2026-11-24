"""Glue between the data sources and the F&O analysis. Used by the agent loop, inbox replies and the MCP server."""
import time
from datetime import date, datetime, timedelta

from . import analysis
from .clock import IST
from .fno_analysis import MIN_DTE, Ctx, View, analyse
from .fno_data import INDEX_SYMBOLS, FnoError, NseFno


class FnoService:
    def __init__(self, cfg, fno: NseFno, data, cache: dict | None = None):
        self.cfg, self.fno, self.data = cfg, fno, data
        self.cache = cache if cache is not None else {}
        self._spurts: tuple[float, dict] | None = None

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
        ch = self.fno.chain(symbol, expiry or self.pick_expiry(symbol, now))
        if symbol in INDEX_SYMBOLS:
            day_pct = self.data.index_quote(INDEX_SYMBOLS[symbol]).pct
        else:
            day_pct = self.data.stock_quote(symbol).pct
        futs = [f for f in self.fno.futures([symbol]).get(symbol, []) if (f.expiry - now.date()).days >= MIN_DTE]
        st = self.stats(symbol, now.date())
        ctx = Ctx(symbol, symbol in INDEX_SYMBOLS, day_pct, st["sma50"], st["atr"], tone, vix, vix_pct, outperf,
                  tuple(negative_news), self.fno.lot_size(symbol, now.date()), self.cfg.capital, self.cfg.risk_pct,
                  self.cfg.broker, prev_pcr, self.spurts().get(symbol))
        view = analyse(ch, futs[0] if futs else None, ctx, now)
        view.stale = not (ch.ts and ch.ts.date() == now.date() and now - ch.ts <= timedelta(minutes=25))
        view.lot = ctx.lot
        view.source = ch.source
        return view
