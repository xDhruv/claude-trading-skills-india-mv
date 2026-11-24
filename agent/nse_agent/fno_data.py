"""Futures and options data from NSE's public website JSON (no login, no key).

This is not an official API: the endpoints are the ones nseindia.in's own option-chain pages use. They are
delayed, can change without notice, and may block cloud IPs (so run this from home or a VM, not GitHub
Actions). Use it for personal, low-frequency analysis only.
"""
import csv
import http.cookiejar
import io
import time
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime

from .clock import parse_nse_ts

BASE = "https://www.nseindia.in"
ARCHIVE = "https://nsearchives.nseindia.in"
INDEX_SYMBOLS = {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK"}  # symbol -> NSE CM MCP index name
FUT_FEEDS = {"NIFTY": "nse50_fut", "BANKNIFTY": "nifty_bank_fut"}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")


class FnoError(Exception):
    pass


@dataclass
class Leg:
    ltp: float
    oi: float
    doi: float
    iv: float
    volume: float
    bid: float
    ask: float


@dataclass
class Chain:
    symbol: str
    expiry: date
    spot: float
    ts: datetime | None
    rows: list  # [(strike, Leg | None, Leg | None)] sorted by strike
    source: str = "NSE website"


@dataclass
class Future:
    symbol: str
    expiry: date
    ltp: float
    pct: float
    open: float
    high: float
    low: float
    prev: float
    oi: float
    volume: float
    spot: float


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _day(text: str) -> date:
    return datetime.strptime(text, "%d-%b-%Y").date()


def _leg(d: dict | None) -> Leg | None:
    if not d:
        return None
    leg = Leg(_f(d.get("lastPrice")), _f(d.get("openInterest")), _f(d.get("changeinOpenInterest")),
              _f(d.get("impliedVolatility")), _f(d.get("totalTradedVolume")),
              _f(d.get("buyPrice1")), _f(d.get("sellPrice1")))
    return leg if (leg.ltp or leg.oi or leg.volume) else None


class NseWeb:
    """Cookie-holding HTTP session the way a browser visits the option-chain page."""

    def __init__(self, base: str = BASE, pause: float = 0.6, timeout: float = 45):
        self.base, self.pause, self.timeout = base, pause, timeout
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._warmed = 0.0

    def _headers(self, accept: str) -> dict:
        return {"User-Agent": UA, "Accept": accept, "Accept-Language": "en-US,en;q=0.9",
                "Referer": f"{self.base}/option-chain"}

    def _open(self, url: str, accept: str) -> bytes:
        req = urllib.request.Request(url, headers=self._headers(accept))
        with self.opener.open(req, timeout=self.timeout) as r:
            return r.read()

    def warm(self) -> None:
        try:
            self._open(f"{self.base}/option-chain", "text/html,*/*")
        except (urllib.error.URLError, TimeoutError):
            pass
        self._warmed = time.time()

    def json(self, path: str):
        if time.time() - self._warmed > 600:
            self.warm()
        last: Exception | None = None
        for attempt in range(3):
            try:
                data = json.loads(self._open(self.base + path, "application/json, text/plain, */*"))
                time.sleep(self.pause)
                return data
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code in (401, 403):
                    self.warm()
                elif exc.code == 404:
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last = exc
            time.sleep(1.5 * (attempt + 1))
        raise FnoError(f"NSE web {path.split('?')[0]} failed: {last}")

    def text(self, url: str) -> str:
        try:
            return self._open(url, "text/csv,*/*").decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise FnoError(f"download failed: {exc}") from exc


class NseFno:
    def __init__(self, web: NseWeb | None = None):
        self.web = web or NseWeb()
        self._lots: dict[str, int] | None = None
        self._lots_day: date | None = None
        self._fut_cache: tuple[float, dict] | None = None

    @staticmethod
    def is_index(symbol: str) -> bool:
        return symbol in INDEX_SYMBOLS

    def expiries(self, symbol: str) -> list[date]:
        d = self.web.json("/api/option-chain-contract-info?symbol=" + urllib.parse.quote(symbol))
        return sorted(_day(x) for x in d.get("expiryDates", []))

    def chain(self, symbol: str, expiry: date) -> Chain:
        kind = "Indices" if self.is_index(symbol) else "Equity"
        path = (f"/api/option-chain-v3?type={kind}&symbol={urllib.parse.quote(symbol)}"
                f"&expiry={expiry.strftime('%d-%b-%Y')}")
        d = self.web.json(path)
        rec = d.get("records", {})
        rows = []
        for r in rec.get("data", []):
            ce, pe = _leg(r.get("CE")), _leg(r.get("PE"))
            if ce or pe:
                rows.append((float(r["strikePrice"]), ce, pe))
        if not rows:
            raise FnoError(f"empty option chain for {symbol} {expiry}")
        rows.sort(key=lambda x: x[0])
        return Chain(symbol, expiry, _f(rec.get("underlyingValue")), parse_nse_ts(rec.get("timestamp")), rows)

    def futures(self, symbols=None) -> dict[str, list[Future]]:
        """Futures by underlying. The stock-futures feed only lists the most active contracts (symbols is ignored)."""
        if self._fut_cache and time.time() - self._fut_cache[0] < 120:
            return self._fut_cache[1]
        out: dict[str, list[Future]] = {}
        rows = []
        for feed in (*FUT_FEEDS.values(), "stock_fut"):
            try:
                rows += self.web.json(f"/api/liveEquity-derivatives?index={feed}").get("data", [])
            except FnoError:
                continue
        seen = set()
        for r in rows:
            if r.get("instrumentType") not in ("FUTIDX", "FUTSTK") or r["identifier"] in seen:
                continue
            seen.add(r["identifier"])
            out.setdefault(r["underlying"], []).append(Future(
                r["underlying"], _day(r["expiryDate"]), _f(r.get("lastPrice")), _f(r.get("pChange")),
                _f(r.get("openPrice")), _f(r.get("highPrice")), _f(r.get("lowPrice")), _f(r.get("closePrice")),
                _f(r.get("openInterest")), _f(r.get("volume")), _f(r.get("underlyingValue"))))
        for lst in out.values():
            lst.sort(key=lambda f: f.expiry)
        self._fut_cache = (time.time(), out)
        return out

    def oi_spurts(self) -> dict[str, dict]:
        """Open-interest change by underlying (futures + options), with percent change."""
        d = self.web.json("/api/live-analysis-oi-spurts-underlyings")
        out = {}
        for r in d.get("data", []):
            prev = _f(r.get("prevOI"))
            out[r["symbol"]] = {"oi": _f(r.get("latestOI")), "prev": prev, "change": _f(r.get("changeInOI")),
                                "pct": (_f(r.get("changeInOI")) / prev * 100) if prev else 0.0}
        return out

    def lot_size(self, symbol: str, today: date | None = None) -> int | None:
        today = today or date.today()
        if self._lots is None or self._lots_day != today:
            lots: dict[str, int] = {}
            try:
                text = self.web.text(f"{ARCHIVE}/content/fo/fo_mktlots.csv")
            except FnoError:
                text = ""
            for row in csv.reader(io.StringIO(text)):
                row = [c.strip() for c in row]
                if len(row) < 3 or row[1].upper() in ("SYMBOL", ""):
                    continue
                first = next((c for c in row[2:] if c.isdigit()), None)
                if first:
                    lots[row[1].upper()] = int(first)
            self._lots, self._lots_day = lots, today
        return self._lots.get(symbol.upper())
