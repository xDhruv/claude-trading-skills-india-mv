"""Local stdio MCP server that exposes NSE futures & options to Claude (or any MCP client).

Run: python -m nse_agent.fno_mcp     (from the agent folder). Data comes from NSE's public website endpoints and,
for the verdict, the official NSE cash-market MCP. Read-only: it never places orders.
"""
import json
import sys
from datetime import datetime

from . import clock
from .config import Config
from .fno_data import FnoError
from .fno_render import render_view
from .fno_service import FnoService
from .fno_source import make_fno
from .mcp import McpClient, McpError
from .nse import NseData

PROTOCOL = "2024-11-05"
SYMBOL = {"type": "string", "description": "NIFTY, BANKNIFTY or an F&O stock symbol such as RELIANCE"}
TOOLS = [
    {"name": "fno_view",
     "description": "Full futures & options verdict for one underlying: bullish/bearish/neutral with score, confidence, an "
                    "option or futures trade plan sized to your risk, OI levels and the evidence graph. Educational, delayed data.",
     "inputSchema": {"type": "object", "properties": {"symbol": SYMBOL, "format": {"type": "string", "enum": ["text", "json"]}},
                     "required": ["symbol"]}},
    {"name": "fno_option_chain",
     "description": "Live option chain around the money (OI, change in OI, IV, volume, last price) for the chosen or nearest expiry.",
     "inputSchema": {"type": "object", "properties": {
         "symbol": SYMBOL, "expiry": {"type": "string", "description": "DD-Mon-YYYY; default is the nearest with 2+ days left"},
         "strikes": {"type": "integer", "description": "strikes each side of the money (default 8)"}}, "required": ["symbol"]}},
    {"name": "fno_futures",
     "description": "Futures quotes with price change and open interest; one underlying or the most active list.",
     "inputSchema": {"type": "object", "properties": {"symbol": SYMBOL}}},
    {"name": "fno_oi_spurts",
     "description": "Underlyings with the biggest open-interest change today (futures + options).",
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
]


class Backend:
    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or Config.from_env()
        self.fno = make_fno(self.cfg)
        self.data = NseData(McpClient(self.cfg.cm_url), McpClient(self.cfg.bhav_url))
        self.service = FnoService(self.cfg, self.fno, self.data)


def _view(b: Backend, a: dict) -> str:
    now = clock.now_ist()
    sym = str(a["symbol"]).upper()
    try:
        vix = b.data.index_quote("INDIA VIX")
        v = b.service.view(sym, now, None, vix.last, vix.pct)
    except (McpError, KeyError, TypeError, ValueError):  # the cash MCP is optional context
        v = b.service.view(sym, now)
    if a.get("format") == "json":
        return json.dumps({"symbol": v.symbol, "direction": v.direction, "score": v.score, "confidence": v.confidence,
                           "stale": v.stale, "expiry": v.expiry, "plan": v.plan, "futures_alternative": v.futures_alt,
                           "notes": v.notes, "metrics": v.metrics, "graph": v.graph()}, default=str, indent=1)
    return render_view(v, now)


def _chain(b: Backend, a: dict) -> str:
    now, sym = clock.now_ist(), str(a["symbol"]).upper()
    expiry = datetime.strptime(a["expiry"], "%d-%b-%Y").date() if a.get("expiry") else None
    ch = b.fno.chain(sym, expiry or b.service.pick_expiry(sym, now))
    n = int(a.get("strikes") or 8)
    ks = [k for k, _, _ in ch.rows]
    atm = min(ks, key=lambda k: abs(k - ch.spot))
    i = ks.index(atm)
    out = [f"{sym} {ch.expiry:%d %b %Y} spot {ch.spot:,.2f} ({ch.source} stamp {ch.ts})",
           "strike | CE ltp  oi  d_oi  iv  vol | PE ltp  oi  d_oi  iv  vol"]
    fmt = lambda x: "-" if x is None else f"{x.ltp:.2f} {x.oi:,.0f} {x.doi:+,.0f} {x.iv:.1f} {x.volume:,.0f}"  # noqa: E731
    for k, c, p in ch.rows[max(0, i - n): i + n + 1]:
        out.append(f"{k:,.0f}{'*' if k == atm else ' '} | {fmt(c)} | {fmt(p)}")
    return "\n".join(out)


def _futures(b: Backend, a: dict) -> str:
    sym = str(a.get("symbol") or "").upper()
    today = clock.now_ist().date()
    futs = {s: [f for f in lst if f.expiry >= today] for s, lst in b.fno.futures([sym] if sym else None).items()}
    rows = futs.get(sym, []) if sym else [f for lst in futs.values() for f in lst[:1]]
    return "\n".join(f"{f.symbol} {f.expiry:%d %b %Y}: {f.ltp:,.2f} ({f.pct:+.2f}%), OI {f.oi:,.0f}, volume {f.volume:,.0f}, "
                     f"spot {f.spot:,.2f}" for f in rows) or "No futures found for that symbol."


def _spurts(b: Backend, a: dict) -> str:
    rows = sorted(b.fno.oi_spurts().items(), key=lambda kv: -abs(kv[1]["pct"]))[: int(a.get("limit") or 15)]
    return "\n".join(f"{s}: OI {r['oi']:,.0f} ({r['pct']:+.1f}% / {r['change']:+,.0f})" for s, r in rows)


HANDLERS = {"fno_view": _view, "fno_option_chain": _chain, "fno_futures": _futures, "fno_oi_spurts": _spurts}


def handle(msg: dict, backend: Backend) -> dict | None:
    """One JSON-RPC message in, one response (or None for notifications) out."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None
    reply = lambda **kw: {"jsonrpc": "2.0", "id": mid, **kw}  # noqa: E731
    if method == "initialize":
        return reply(result={"protocolVersion": params.get("protocolVersion") or PROTOCOL, "capabilities": {"tools": {}},
                             "serverInfo": {"name": "nse-fno", "version": "1.0.0"}})
    if method == "ping":
        return reply(result={})
    if method == "tools/list":
        return reply(result={"tools": TOOLS})
    if method == "tools/call":
        fn = HANDLERS.get(params.get("name"))
        if not fn:
            return reply(error={"code": -32602, "message": f"unknown tool {params.get('name')}"})
        try:
            text, error = fn(backend, params.get("arguments") or {}), False
        except (FnoError, McpError, KeyError, ValueError, TypeError) as exc:
            text, error = f"{type(exc).__name__}: {exc}", True
        return reply(result={"content": [{"type": "text", "text": text}], "isError": error})
    return reply(error={"code": -32601, "message": f"method not found: {method}"})


def main() -> int:
    backend = Backend()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            out = handle(json.loads(line), backend)
        except json.JSONDecodeError:
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
