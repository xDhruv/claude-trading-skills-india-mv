"""Futures and options from Angel One's SmartAPI: free with an Angel One account, official and near real time.

Same interface as `NseFno` (expiries, chain, futures, oi_spurts, lot_size) so the rest of the agent does not care
which source is active. Needs four secrets in agent/.env: ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN and
ANGEL_TOTP_SECRET (the text key shown next to the QR code when you enable TOTP). Read-only: no order endpoint is
ever called. Credentials are only sent to apiconnect.angelone.in.
"""
import base64
import hashlib
import hmac
import json
import re
import struct
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from .clock import IST, now_ist, parse_nse_ts
from .fno_analysis import bs_price
from .fno_data import INDEX_SYMBOLS, Chain, FnoError, Future, Leg

ROOT = "https://apiconnect.angelone.in"
MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
LOGIN = "/rest/auth/angelbroking/user/v1/loginByPassword"
QUOTE = "/rest/secure/angelbroking/market/v1/quote/"
GREEKS = "/rest/secure/angelbroking/marketData/v1/optionGreek"
BUILDUP = "/rest/secure/angelbroking/marketData/v1/OIBuildup"
OI_HISTORY = "/rest/secure/angelbroking/historical/v1/getOIData"
INDEX_TOKENS = {"NIFTY": "99926000", "BANKNIFTY": "99926009"}
SOURCE = "Angel One SmartAPI"
BATCH = 50  # tokens per quote request
STRIKES_EACH_SIDE = 30
OI_WINDOW = 6  # strikes each side of the money that get a change-in-OI figure
GAPS = {"quote": 0.15, "optionGreek": 1.1, "OIBuildup": 1.1, "getOIData": 0.4}  # min seconds between calls
FUT_RE = re.compile(r"\d{2}[A-Z]{3}\d{2}FUT$")


