"""Tests for the futures & options desk (agent/nse_agent/fno_*). No network: the NSE web client is faked."""
import unittest
from datetime import date

from test_nse_agent import FakeData, at, make_agent, news  # also puts agent/ on sys.path

from nse_agent import fno_analysis as fa  # noqa: E402
from nse_agent import fno_mcp  # noqa: E402
from nse_agent.engine import Agent  # noqa: E402
from nse_agent.fno_data import Chain, Future, Leg, NseFno  # noqa: E402
from nse_agent.fno_render import render_view  # noqa: E402
from nse_agent.fno_service import FnoService  # noqa: E402
from nse_agent.nse import IndexQuote  # noqa: E402

EXPIRY = date(2026, 12, 1)  # 7 days after the test clock
SPOT = 25350.0


def make_chain(spot=SPOT, iv=16.0, put_oi_scale=1.0, call_doi=3.0, put_doi=1.5, ts=None, expiry=EXPIRY):
    t = (expiry - date(2026, 11, 24)).days / 365
    rows = []
    for i in range(0, 55):
        k = 24000.0 + 50 * i
        near = abs(k - spot) <= 300
        ce, pe = fa.bs_price("C", spot, k, t, iv), fa.bs_price("P", spot, k, t, iv)
        c_oi = 129_000 if k == 26000 else 5_000 + (20_000 if near else 0)
        p_oi = (79_000 if k == 24000 else 5_000 + (20_000 if near else 0)) * put_oi_scale
        rows.append((k, Leg(ce, c_oi, c_oi * 0.01 * call_doi if near else 0, iv, 5_000, ce * 0.995, ce * 1.005),
                     Leg(pe, p_oi, p_oi * 0.01 * put_doi if near else 0, iv, 5_000, pe * 0.995, pe * 1.005)))
    return Chain("NIFTY", expiry, spot, ts or at(10, 25), rows)


def future(pct=-1.55):
    return Future("NIFTY", date(2026, 12, 29), SPOT + 180, pct, SPOT, SPOT, SPOT, SPOT, 180_000, 7_000_000, SPOT)


def ctx(**kw):
    base = dict(symbol="NIFTY", is_index=True, day_pct=-1.75, sma50=23520.0, atr=372.0, tone="risk-off", vix=17.9,
                vix_pct=3.1, lot=65, capital=4_000_000, risk_pct=1.0, spurt={"pct": 11.1})
    base.update(kw)
    return fa.Ctx(**base)


class FakeFno:
    def __init__(self, chain=None):
        self._chain = chain or make_chain()

    def expiries(self, symbol):
        return [date(2026, 11, 24), EXPIRY, date(2026, 12, 29)]

    def chain(self, symbol, expiry):
        return self._chain

    def futures(self, symbols=None):
        return {"NIFTY": [future()]}

    def oi_spurts(self):
        return {"NIFTY": {"oi": 1.0, "prev": 1.0, "change": 0.1, "pct": 11.1}}

    def lot_size(self, symbol, today=None):
        return {"NIFTY": 65}.get(symbol)


class PricingTests(unittest.TestCase):
    def test_put_call_parity_and_delta_bounds(self):
        s, k, t, iv = 25000.0, 25200.0, 10 / 365, 16.0
        lhs = fa.bs_price("C", s, k, t, iv) - fa.bs_price("P", s, k, t, iv)
        import math
        self.assertAlmostEqual(lhs, s - k * math.exp(-fa.RATE * t), delta=0.5)
        call, put = fa.greeks("C", s, k, t, iv), fa.greeks("P", s, k, t, iv)
        self.assertTrue(0 < call["delta"] < 1 and -1 < put["delta"] < 0)
        self.assertLess(call["theta_day"], 0)

    def test_buildup_quadrants(self):
        c = fa.classify_buildup
        self.assertEqual(c(1.0, 5, True), "long buildup")
        self.assertEqual(c(1.0, -5, True), "short covering")
        self.assertEqual(c(-1.0, 5, True), "short buildup")
        self.assertEqual(c(-1.0, -5, True), "long unwinding")
        self.assertEqual(c(0.05, 5, True), "flat")


