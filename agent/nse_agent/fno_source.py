"""Picks the F&O data source: Angel One SmartAPI when its credentials are set, else NSE's public website.

When Angel One is primary and a call fails (login problem, rate limit, outage), the same call is retried on the
NSE website so the agent keeps working; the chain's `source` field says which one produced the numbers.
"""
import logging

from .angel_data import AngelFno, AngelSession
from .fno_data import FnoError, NseFno

log = logging.getLogger("nse_agent")


class FallbackFno:
    def __init__(self, primary, fallback):
        self.primary, self.fallback = primary, fallback

    @staticmethod
    def is_index(symbol: str) -> bool:
        return NseFno.is_index(symbol)

    def _try(self, name: str, *args):
        try:
            return getattr(self.primary, name)(*args)
        except FnoError as exc:
            log.warning("Angel One %s failed (%s); using the NSE website", name, exc)
            return getattr(self.fallback, name)(*args)

    def expiries(self, symbol):
        return self._try("expiries", symbol)

    def chain(self, symbol, expiry):
        return self._try("chain", symbol, expiry)

    def futures(self, symbols=None):
        return self._try("futures", symbols)

    def oi_spurts(self):
        return self._try("oi_spurts")

    def lot_size(self, symbol, today=None):
        try:
            lot = self.primary.lot_size(symbol, today)
        except FnoError:
            lot = None
        return lot or self.fallback.lot_size(symbol, today)


def make_fno(cfg):
    """NseFno, AngelFno, or Angel One with NSE as backup, according to FNO_SOURCE (auto, angel or nse)."""
    if cfg.fno_source == "nse" or (cfg.fno_source == "auto" and not cfg.angel_ready):
        return NseFno()
    if not cfg.angel_ready:
        raise FnoError("FNO_SOURCE=angel needs ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN and ANGEL_TOTP_SECRET")
    session = AngelSession(cfg.angel_api_key, cfg.angel_client_code, cfg.angel_pin, cfg.angel_totp_secret)
    return FallbackFno(AngelFno(session, cache_dir=cfg.state_dir), NseFno())
