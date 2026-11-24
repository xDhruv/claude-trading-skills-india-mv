"""Free news from public RSS feeds: fetch, dedupe, score by market relevance, and veto trade ideas on bad news."""
import hashlib
import html
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

MARKET_WORDS = {
    "rbi": 2, "repo rate": 3, "rate cut": 3, "rate hike": 3, "fomc": 3, "fed ": 2, "federal reserve": 2,
    "budget": 2, "sebi": 2, "tariff": 3, "crude": 2, "ceasefire": 2, "war": 2, "gdp": 2, "cpi": 2,
    "inflation": 2, "fii": 2, "fpi": 2, "sell-off": 3, "selloff": 3, "crash": 3, "plunge": 3, "rally": 2,
    "record high": 2, "circuit": 2, "gst": 1, "nifty": 1, "sensex": 1, "rupee": 1, "geopolitical": 2,
    "election": 2, "default": 2, "downgrade": 2, "upgrade": 1, "results": 1, "block deal": 1,
}
NEGATIVE_WORDS = ("fraud", "probe", "raid", "penalty", "sebi order", "default", "resigns", "resignation",
                  "downgrade", "plunge", "slump", "scam", "insolvency", "bankruptcy", "ban ", "show-cause",
                  "profit warning", "misses estimates", "cuts guidance", "recall", "fire at", "accident")

ALIASES = {
    "RELIANCE": ["reliance"], "TCS": ["tcs", "tata consultancy"], "HDFCBANK": ["hdfc bank"],
    "ICICIBANK": ["icici bank"], "INFY": ["infosys"], "SBIN": ["sbi", "state bank"], "BHARTIARTL": ["airtel"],
    "ITC": ["itc "], "LT": ["larsen"], "AXISBANK": ["axis bank"], "KOTAKBANK": ["kotak"],
    "MARUTI": ["maruti"], "SUNPHARMA": ["sun pharma"], "TATASTEEL": ["tata steel"], "M&M": ["mahindra"],
    "BAJFINANCE": ["bajaj finance"], "HCLTECH": ["hcl tech", "hcltech"], "NTPC": ["ntpc"],
    "POWERGRID": ["power grid"], "ONGC": ["ongc"], "ADANIPORTS": ["adani ports"], "TITAN": ["titan"],
    "ULTRACEMCO": ["ultratech"], "COALINDIA": ["coal india"], "JSWSTEEL": ["jsw steel"],
}


@dataclass
class Item:
    title: str
    link: str
    source: str
    published: datetime | None
    score: int = 0
    symbols: tuple = ()

    @property
    def key(self) -> str:
        return hashlib.sha1((self.link or self.title).encode()).hexdigest()[:16]


def _get(url: str, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 nse-agent/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_rss(xml_bytes: bytes, source: str = "") -> list[Item]:
    root = ET.fromstring(xml_bytes)
    out = []
    for it in root.iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        link = (it.findtext("link") or "").strip()
        pub = None
        raw = it.findtext("pubDate")
        if raw:
            try:
                pub = parsedate_to_datetime(raw)
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pub = None
        if title:
            out.append(Item(title, link, source or urllib.parse.urlparse(link).netloc, pub))
    return out


def fetch_feeds(urls) -> list[Item]:
    items: list[Item] = []
    for url in urls:
        try:
            items.extend(parse_rss(_get(url), urllib.parse.urlparse(url).netloc))
        except Exception:  # one broken feed must not stop the others
            continue
    return items


def score(item: Item, symbols) -> Item:
    t = " " + item.title.lower() + " "
    s = sum(w for k, w in MARKET_WORDS.items() if k in t)
    hit = []
    for sym in symbols:
        names = ALIASES.get(sym, []) + [sym.lower()]
        if any((n in t) if n.endswith(" ") or " " in n else re.search(rf"\b{re.escape(n)}\b", t) for n in names):
            hit.append(sym)
    item.score = s + 3 * len(hit)
    item.symbols = tuple(hit)
    return item


def fresh_relevant(items, seen: set, symbols, now: datetime, max_age_h: float = 6, min_score: int = 3) -> list[Item]:
    out, keys = [], set()
    for it in items:
        if it.key in seen or it.key in keys:
            continue
        if it.published and now - it.published > timedelta(hours=max_age_h):
            continue
        keys.add(it.key)
        score(it, symbols)
        if it.score >= min_score:
            out.append(it)
    return sorted(out, key=lambda i: -i.score)


def symbol_news(symbol: str, now: datetime, hours: float = 36) -> list[Item]:
    """Recent headlines about one stock from Google News RSS."""
    q = urllib.parse.quote(f'"{ALIASES.get(symbol, [symbol.lower()])[0].strip()}" stock when:2d')
    url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        items = parse_rss(_get(url))
    except Exception:
        return []
    return [i for i in items if not i.published or now - i.published <= timedelta(hours=hours)][:8]


def is_negative(item: Item) -> bool:
    t = item.title.lower() + " "
    return any(w in t for w in NEGATIVE_WORDS)
