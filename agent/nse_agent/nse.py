"""Typed wrappers over the two NSE MCP servers (CM Market Live and Bhavcopy)."""
import re
from dataclasses import dataclass
from datetime import date, datetime

from .clock import parse_nse_ts
from .mcp import McpClient, McpError

_UNITS = {"K": 1e3, "L": 1e5, "CR": 1e7}


def parse_indian(text) -> float:
    """'1,248.68 L' -> 124868000.0, '474.25 Cr' -> 4742500000.0, '44.18 K' -> 44180.0."""
    if isinstance(text, (int, float)):
        return float(text)
    m = re.match(r"^\s*([\d,.]+)\s*([A-Za-z]*)\s*$", str(text))
    if not m:
        return 0.0
    return float(m.group(1).replace(",", "")) * _UNITS.get(m.group(2).upper(), 1.0)


@dataclass
class Quote:
    symbol: str
    series: str
    ltp: float
    open: float
    high: float
    low: float
    prev: float
    pct: float
    volume: float
    high52: float
    low52: float
    ts: datetime | None


@dataclass
class IndexQuote:
    name: str
    last: float
    prev: float
    pct: float
    open: float
    high: float
    low: float
    ts: datetime | None


@dataclass
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Mover:
    symbol: str
    series: str
    ltp: float
    pct: float
    turnover_cr: float


class NseData:
    def __init__(self, cm: McpClient, bhav: McpClient):
        self.cm, self.bhav = cm, bhav

    def stock_quote(self, symbol: str) -> Quote:
        s = self.cm.call("cm_get_stock_quote", {"symbol": symbol})["stock"]
        return Quote(
            symbol=s["symbol"], series=s.get("series", ""), ltp=float(s["lastTradedPrice"]),
            open=float(s["openPrice"]), high=float(s["highPrice"]), low=float(s["lowPrice"]),
            prev=float(s["preClosePrice"]), pct=float(s["perChange"]), volume=float(s["volume"]),
            high52=float(s.get("fiftyTwoWeekHigh") or 0), low52=float(s.get("fiftyTwoWeekLow") or 0),
            ts=parse_nse_ts(s.get("latestTimestamp")),
        )

    def index_quote(self, name: str) -> IndexQuote:
        d = self.cm.call("cm_get_index_quote", {"indexName": name})
        i = d["index"]
        return IndexQuote(
            name=i["indexName"], last=float(i["last"]), prev=float(i["previousClose"]),
            pct=float(i["percChange"]), open=float(i["open"]), high=float(i["high"]), low=float(i["low"]),
            ts=parse_nse_ts(d.get("nseTimestamp")),
        )

    def movers(self, index: str = "", limit: int = 25) -> tuple[list[Mover], list[Mover]]:
        d = self.cm.call("nse_get_market_movers", {"indexName": index, "limit": limit})

        def conv(rows):
            return [Mover(r["symbol"], r.get("series", ""), float(r["ltp"]), float(r["perChange"]),
                          parse_indian(r.get("turnover", 0)) / 1e7) for r in rows]
        return conv(d.get("gainers", [])), conv(d.get("losers", []))

    def history(self, symbol: str, end: date, months: int = 3) -> list[Bar]:
        d = self.bhav.call("get_stock_history", {"symbol": symbol, "months": months, "endDate": end.isoformat()})
        return [Bar(r["date"], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                    float(r["volume"])) for r in d.get("data", [])]

    def index_history(self, name: str, end: date, months: int = 3) -> list[Bar]:
        d = self.bhav.call("get_index_history", {"indexName": name, "months": months, "endDate": end.isoformat()})
        return [Bar(r["date"], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                    float(r.get("volume") or 0)) for r in d.get("data", [])]

    def sma(self, symbol: str, days: int) -> float | None:
        v = self.bhav.call("moving_average", {"symbol": symbol, "days": days})
        return float(v) if isinstance(v, (int, float)) else None

    def corporate_actions(self, symbol: str, start: date, end: date) -> list[dict]:
        d = self.bhav.call("get_corporate_actions", {"symbol": symbol, "fromDate": start.isoformat(),
                                                    "toDate": end.isoformat()})
        return d.get("actions", []) if isinstance(d, dict) else []

    def outperformance(self, symbol: str, end: date, months: int = 3) -> float | None:
        d = self.bhav.call("get_stock_vs_index", {"symbol": symbol, "indexName": "NIFTY 50", "months": months,
                                                  "date": end.isoformat()})
        return d.get("outperformance_pct_points") if isinstance(d, dict) else None

    def mood(self, day: date) -> dict:
        return self.bhav.call("get_market_mood", {"date": day.isoformat()})

    def breadth(self, day: date) -> dict:
        return self.bhav.call("get_market_breadth", {"date": day.isoformat()})

    def index_movers(self, day: date, period: str = "1D", n: int = 3) -> dict:
        return self.bhav.call("get_index_movers", {"date": day.isoformat(), "period": period, "n": n,
                                                  "scope": "sectoral"})

    def sectoral(self) -> list[IndexQuote]:
        d = self.cm.call("cm_get_live_indices", {"group": "sectoral", "nameFilter": ""})
        rows = (d.get("indices") or {}).get("sectoral", []) if isinstance(d, dict) else []
        out = []
        for r in rows:
            try:
                out.append(IndexQuote(r["indexName"], float(r["last"]), float(r["previousClose"]),
                                      float(r["percChange"]), float(r.get("open", 0)), float(r.get("high", 0)),
                                      float(r.get("low", 0)), parse_nse_ts(d.get("nseTimestamp"))))
            except (KeyError, TypeError, ValueError):
                continue
        return out


__all__ = ["NseData", "Quote", "IndexQuote", "Bar", "Mover", "McpError", "parse_indian"]