class MetricsTests(unittest.TestCase):
    def test_walls_pcr_and_max_pain(self):
        m = fa.metrics(make_chain(), at(10, 30), 372.0)
        self.assertEqual((m["call_wall"], m["put_wall"]), (26000.0, 24000.0))
        self.assertAlmostEqual(m["dte"], 7.2, delta=0.1)
        self.assertIsNotNone(m["pcr_oi"])
        self.assertTrue(24000 <= m["max_pain"] <= 26700)
        self.assertLess(m["iv_ratio"], 1.0)  # 16% IV vs a 1.47% daily ATR


class VerdictTests(unittest.TestCase):
    def view(self, c=None, chain=None, fut=None):
        return fa.analyse(chain or make_chain(), fut or future(), c or ctx(), at(10, 30))

    def test_bearish_confluence_gives_a_defined_risk_plan(self):
        v = self.view()
        self.assertEqual(v.direction, "bearish")
        p = v.plan
        self.assertIsNotNone(p)
        lv = p["levels"]
        self.assertTrue(lv["stop"] > lv["trigger"] > lv["target"])
        self.assertLessEqual(p["risk_rs"], 4_000_000 * 0.01)
        self.assertGreaterEqual(p["rr_net"], 1.5)
        self.assertEqual(p["qty"], p["lots"] * 65)

    def test_plans_never_sell_naked_options(self):
        structures = set()
        for iv in (16.0, 28.0, 40.0):
            for day in (-1.75, 1.75):
                bull = day > 0
                chain = make_chain(iv=iv, put_oi_scale=3.0 if bull else 1.0, call_doi=1.0 if bull else 3.0,
                                   put_doi=4.0 if bull else 1.5)
                v = self.view(ctx(day_pct=day, tone="trending up" if bull else "risk-off"), chain=chain, fut=future(pct=day))
                if v.plan:
                    structures.add(v.plan["structure"])
                    actions = [leg["action"] for leg in v.plan["legs"]]
                    if "SELL" in actions:
                        self.assertIn("BUY", actions)
                    self.assertLessEqual(v.plan["risk_rs"], 4_000_000 * 0.01)
        self.assertGreaterEqual(len(structures), 3)  # long option, debit spread and credit spread all get exercised

    def test_small_account_gets_no_trade_with_reason(self):
        v = self.view(ctx(capital=100_000))
        self.assertIsNone(v.plan)
        self.assertTrue(any("risk budget" in n for n in v.notes))

    def test_no_directional_options_trade_near_expiry(self):
        short = make_chain(expiry=date(2026, 11, 25))
        v = fa.analyse(short, future(), ctx(), at(10, 30))
        self.assertIsNone(v.plan)

    def test_mixed_evidence_is_not_a_trade(self):
        v = self.view(ctx(day_pct=0.0, tone="choppy", spurt={"pct": 0.0}), fut=future(pct=0.0))
        self.assertIn(v.direction, ("neutral", "conflicted"))
        self.assertIsNone(v.plan)

    def test_negative_news_blocks_a_bullish_call(self):
        bull = ctx(day_pct=1.75, tone="trending up", negative_news=("Probe launched",))
        chain = make_chain(put_oi_scale=3.0, call_doi=1.0, put_doi=4.0)
        v = fa.analyse(chain, future(pct=1.5), bull, at(10, 30))
        self.assertNotEqual(v.direction, "bullish")

    def test_graph_marks_support_and_opposition(self):
        v = self.view()
        g = v.graph()
        relations = {e["relation"] for e in g["edges"]}
        self.assertIn("supports", relations)
        ids = {n["id"] for n in g["nodes"]}
        self.assertTrue({"verdict", "stop", "target", "structure"} <= ids)
        text = render_view(v, at(10, 30))
        self.assertIn("KNOWLEDGE GRAPH", text)
        self.assertIn("SEBI", text)
        self.assertIn("01 Dec 2026", text)