def totp(secret: str, at: float | None = None, step: int = 30, digits: int = 6) -> str:
    """RFC 6238 time-based one-time password (what an authenticator app shows)."""
    s = secret.replace(" ", "").upper()
    key = base64.b32decode(s + "=" * (-len(s) % 8))
    mac = hmac.new(key, struct.pack(">Q", int((time.time() if at is None else at) // step)), hashlib.sha1).digest()
    off = mac[-1] & 15
    return f"{(struct.unpack('>I', mac[off:off + 4])[0] & 0x7FFFFFFF) % 10 ** digits:0{digits}d}"


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


class AngelSession:
    """Logs in with client code + PIN + TOTP, keeps the token, paces calls to the documented rate limits."""

    def __init__(self, api_key: str, client_code: str, pin: str, totp_secret: str, root: str = ROOT,
                 timeout: float = 30, opener=None, clock=time.time, sleep=time.sleep):
        self.api_key, self.client_code, self.pin, self.secret = api_key, client_code, pin, totp_secret
        self.root, self.timeout, self.clock, self.sleep = root, timeout, clock, sleep
        self.opener = opener or urllib.request.build_opener()
        self._token = ""
        self._token_at = 0.0
        self._failed_at = 0.0
        self._last: dict[str, float] = {}
        self._mac = ":".join(re.findall("..", f"{uuid.getnode():012x}"))

    def _send(self, path: str, body: dict, auth: bool = True) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json", "Accept": "application/json", "X-UserType": "USER",
                   "X-SourceID": "WEB", "X-ClientLocalIP": "127.0.0.1", "X-ClientPublicIP": "127.0.0.1",
                   "X-MACAddress": self._mac, "X-PrivateKey": self.api_key}
        if auth and self._token:
            headers["Authorization"] = "Bearer " + self._token
        req = urllib.request.Request(self.root + path, json.dumps(body).encode(), headers, method="POST")
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                code, raw = r.status, r.read()
        except urllib.error.HTTPError as exc:
            code, raw = exc.code, exc.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise FnoError(f"Angel One unreachable: {exc}") from exc
        try:
            data = json.loads(raw)
        except ValueError:
            data = {}
        return code, data if isinstance(data, dict) else {}

    def login(self) -> None:
        if self._failed_at and self.clock() - self._failed_at < 300:
            raise FnoError("Angel One login failed a moment ago; not retrying for 5 minutes")
        self._pace("login", 1.1)
        _, d = self._send(LOGIN, {"clientcode": self.client_code, "password": self.pin,
                                  "totp": totp(self.secret, self.clock())}, auth=False)
        token = (d.get("data") or {}).get("jwtToken")
        if not (d.get("status") and token):
            self._failed_at = self.clock()
            raise FnoError(f"Angel One login failed: {d.get('message') or 'no token returned'} "
                           "(check ANGEL_CLIENT_CODE, ANGEL_PIN, ANGEL_TOTP_SECRET and ANGEL_API_KEY)")
        self._token, self._token_at, self._failed_at = token, self.clock(), 0.0

    def _pace(self, name: str, gap: float) -> None:
        wait = self._last.get(name, 0.0) + gap - self.clock()
        if wait > 0:
            self.sleep(wait)
        self._last[name] = self.clock()

    def call(self, path: str, body: dict):
        name = path.rstrip("/").rsplit("/", 1)[-1]
        for attempt in range(3):
            if not self._token or self.clock() - self._token_at > 8 * 3600:
                self.login()
            self._pace(name, GAPS.get(name, 0.2))
            code, d = self._send(path, body)
            msg = str(d.get("message") or "")
            if code == 200 and d.get("status", d.get("success")):
                return d.get("data")
            if d.get("error_type") == "TokenException" or code == 401 or "token" in msg.lower():
                self._token = ""
                continue
            if code == 403 or "access denied" in msg.lower() or "rate" in msg.lower():
                self.sleep(1.5 * (attempt + 1))
                continue
            raise FnoError(f"Angel One {name} failed: {msg or code}")
        raise FnoError(f"Angel One {name} failed after retries")


def implied_vol(kind: str, spot: float, strike: float, t: float, price: float) -> float:
    """IV in percent by bisection; 0 when the price is below intrinsic or unsolvable."""
    intrinsic = max(spot - strike, 0.0) if kind == "C" else max(strike - spot, 0.0)
    if price <= intrinsic + 0.01 or t <= 0:
        return 0.0
    lo, hi = 1.0, 300.0
    if bs_price(kind, spot, strike, t, hi) < price:
        return 0.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if bs_price(kind, spot, strike, t, mid) < price else (lo, mid)
    return round((lo + hi) / 2, 2)


def _download_master() -> list[dict]:
    try:
        with urllib.request.urlopen(MASTER_URL, timeout=180) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise FnoError(f"Angel One instrument list download failed: {exc}") from exc


def _slim(rows: list[dict], today: date) -> dict:
    """Keep only what is needed: live NFO derivatives plus the NSE cash token of each stock."""
    deriv, eq = [], {}
    for r in rows:
        if r.get("exch_seg") == "NSE" and str(r.get("symbol", "")).endswith("-EQ"):
            eq[r["name"]] = r["token"]
        elif r.get("exch_seg") == "NFO" and r.get("instrumenttype") in ("FUTIDX", "OPTIDX", "FUTSTK", "OPTSTK"):
            try:
                exp = datetime.strptime(r["expiry"], "%d%b%Y").date()
            except ValueError:
                continue
            if exp >= today:
                deriv.append([r["token"], r["symbol"], r["name"], exp.isoformat(), _f(r["strike"]) / 100,
                              int(_f(r["lotsize"])), r["instrumenttype"]])
    return {"deriv": deriv, "eq": eq}


class AngelFno:
    def __init__(self, session: AngelSession, cache_dir: str | Path | None = None, fetch_master=None):
        self.s = session
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.fetch_master = fetch_master or _download_master
        self._m: dict | None = None
        self._m_day: date | None = None
        self._prev: dict[tuple, float | None] = {}
        self._oi_off_until = 0.0
        self._fut_cache: dict[str, tuple[float, list[Future]]] = {}
        self._greek_cache: dict[tuple, tuple[float, dict]] = {}

    @staticmethod
    def is_index(symbol: str) -> bool:
        return symbol in INDEX_SYMBOLS

    # ---- instruments ---------------------------------------------------------------------------
    def _master(self) -> dict:
        today = now_ist().date()
        if self._m is not None and self._m_day == today:
            return self._m
        path = self.cache_dir / f"angel_master_{today}.json" if self.cache_dir else None
        slim = None
        if path and path.exists():
            try:
                slim = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                slim = None
        if slim is None:
            slim = _slim(self.fetch_master(), today)
            if path:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    for old in path.parent.glob("angel_master_*.json"):
                        old.unlink(missing_ok=True)
                    path.write_text(json.dumps(slim), encoding="utf-8")
                except OSError:
                    pass
        opt: dict = {}
        fut: dict = {}
        lot: dict = {}
        for token, sym, name, exp, strike, lots, kind in slim["deriv"]:
            e = date.fromisoformat(exp)
            lot[name] = lots
            if kind in ("OPTIDX", "OPTSTK"):
                opt.setdefault((name, e), []).append((strike, sym[-2:], token))
            else:
                fut.setdefault(name, []).append((e, token))
        for lst in fut.values():
            lst.sort()
        self._m, self._m_day = {"opt": opt, "fut": fut, "lot": lot, "eq": slim["eq"]}, today
        return self._m

    def expiries(self, symbol: str) -> list[date]:
        found = sorted(e for (name, e) in self._master()["opt"] if name == symbol.upper())
        if not found:
            raise FnoError(f"{symbol} has no listed options on Angel One's instrument list")
        return found

    def lot_size(self, symbol: str, today: date | None = None) -> int | None:
        return self._master()["lot"].get(symbol.upper())

    # ---- quotes --------------------------------------------------------------------------------
    def _quotes(self, exchange: str, tokens: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(tokens), BATCH):
            data = self.s.call(QUOTE, {"mode": "FULL", "exchangeTokens": {exchange: tokens[i:i + BATCH]}})
            for r in (data or {}).get("fetched", []):
                out[str(r["symbolToken"])] = r
        return out

    def _spot(self, symbol: str) -> dict:
        token = INDEX_TOKENS.get(symbol) or self._master()["eq"].get(symbol)
        if not token:
            raise FnoError(f"no cash instrument found for {symbol}")
        q = self._quotes("NSE", [token]).get(token)
        if not q or not _f(q.get("ltp")):
            raise FnoError(f"no spot price for {symbol}")
        return q

    @staticmethod
    def _stamp(q: dict) -> datetime | None:
        return parse_nse_ts(q.get("exchFeedTime") or q.get("exchTradeTime"))

    # ---- change in OI (previous session's close from daily OI history) -------------------------
    def _prev_oi(self, token: str, trade_day: date) -> float | None:
        key = (token, trade_day)
        if key in self._prev:
            return self._prev[key]
        rows = self.s.call(OI_HISTORY, {
            "exchange": "NFO", "symboltoken": token, "interval": "ONE_DAY",
            "fromdate": (trade_day - timedelta(days=10)).strftime("%Y-%m-%d 09:15"),
            "todate": trade_day.strftime("%Y-%m-%d 15:30")})
        prev = None
        for r in rows or []:
            if str(r.get("time", ""))[:10] < trade_day.isoformat():
                prev = _f(r.get("oi"))
        self._prev[key] = prev
        return prev

    def _iv_map(self, symbol: str, expiry: date) -> dict:
        key = (symbol, expiry)
        hit = self._greek_cache.get(key)
        if hit and time.time() - hit[0] < 300:
            return hit[1]
        try:
            data = self.s.call(GREEKS, {"name": symbol, "expirydate": expiry.strftime("%d%b%Y").upper()})
        except FnoError:
            return {}
        out = {(_f(r.get("strikePrice")), str(r.get("optionType"))): _f(r.get("impliedVolatility")) for r in data or []}
        self._greek_cache[key] = (time.time(), out)
        return out

    # ---- public API ----------------------------------------------------------------------------
    def chain(self, symbol: str, expiry: date) -> Chain:
        symbol = symbol.upper()
        contracts = self._master()["opt"].get((symbol, expiry))
        if not contracts:
            raise FnoError(f"no {symbol} options expiring {expiry} on Angel One's instrument list")
        sq = self._spot(symbol)
        spot = _f(sq["ltp"])
        strikes = sorted({k for k, _, _ in contracts})
        keep = set(sorted(strikes, key=lambda k: abs(k - spot))[:2 * STRIKES_EACH_SIDE + 1])
        wanted = [(k, kind, tok) for k, kind, tok in contracts if k in keep]
        quotes = self._quotes("NFO", [tok for _, _, tok in wanted])
        stamps = [x for x in (self._stamp(sq), *(self._stamp(q) for q in quotes.values())) if x]
        ts = max(stamps) if stamps else None
        trade_day = ts.date() if ts else now_ist().date()
        ivs = self._iv_map(symbol, expiry)
        atm = min(keep, key=lambda k: abs(k - spot))
        step = min((b - a for a, b in zip(sorted(keep), sorted(keep)[1:])), default=0)
        t = max((datetime(expiry.year, expiry.month, expiry.day, 15, 30, tzinfo=IST) - now_ist()).total_seconds()
                / 86400 / 365, 1e-6)
        rows: dict[float, list] = {}
        for k, kind, tok in wanted:
            q = quotes.get(tok)
            if not q:
                continue
            ltp, oi, vol = _f(q.get("ltp")), _f(q.get("opnInterest")), _f(q.get("tradeVolume"))
            if not (ltp or oi or vol):
                continue
            depth = q.get("depth") or {}
            bid = _f((depth.get("buy") or [{}])[0].get("price"))
            ask = _f((depth.get("sell") or [{}])[0].get("price"))
            iv = ivs.get((k, kind)) or implied_vol("C" if kind == "CE" else "P", spot, k, t, ltp)
            doi = 0.0
            if step and abs(k - atm) <= OI_WINDOW * step and time.time() >= self._oi_off_until:
                try:
                    prev = self._prev_oi(tok, trade_day)
                    doi = oi - prev if prev is not None else 0.0
                except FnoError:
                    self._oi_off_until = time.time() + 1800
            rows.setdefault(k, [None, None])[0 if kind == "CE" else 1] = Leg(ltp, oi, doi, iv, vol, bid, ask)
        if not rows:
            raise FnoError(f"empty option chain for {symbol} {expiry}")
        return Chain(symbol, expiry, spot, ts, [(k, ce, pe) for k, (ce, pe) in sorted(rows.items())], SOURCE)

    def futures(self, symbols=None) -> dict[str, list[Future]]:
        out: dict[str, list[Future]] = {}
        today = now_ist().date()
        for sym in (s.upper() for s in (symbols or INDEX_SYMBOLS)):
            hit = self._fut_cache.get(sym)
            if hit and time.time() - hit[0] < 120:
                out[sym] = hit[1]
                continue
            contracts = [(e, tok) for e, tok in self._master()["fut"].get(sym, []) if e >= today]
            if not contracts:
                continue
            quotes = self._quotes("NFO", [tok for _, tok in contracts])
            try:
                spot = _f(self._spot(sym).get("ltp"))
            except FnoError:
                spot = 0.0
            futs = [Future(sym, e, _f(q.get("ltp")), _f(q.get("percentChange")), _f(q.get("open")), _f(q.get("high")),
                           _f(q.get("low")), _f(q.get("close")), _f(q.get("opnInterest")), _f(q.get("tradeVolume")), spot)
                    for e, tok in contracts if (q := quotes.get(tok))]
            self._fut_cache[sym] = (time.time(), futs)
            out[sym] = futs
        return out

    def oi_spurts(self) -> dict[str, dict]:
        """Open-interest change of the near-month future by underlying (stocks from Angel's buildup lists)."""
        out: dict[str, dict] = {}
        for kind in ("Long Built Up", "Short Built Up", "Short Covering", "Long Unwinding"):
            try:
                rows = self.s.call(BUILDUP, {"expirytype": "NEAR", "datatype": kind})
            except FnoError:
                continue
            for r in rows or []:
                oi, change = _f(r.get("opnInterest")), _f(r.get("netChangeOpnInterest"))
                prev = oi - change
                name = FUT_RE.sub("", str(r.get("tradingSymbol", "")))
                if name and prev > 0:
                    out[name] = {"oi": oi, "prev": prev, "change": change, "pct": change / prev * 100}
        for sym in INDEX_SYMBOLS:
            if sym in out:
                continue
            try:
                fut = self.futures([sym]).get(sym)
                token = next(tok for e, tok in self._master()["fut"][sym] if fut and e == fut[0].expiry)
                trade_day = (self._stamp(self._spot(sym)) or now_ist()).date()
                prev = self._prev_oi(token, trade_day)
            except (FnoError, StopIteration, KeyError):
                continue
            if prev:
                out[sym] = {"oi": fut[0].oi, "prev": prev, "change": fut[0].oi - prev,
                            "pct": (fut[0].oi - prev) / prev * 100}
        return out
