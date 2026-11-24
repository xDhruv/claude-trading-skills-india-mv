"""Settings from environment variables and an optional .env file (stdlib only)."""
import os
import pathlib
from dataclasses import dataclass, field
DEFAULT_WATCHLIST = (
    "RELIANCE,TCS,HDFCBANK,ICICIBANK,INFY,SBIN,BHARTIARTL,ITC,LT,AXISBANK,KOTAKBANK,MARUTI,SUNPHARMA,"
    "TATASTEEL,M&M,BAJFINANCE,HCLTECH,NTPC,POWERGRID,ONGC,ADANIPORTS,TITAN,ULTRACEMCO,COALINDIA,JSWSTEEL"
)

DEFAULT_FEEDS = (
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms,"
    "https://www.moneycontrol.com/rss/marketreports.xml,"
    "https://www.livemint.com/rss/markets,"
    "https://www.business-standard.com/rss/markets-106.rss,"
    "https://news.google.com/rss/search?q=Nifty+OR+Sensex+OR+RBI+OR+SEBI+when:1d&hl=en-IN&gl=IN&ceid=IN:en"
)


def load_env_file(path: pathlib.Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _csv(value: str) -> list[str]:
    return [x.strip().upper() for x in value.split(",") if x.strip()]


@dataclass
class Config:
    email_to: str = ""
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    imap_host: str = "imap.gmail.com"
    capital: float = 500000.0
    risk_pct: float = 1.0
    broker: str = "zerodha"
    max_ideas_per_scan: int = 3
    max_ideas_per_day: int = 8
    max_emails_per_day: int = 15
    max_candidates: int = 12
    min_turnover_cr: float = 25.0
    watchlist: tuple = ()
    holdings: tuple = ()
    scan_interval_min: int = 15
    track_interval_min: int = 5
    news_interval_min: int = 20
    news_email_gap_min: int = 30
    inbox_interval_min: int = 10
    news_feeds: tuple = ()
    llm_provider: str = "none"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    state_dir: str = ".nse_agent_state"
    dry_run: bool = False
    cm_url: str = "https://mcp.nseindia.in/cmmkt/mcp"
    bhav_url: str = "https://mcp.nseindia.in/bhavcopy/cm/mcp"
    fno_enabled: bool = True
    fno_interval_min: int = 20
    fno_underlyings: tuple = ("NIFTY", "BANKNIFTY")
    fno_stocks_per_scan: int = 3
    max_fno_ideas_per_day: int = 4
    fno_source: str = "auto"
    fno_nondirectional: bool = True
    angel_api_key: str = field(default="", repr=False)
    angel_client_code: str = field(default="", repr=False)
    angel_pin: str = field(default="", repr=False)
    angel_totp_secret: str = field(default="", repr=False)

    @property
    def angel_ready(self) -> bool:
        return all((self.angel_api_key, self.angel_client_code, self.angel_pin, self.angel_totp_secret))

    @classmethod
    def from_env(cls, env_file: str | None = None) -> "Config":
        here = pathlib.Path(__file__).resolve().parents[1]
        for candidate in ([pathlib.Path(env_file)] if env_file else [pathlib.Path(".env"), here / ".env"]):
            load_env_file(candidate)
        e = lambda key, default="": os.environ.get(key) or default  # noqa: E731  (empty counts as unset)
        user = e("SMTP_USER", "")
        return cls(
            email_to=e("EMAIL_TO", user),
            smtp_user=user,
            smtp_password=e("SMTP_APP_PASSWORD", ""),
            smtp_host=e("SMTP_HOST", "smtp.gmail.com"),
            smtp_port=int(e("SMTP_PORT", "465")),
            imap_host=e("IMAP_HOST", "imap.gmail.com"),
            capital=float(e("CAPITAL", "500000")),
            risk_pct=float(e("RISK_PCT", "1.0")),
            broker=e("BROKER", "zerodha"),
            max_ideas_per_scan=int(e("MAX_IDEAS_PER_SCAN", "3")),
            max_ideas_per_day=int(e("MAX_IDEAS_PER_DAY", "8")),
            max_emails_per_day=int(e("MAX_EMAILS_PER_DAY", "15")),
            max_candidates=int(e("MAX_CANDIDATES", "12")),
            min_turnover_cr=float(e("MIN_TURNOVER_CR", "25")),
            watchlist=tuple(_csv(e("WATCHLIST", DEFAULT_WATCHLIST))),
            holdings=tuple(_csv(e("HOLDINGS", ""))),
            scan_interval_min=int(e("SCAN_INTERVAL_MIN", "15")),
            track_interval_min=int(e("TRACK_INTERVAL_MIN", "5")),
            news_interval_min=int(e("NEWS_INTERVAL_MIN", "20")),
            news_email_gap_min=int(e("NEWS_EMAIL_GAP_MIN", "30")),
            inbox_interval_min=int(e("INBOX_INTERVAL_MIN", "10")),
            news_feeds=tuple(x.strip() for x in e("NEWS_FEEDS", DEFAULT_FEEDS).split(",") if x.strip()),
            llm_provider=e("LLM_PROVIDER", "gemini" if e("GEMINI_API_KEY") else "none").lower(),
            gemini_api_key=e("GEMINI_API_KEY", ""),
            gemini_model=e("GEMINI_MODEL", "gemini-2.0-flash"),
            state_dir=e("STATE_DIR", ".nse_agent_state"),
            dry_run=e("DRY_RUN", "").lower() in ("1", "true", "yes"),
            fno_enabled=e("FNO_ENABLED", "1").lower() in ("1", "true", "yes"),
            fno_interval_min=int(e("FNO_INTERVAL_MIN", "20")),
            fno_underlyings=tuple(_csv(e("FNO_UNDERLYINGS", "NIFTY,BANKNIFTY"))),
            fno_stocks_per_scan=int(e("FNO_STOCKS_PER_SCAN", "3")),
            max_fno_ideas_per_day=int(e("MAX_FNO_IDEAS_PER_DAY", "4")),
            fno_source=e("FNO_SOURCE", "auto").lower(),
            fno_nondirectional=e("FNO_NONDIRECTIONAL", "1").lower() in ("1", "true", "yes"),
            angel_api_key=e("ANGEL_API_KEY", ""),
            angel_client_code=e("ANGEL_CLIENT_CODE", ""),
            angel_pin=e("ANGEL_PIN", ""),
            angel_totp_secret=e("ANGEL_TOTP_SECRET", ""),
        )

    def can_email(self) -> bool:
        return bool(self.email_to and self.smtp_user and self.smtp_password)