class ServiceAndEngineTests(unittest.TestCase):
    def setUp(self):
        self._orig = (news.fetch_feeds, news.symbol_news)
        news.fetch_feeds = lambda urls: []
        news.symbol_news = lambda symbol, now, hours=36: []

    def tearDown(self):
        news.fetch_feeds, news.symbol_news = self._orig

    def agent(self):
        data = FakeData()
        data.nifty = IndexQuote("NIFTY 50", SPOT, 25800, -1.75, 25700, 25720, 25300, at(10, 29))
        agent = make_agent(data, fno_enabled=True)
        agent.cfg.capital = 4_000_000
        agent._fno_client = FakeFno()
        agent.fno_service().stats = lambda symbol, today: {"sma50": 23520.0, "atr": 372.0}
        agent.fno_service().brain = lambda symbol, today, dte: {}  # the fake bars are a smooth uptrend, not a real chart
        return agent

    def test_scan_sends_one_idea_and_tracks_the_short_side(self):
        agent = self.agent()
        agent.tick(at(10, 30))
        subjects = [s for s, _, _ in agent.mailer.sent]
        self.assertTrue(any("F&O idea: NIFTY bearish" in s for s in subjects), subjects)
        idea = next(i for i in agent.state.data["ideas"].values() if i["kind"] == "fno")
        self.assertEqual((idea["side"], idea["index_name"]), ("short", "NIFTY 50"))
        agent.tick(at(10, 50))  # same call again: deduplicated
        self.assertEqual(sum("F&O idea" in s for s, _, _ in agent.mailer.sent), 1)
        agent.data.nifty = IndexQuote("NIFTY 50", idea["entry"] - 5, 25800, -1.9, 25700, 25720, 25300, at(10, 59))
        agent.tick(at(11, 0))
        self.assertEqual(idea["status"], "triggered")
        self.assertTrue(any("F&O update" in s for s, _, _ in agent.mailer.sent))

    def test_stale_chain_is_never_an_alert(self):
        agent = self.agent()
        agent._fno_client = FakeFno(make_chain(ts=at(15, 40, date(2026, 11, 23))))
        agent.fno_service().fno = agent._fno_client
        agent.tick(at(10, 30))
        self.assertFalse(any("F&O" in s for s, _, _ in agent.mailer.sent))

    def test_short_idea_lifecycle_mirrors_long(self):
        idea = {"horizon": "swing", "side": "short", "status": "pending", "entry": 100.0, "stop": 105.0, "target": 90.0}
        self.assertIsNone(Agent.advance(idea, 101.0, at(11)))
        self.assertEqual(Agent.advance(idea, 99.9, at(11)), "triggered")
        self.assertEqual(Agent.advance(idea, 89.0, at(12)), "target")
        stopped = dict(idea, status="triggered")
        self.assertEqual(Agent.advance(stopped, 105.5, at(12)), "stopped")

    def test_service_picks_first_expiry_with_two_days_left(self):
        agent = self.agent()
        self.assertEqual(agent.fno_service().pick_expiry("NIFTY", at(10, 30)), EXPIRY)


class DataAndMcpTests(unittest.TestCase):
    def test_lot_size_csv(self):
        class Web:
            def text(self, url):
                return ("UNDERLYING,SYMBOL,DEC-26,JAN-27\nNifty 50   ,NIFTY    ,65 ,65\n"
                        "Reliance Industries ,RELIANCE ,500 ,500\n")
        fno = NseFno(Web())
        self.assertEqual((fno.lot_size("NIFTY", date(2026, 11, 24)), fno.lot_size("RELIANCE")), (65, 500))
        self.assertIsNone(fno.lot_size("NOPE"))

    def backend(self):
        b = object.__new__(fno_mcp.Backend)
        b.fno = FakeFno()
        b.data = FakeData()
        b.service = FnoService(make_agent(b.data).cfg, b.fno, b.data)
        return b

    def test_mcp_handshake_and_tools(self):
        b = self.backend()
        init = fno_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, b)
        self.assertEqual(init["result"]["serverInfo"]["name"], "nse-fno")
        self.assertIsNone(fno_mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, b))
        names = {t["name"] for t in fno_mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, b)["result"]["tools"]}
        self.assertEqual(names, {"fno_view", "fno_option_chain", "fno_futures", "fno_oi_spurts", "fno_strategy_guide"})
        bad = fno_mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "place_order"}}, b)
        self.assertIn("error", bad)

    def test_mcp_chain_and_oi_tools(self):
        b = self.backend()
        call = lambda name, args: fno_mcp.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",  # noqa: E731
                                                  "params": {"name": name, "arguments": args}}, b)
        out = call("fno_option_chain", {"symbol": "NIFTY", "strikes": 2})["result"]
        self.assertFalse(out["isError"])
        self.assertIn("25,350*", out["content"][0]["text"])
        self.assertIn("NIFTY: OI", call("fno_oi_spurts", {})["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
