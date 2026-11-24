"""Tests for the Angel One SmartAPI F&O source. No network: the HTTP opener and the API session are faked."""
import json
import unittest
from datetime import datetime, timedelta

from test_nse_agent import at  # noqa: F401  (puts agent/ on sys.path)

from nse_agent import angel_data as ad  # noqa: E402
from nse_agent import fno_analysis as fa  # noqa: E402
from nse_agent.clock import IST, now_ist  # noqa: E402
from nse_agent.config import Config  # noqa: E402
from nse_agent.fno_data import FnoError, Future, NseFno  # noqa: E402
from nse_agent.fno_source import FallbackFno, make_fno  # noqa: E402

TODAY = now_ist().date()
EXPIRY = TODAY + timedelta(days=7)
FAR = TODAY + timedelta(days=35)
SPOT = 25000.0
STRIKES = [24500.0 + 50 * i for i in range(21)]
STAMP = now_ist().strftime("%d-%b-%Y %H:%M:%S")


class Resp:
    def __init__(self, body, status=200):
        self.body, self.status = body, status

    def read(self):
        return json.dumps(self.body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Opener:
    def __init__(self, script):
        self.script, self.reqs = list(script), []

    def open(self, req, timeout=None):
        self.reqs.append(req)
        return self.script.pop(0)


def login_ok(token):
    return Resp({"status": True, "message": "SUCCESS", "data": {"jwtToken": token}})


def session(script):
    return ad.AngelSession("KEY", "A12345", "4321", "JBSWY3DPEHPK3PXP", opener=Opener(script), sleep=lambda s: None)


class SessionTests(unittest.TestCase):
    def test_totp_matches_rfc6238_vector(self):
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # ASCII "12345678901234567890"
        self.assertEqual(ad.totp(secret, at=59, digits=8), "94287082")
        self.assertEqual(ad.totp(secret, at=1111111109, digits=8), "07081804")

    def test_login_then_reuse_token_and_send_headers(self):
        s = session([login_ok("tok1"), Resp({"status": True, "data": {"x": 1}}), Resp({"status": True, "data": {"x": 2}})])
        self.assertEqual(s.call(ad.GREEKS, {"a": 1}), {"x": 1})
        self.assertEqual(s.call(ad.GREEKS, {"a": 2}), {"x": 2})
        login, first, second = s.opener.reqs
        sent = json.loads(login.data)
        self.assertEqual((sent["clientcode"], sent["password"]), ("A12345", "4321"))
        self.assertRegex(sent["totp"], r"^\d{6}$")
        self.assertEqual(first.get_header("X-privatekey"), "KEY")
        self.assertEqual(second.get_header("Authorization"), "Bearer tok1")
        self.assertEqual(len(s.opener.reqs), 3)  # one login only

    def test_expired_token_triggers_one_relogin(self):
        s = session([login_ok("tok1"), Resp({"status": False, "message": "Invalid Token", "errorcode": "AG8001"}),
                     login_ok("tok2"), Resp({"status": True, "data": [1]})])
        self.assertEqual(s.call(ad.GREEKS, {}), [1])
        self.assertEqual(s.opener.reqs[-1].get_header("Authorization"), "Bearer tok2")

    def test_failed_login_is_clear_does_not_leak_secrets_and_is_not_hammered(self):
        s = session([Resp({"status": False, "message": "Invalid totp"})])
        with self.assertRaises(FnoError) as cm:
            s.call(ad.GREEKS, {})
        self.assertIn("Invalid totp", str(cm.exception))
        self.assertNotIn("4321", str(cm.exception))
        self.assertNotIn("JBSWY3DPEHPK3PXP", str(cm.exception))
        with self.assertRaises(FnoError) as again:  # script is empty: a real request would raise IndexError
            s.call(ad.GREEKS, {})
        self.assertIn("not retrying", str(again.exception))

    def test_implied_vol_round_trips(self):
        price = fa.bs_price("P", 25000, 24800, 7 / 365, 18.5)
        self.assertAlmostEqual(ad.implied_vol("P", 25000, 24800, 7 / 365, price), 18.5, places=1)
        self.assertEqual(ad.implied_vol("C", 25000, 24000, 7 / 365, 500.0), 0.0)  # below intrinsic


def master_rows():
    rows = [{"token": "99926000", "symbol": "Nifty 50", "name": "NIFTY", "expiry": "", "strike": "0", "lotsize": "1",
             "instrumenttype": "AMXIDX", "exch_seg": "NSE"},
            {"token": "2885", "symbol": "RELIANCE-EQ", "name": "RELIANCE", "expiry": "", "strike": "-1", "lotsize": "1",
             "instrumenttype": "", "exch_seg": "NSE"}]
    for e, tag in ((EXPIRY, "W"), (FAR, "M")):
        fmt = e.strftime("%d%b%Y").upper()
        for k in STRIKES:
            for kind in ("CE", "PE"):
                rows.append({"token": f"{tag}{int(k)}{kind}", "symbol": f"NIFTY{tag}{int(k)}{kind}", "name": "NIFTY",
                             "expiry": fmt, "strike": f"{k * 100:.6f}", "lotsize": "65", "instrumenttype": "OPTIDX",
                             "exch_seg": "NFO"})
    for e, tok in ((FAR, "F2"), (EXPIRY + timedelta(days=21), "F1")):
        rows.append({"token": tok, "symbol": "NIFTYFUT" + tok, "name": "NIFTY", "expiry": e.strftime("%d%b%Y").upper(),
                     "strike": "-1", "lotsize": "65", "instrumenttype": "FUTIDX", "exch_seg": "NFO"})
    rows.append({"token": "OLD", "symbol": "NIFTYOLD", "name": "NIFTY", "expiry": (TODAY - timedelta(days=3))
                 .strftime("%d%b%Y").upper(), "strike": "-1", "lotsize": "65", "instrumenttype": "FUTIDX", "exch_seg": "NFO"})
    return rows


class FakeSession:
    """Answers the endpoints AngelFno uses with a consistent little market (iv 15, ATM OI is the highest)."""

    def __init__(self, oi_history_works=True, greeks_work=True):
        self.calls, self.oi_ok, self.greeks_ok = [], oi_history_works, greeks_work

    @staticmethod
    def oi(token):
        k = float(token[1:-2]) if token[0] in "WM" else 0.0
        return 200_000 - abs(k - SPOT) * 40 if k else 150_000

    def quote(self, exchange, token):
        feed = {"exchFeedTime": STAMP, "tradeVolume": 5000}
        if exchange == "NSE":
            return {"symbolToken": token, "ltp": SPOT if token == "99926000" else 2900.0, **feed}
        if token[0] in "WM":
            k, kind = float(token[1:-2]), token[-2:]
            days = 7 if token[0] == "W" else 35
            ltp = round(fa.bs_price("C" if kind == "CE" else "P", SPOT, k, days / 365, 15.0), 2)
            return {"symbolToken": token, "ltp": ltp, "opnInterest": self.oi(token), **feed,
                    "depth": {"buy": [{"price": ltp - 0.5}], "sell": [{"price": ltp + 0.5}]}}
        return {"symbolToken": token, "ltp": SPOT + 120, "opnInterest": 180_000, "percentChange": -1.4, "open": SPOT,
                "high": SPOT + 150, "low": SPOT - 90, "close": SPOT + 300, **feed}

    def call(self, path, body):
        self.calls.append(path)
        if path == ad.QUOTE:
            (ex, toks), = body["exchangeTokens"].items()
            return {"fetched": [self.quote(ex, t) for t in toks], "unfetched": []}
        if path == ad.GREEKS:
            if not self.greeks_ok:
                raise FnoError("greeks down")
            return [{"strikePrice": f"{k:.6f}", "optionType": kind, "impliedVolatility": "15.0"}
                    for k in STRIKES for kind in ("CE", "PE") if (k, kind) != (SPOT, "CE")]
        if path == ad.OI_HISTORY:
            if not self.oi_ok:
                raise FnoError("history unavailable")
            cur = self.oi(body["symboltoken"])
            return [{"time": f"{TODAY - timedelta(days=1)}T00:00:00+05:30", "oi": cur - 1000},
                    {"time": f"{TODAY}T00:00:00+05:30", "oi": cur}]
        if path == ad.BUILDUP:
            if body["datatype"] == "Long Built Up":
                return [{"tradingSymbol": "RELIANCE30DEC26FUT", "opnInterest": "1100", "netChangeOpnInterest": "100"}]
            return []
        raise AssertionError(path)


def angel(tmp=None, **kw):
    s = FakeSession(**kw)
    return ad.AngelFno(s, cache_dir=tmp, fetch_master=master_rows), s


class AngelFnoTests(unittest.TestCase):
    def test_instruments_expiries_and_lots(self):
        f, _ = angel()
        self.assertEqual(f.expiries("NIFTY"), [EXPIRY, FAR])
        self.assertEqual(f.lot_size("NIFTY"), 65)
        with self.assertRaises(FnoError):
            f.expiries("NOSUCH")

    def test_chain_has_prices_oi_iv_depth_and_change_in_oi(self):
        f, s = angel()
        ch = f.chain("NIFTY", EXPIRY)
        self.assertEqual((ch.source, ch.spot, len(ch.rows)), ("Angel One SmartAPI", SPOT, 21))
        self.assertIsNotNone(ch.ts)
        self.assertLess(abs((now_ist() - ch.ts).total_seconds()), 120)
        by = {k: (c, p) for k, c, p in ch.rows}
        ce, pe = by[25050.0]
        self.assertAlmostEqual(ce.iv, 15.0)
        self.assertGreater(ce.ask, ce.bid)
        self.assertEqual(ce.oi, 200_000 - 50 * 40)
        self.assertEqual(ce.doi, 1000)  # inside the 6-strike window: today's OI minus yesterday's
        self.assertEqual(by[24500.0][0].doi, 0)  # outside the window: not fetched
        self.assertEqual(s.calls.count(ad.OI_HISTORY), 26)
        # the one call missing from the greeks list is solved from its price
        self.assertAlmostEqual(by[SPOT][0].iv, 15.0, delta=0.6)

    def test_chain_still_works_when_history_and_greeks_fail(self):
        f, _ = angel(oi_history_works=False, greeks_work=False)
        ch = f.chain("NIFTY", EXPIRY)
        by = {k: (c, p) for k, c, p in ch.rows}
        self.assertTrue(all(c.doi == 0 for _, c, _ in ch.rows if c))
        self.assertAlmostEqual(by[25100.0][1].iv, 15.0, delta=0.6)  # IV solved from the price

    def test_futures_and_spurts(self):
        f, _ = angel()
        futs = f.futures(["NIFTY"])["NIFTY"]
        self.assertEqual([x.expiry for x in futs], [EXPIRY + timedelta(days=21), FAR])  # sorted; expired one dropped
        self.assertEqual((futs[0].ltp, futs[0].oi, futs[0].pct, futs[0].prev, futs[0].spot),
                         (SPOT + 120, 180_000, -1.4, SPOT + 300, SPOT))
        sp = f.oi_spurts()
        self.assertAlmostEqual(sp["RELIANCE"]["pct"], 10.0)
        self.assertAlmostEqual(sp["NIFTY"]["change"], 180_000 - 149_000)  # index future OI vs yesterday's close

    def test_master_is_cached_on_disk_for_the_day(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            angel(tmp)[0].lot_size("NIFTY")
            g = ad.AngelFno(FakeSession(), cache_dir=tmp, fetch_master=lambda: self.fail("should read the cache"))
            self.assertEqual(g.lot_size("NIFTY"), 65)


class SourceSelectionTests(unittest.TestCase):
    def test_fallback_uses_nse_when_angel_fails_and_labels_nothing_wrongly(self):
        class Broken:
            def chain(self, *a):
                raise FnoError("login failed")

            lot_size = futures = expiries = oi_spurts = chain

        class Backup:
            def chain(self, *a):
                return "nse-chain"

            def lot_size(self, *a):
                return 65

        fb = FallbackFno(Broken(), Backup())
        self.assertEqual(fb.chain("NIFTY", EXPIRY), "nse-chain")
        self.assertEqual(fb.lot_size("NIFTY"), 65)

    def test_make_fno_follows_credentials_and_setting(self):
        none = Config()
        full = Config(angel_api_key="k", angel_client_code="c", angel_pin="p", angel_totp_secret="JBSWY3DPEHPK3PXP")
        self.assertIsInstance(make_fno(none), NseFno)
        self.assertIsInstance(make_fno(full), FallbackFno)
        self.assertIsInstance(make_fno(Config(**{**full.__dict__, "fno_source": "nse"})), NseFno)
        with self.assertRaises(FnoError):
            make_fno(Config(fno_source="angel"))
        self.assertNotIn("JBSWY3DPEHPK3PXP", repr(full))


if __name__ == "__main__":
    unittest.main()
