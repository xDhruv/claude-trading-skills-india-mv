"""Tests for the F&O strategy brain: payoff maths, chart patterns, analogs, news and event risk, the non-directional planner."""
import contextlib
import io
import json
import math
import random
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from test_fno import FakeFno, make_chain
from test_nse_agent import FakeData, at, make_agent  # also puts agent/ on sys.path

from nse_agent import __main__ as cli  # noqa: E402
from nse_agent import fno_analysis as fa  # noqa: E402
from nse_agent import fno_mcp, fno_news, fno_patterns as fp, fno_service as fsvc  # noqa: E402
from nse_agent import fno_strategies as fs  # noqa: E402
from nse_agent import news  # noqa: E402
from nse_agent.engine import Agent  # noqa: E402
from nse_agent.fno_render import render_view  # noqa: E402
from nse_agent.nse import Bar  # noqa: E402


def bars_from(closes, spread=0.004):
    start = date(2025, 1, 1)
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        hi, lo = max(prev, c) * (1 + spread), min(prev, c) * (1 - spread)
        out.append(Bar((start + timedelta(days=i)).isoformat(), prev, hi, lo, c, 1000.0))
        prev = c
    return out


def walk(n, seed=1, drift=0.0003, vol=0.01):
    rnd = random.Random(seed)
    c, out = 100.0, []
    for _ in range(n):
        c *= 1 + rnd.gauss(drift, vol)
        out.append(c)
    return out


def legs(name, atm=25000, a=100, w=100, price=100.0):
    return [fs.SLeg(act, kind, k, 0.0 if kind == "FUT" else price, 15.0, r) for act, kind, k, r in fs.shape(name, atm, a, w)]


class PayoffTests(unittest.TestCase):
    def test_every_catalogue_strategy_has_a_shape_and_a_profile(self):
        for name in fs.CATALOGUE:
            prof = fs.profile(legs(name))
            self.assertIn("breakevens", prof, name)

    def test_long_call_and_straddle_are_unbounded_up_with_known_breakevens(self):
        call = fs.profile([fs.SLeg("BUY", "CE", 25000, 100)])
        self.assertIsNone(call["max_profit"])
        self.assertEqual((call["max_loss"], call["breakevens"]), (100, [25100.0]))
        straddle = fs.profile([fs.SLeg("BUY", "CE", 25000, 120), fs.SLeg("BUY", "PE", 25000, 80)])
        self.assertEqual(straddle["breakevens"], [24800.0, 25200.0])
        self.assertIsNone(straddle["max_profit"])

    def test_spreads_and_condor_are_capped_and_naked_shorts_are_flagged_unlimited(self):
        bull = fs.profile([fs.SLeg("BUY", "CE", 25000, 150), fs.SLeg("SELL", "CE", 25200, 70)])
        self.assertEqual((bull["max_profit"], bull["max_loss"], bull["breakevens"]), (120, 80, [25080.0]))
        ic = fs.profile([fs.SLeg("SELL", "PE", 24800, 60), fs.SLeg("BUY", "PE", 24700, 30),
                         fs.SLeg("SELL", "CE", 25200, 60), fs.SLeg("BUY", "CE", 25300, 30)])
        self.assertEqual((ic["max_profit"], ic["max_loss"]), (60, 40))
        self.assertEqual(ic["breakevens"], [24740.0, 25260.0])
        self.assertIsNone(fs.profile([fs.SLeg("SELL", "CE", 25000, 100), fs.SLeg("SELL", "PE", 25000, 100)])["max_loss"])
        self.assertEqual(fs.profile([fs.SLeg("SELL", "PE", 25000, 100)])["max_loss"], 24900)

    def test_box_payoff_is_flat_and_butterfly_peaks_at_the_middle(self):
        box = legs("long_box", w=200)
        self.assertAlmostEqual(fs.payoff(box, 24000), fs.payoff(box, 26000))
        fly = legs("long_butterfly", w=100, price=50.0)
        self.assertGreater(fs.payoff(fly, 25000), fs.payoff(fly, 25100))
        self.assertEqual(fs.profile(fly)["max_profit"], fs.payoff(fly, 25000))

    def test_expected_value_of_a_fair_option_is_near_zero_and_empirical_sample_works(self):
        price = fs.bs_price("C", 25000, 25000, 7 / 365, 15)
        call = [fs.SLeg("BUY", "CE", 25000, price)]
        ex = fs.expected(call, 25000, 7, 15)
        self.assertLess(abs(ex["ev"]), 0.1 * price)  # the grid has no drift, BS prices in the risk-free rate
        self.assertLess(ex["pop"], 0.5)
        sample = fs.expected(call, 25000, 7, rets=[0.02, -0.02, 0.0, 0.01])
        self.assertAlmostEqual(sample["pop"], 0.5)
        self.assertEqual(fs.expected(call, 25000, 7), {"pop": None, "ev": None})

    def test_size_lots_respects_risk_budget_gap_cap_and_outlay(self):
        self.assertEqual(fs.size_lots(5_000, 12_000, 10_000, 500_000, 1.0), 1)
        self.assertEqual(fs.size_lots(5_000, 20_000, 10_000, 500_000, 1.0), 0)  # max loss over 3x the budget
        self.assertEqual(fs.size_lots(0, 1, 1, 500_000, 1.0), 0)

    def test_chart_marks_spot_zero_line_and_curve(self):
        txt = fs.payoff_chart(legs("long_straddle", price=100), 25000, 65)
        self.assertIn("*", txt)
        self.assertIn("|", txt)
        self.assertIn("spot 25,000", txt)

    def test_guide_and_catalogue(self):
        self.assertIn("Net credit", fs.guide("iron_condor"))
        self.assertIn("unlimited", fs.guide("short_straddle"))
        self.assertIn("never suggested", fs.guide("short_strangle"))
        self.assertIn("iron_condor", fs.catalogue_text())


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.ch = make_chain(iv=22.0)
        self.m = fa.metrics(self.ch, at(10, 30), 372.0)

    def plan(self, mode, iv=None, **kw):
        ch, m = self.ch, self.m
        if iv:
            ch = make_chain(iv=iv)
            m = fa.metrics(ch, at(10, 30), 372.0)
        args = dict(lot=65, atr=372.0, capital=4_000_000, risk_pct=1.0, broker="zerodha", is_index=True)
        args.update(kw)
        return fs.plan_nondirectional(mode, ch, m, **args)

    def test_range_sells_a_defined_risk_condor_when_premium_is_rich(self):
        plan, screen = self.plan("range", rv_ann=9.0)
        self.assertIsNotNone(plan, screen)
        self.assertEqual((plan["kind"], plan["structure"]), ("range", "Iron condor (credit)"))
        self.assertEqual(len(plan["legs"]), 4)
        lv = plan["levels"]
        self.assertLess(lv["lower"], self.ch.spot)
        self.assertGreater(lv["upper"], self.ch.spot)
        self.assertGreater(plan["stats"]["max_loss_rs"], 0)
        self.assertIn("*", plan["chart"])
        self.assertTrue(any(s["status"] == "selected" for s in screen))
        self.assertTrue(any(s["status"] == "not suggested" and "Short straddle" in s["name"] for s in screen))

    def test_range_is_refused_around_events_and_when_premium_is_not_rich(self):
        plan, screen = self.plan("range", rv_ann=9.0, event_level=2)
        self.assertIsNone(plan)
        self.assertIn("event", screen[0]["why"])
        plan, screen = self.plan("range", rv_ann=21.0)
        self.assertIsNone(plan)
        self.assertIn("not rich enough", screen[0]["why"])
        plan, screen = self.plan("range", rv_ann=9.0, squeeze=True)
        self.assertIsNone(plan)
        self.assertIn("squeeze", screen[0]["why"])

    def test_breakout_buys_a_straddle_or_strangle_only_with_a_reason_and_cheap_options(self):
        plan, screen = self.plan("breakout", iv=12.0, rv_ann=24.0, squeeze=True)
        self.assertIsNotNone(plan, screen)
        self.assertIn("Long str", plan["structure"])
        self.assertEqual(plan["max_loss"], plan["outlay"])
        none, why = self.plan("breakout", iv=12.0, rv_ann=24.0)
        self.assertIsNone(none)
        self.assertIn("no squeeze", why[0]["why"])
        none, why = self.plan("breakout", iv=12.0, rv_ann=10.0, squeeze=True)
        self.assertIsNone(none)
        self.assertIn("not cheap", why[0]["why"])

    def test_nothing_is_planned_without_lot_size_or_with_too_little_time(self):
        plan, screen = self.plan("range", lot=None, rv_ann=9.0)
        self.assertIsNone(plan)
        self.assertEqual(screen[0]["status"], "rejected")
        self.m["dte"] = 1.0
        plan, screen = self.plan("range", rv_ann=9.0)
        self.assertIsNone(plan)


class AnalyseWiringTests(unittest.TestCase):
    def ctx(self, **kw):
        base = dict(symbol="NIFTY", is_index=True, day_pct=0.0, sma50=25300.0, atr=372.0, tone="choppy", vix=14.0,
                    lot=65, capital=4_000_000, risk_pct=1.0, rv_ann=9.0)
        base.update(kw)
        return fa.Ctx(**base)

    def test_flat_evidence_with_rich_premium_yields_a_range_structure(self):
        ch = make_chain(iv=22.0, call_doi=1.0, put_doi=1.0)
        v = fa.analyse(ch, None, self.ctx(), at(10, 30))
        self.assertEqual(v.direction, "neutral")
        self.assertIsNotNone(v.plan, v.notes)
        self.assertEqual(v.plan["kind"], "range")
        self.assertTrue(v.screen)
        text = render_view(v, at(10, 30))
        for part in ("PAYOFF AT EXPIRY", "STRATEGIES CONSIDERED", "Watch levels", "KNOWLEDGE GRAPH"):
            self.assertIn(part, text)
        ids = {n["id"] for n in v.graph()["nodes"]}
        self.assertTrue({"structure", "lower", "upper"} <= ids)

    def test_non_directional_can_be_switched_off(self):
        v = fa.analyse(make_chain(call_doi=1.0, put_doi=1.0), None, self.ctx(allow_nd=False), at(10, 30))
        self.assertIsNone(v.plan)

    def test_chart_analog_news_and_event_become_nodes(self):
        chart = {"patterns": [{"name": "20-day breakout", "bias": 1.5, "text": "x"}], "bias": 1.5, "coiled": True,
                 "bw_pct": 10.0, "squeeze": True}
        analog = {"n": 20, "pool": 400, "up_rate": 0.7, "horizon": 3, "mean_pct": 0.4, "p10_pct": -1.0, "p90_pct": 1.5,
                  "bias": 1.0, "dates": ["2025-01-01"]}
        ctx = self.ctx(chart=chart, analog=analog, news_bias=1.0, news_lines=("up: markets rally",),
                       event={"level": 2, "tags": ["RBI policy"], "lines": ["RBI decision tomorrow"]})
        v = fa.analyse(make_chain(), None, ctx, at(10, 30))
        by = {n.id: n for n in v.nodes}
        for key in ("chart", "analog", "news", "event", "squeeze", "volregime"):
            self.assertIn(key, by)
        self.assertEqual((by["chart"].kind, by["event"].kind), ("direction", "context"))
        self.assertGreater(by["chart"].weight, 1.0)
        text = render_view(v, at(10, 30))
        self.assertIn("CHART PATTERNS", text)
        self.assertIn("HISTORICAL ANALOGS", text)
        self.assertIn("RBI policy", text)

    def test_poor_track_record_downgrades_confidence_and_adds_a_caution(self):
        v = fa.analyse(make_chain(), None, self.ctx(day_pct=-1.8, tone="risk-off", journal={"n": 10, "hit": 0.2}), at(10, 30))
        self.assertTrue(any("caution" in n for n in v.notes))
        self.assertNotEqual(v.confidence, "high")


class PatternTests(unittest.TestCase):
    def test_breakout_and_breakdown_are_named_with_signed_bias(self):
        flat = [100.0 + (i % 3) * 0.2 for i in range(90)]
        up = fp.read_chart(bars_from(flat + [103.0]))
        self.assertIn("20-day breakout", [p["name"] for p in up["patterns"]])
        self.assertGreater(up["bias"], 0)
        down = fp.read_chart(bars_from(flat + [97.0]))
        self.assertIn("20-day breakdown", [p["name"] for p in down["patterns"]])
        self.assertLess(down["bias"], 0)
        self.assertEqual(fp.read_chart(bars_from(flat[:30])), {})

    def test_squeeze_after_a_volatile_stretch(self):
        closes, c = [], 100.0
        for i in range(110):
            c *= 1.03 if i % 2 else 0.97
            closes.append(c)
        for i in range(30):
            c *= 1.001 if i % 2 else 0.999
            closes.append(c)
        chart = fp.read_chart(bars_from(closes))
        self.assertTrue(chart["squeeze"])
        self.assertTrue(chart["coiled"])
        self.assertLessEqual(chart["bw_pct"], 20)

    def test_rsi_and_realised_volatility(self):
        self.assertEqual(fp.rsi_series([float(i) for i in range(1, 40)])[-1], 100.0)
        closes, c = [], 100.0
        for i in range(30):
            c *= 1.01 if i % 2 else 0.99
            closes.append(c)
        self.assertAlmostEqual(fp.realised_vol_ann(closes), 0.01 * math.sqrt(252) * 100, delta=1.0)
        self.assertIsNone(fp.realised_vol_ann([1.0, 2.0]))

    def test_analogs_report_spaced_samples_and_need_enough_history(self):
        bars = bars_from(walk(400))
        a = fp.analogs(bars, 3)
        self.assertIsNotNone(a)
        self.assertGreaterEqual(a["n"], 10)
        self.assertEqual(len(a["rets"]), a["n"])
        self.assertTrue(0 <= a["up_rate"] <= 1)
        self.assertLessEqual(a["p10_pct"], a["p90_pct"])
        self.assertLessEqual(abs(a["bias"]), 2)
        self.assertIsNone(fp.analogs(bars[:120], 3))
        self.assertEqual(len(fp.empirical_returns(bars, 3)), len(bars) - 3)
        self.assertEqual(fp.trading_days(7), 5)
        self.assertEqual(fp.trading_days(30), 10)

    def test_analog_bias_is_zero_when_edge_is_weak(self):
        a = fp.analogs(bars_from(walk(400, seed=7, drift=0.0)), 3)
        if abs(a["up_rate"] - 0.5) < 0.1:
            self.assertEqual(a["bias"], 0.0)


class NewsTests(unittest.TestCase):
    NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)

    def item(self, title, hours=1.0):
        return news.Item(title, "u" + title, "s", self.NOW - timedelta(hours=hours))

    def test_headline_bias_signs_recency_and_staleness(self):
        pos = fno_news.headline_bias([self.item("Sensex surges as FII buying returns, record high")], self.NOW)
        neg = fno_news.headline_bias([self.item("Nifty plunges in sell-off as tariff fears escalate")], self.NOW)
        self.assertGreater(pos["bias"], 0)
        self.assertLess(neg["bias"], 0)
        old = fno_news.headline_bias([self.item("Nifty plunges in sell-off", hours=60)], self.NOW)
        self.assertEqual((old["bias"], old["n"]), (0.0, 0))
        fresh = fno_news.headline_bias([self.item("Market crash", hours=1)], self.NOW)["bias"]
        aged = fno_news.headline_bias([self.item("Market crash", hours=30)], self.NOW)["bias"]
        self.assertLess(fresh, aged)  # more negative when fresher
        self.assertLessEqual(fno_news.headline_bias([self.item(f"crash {i}") for i in range(20)], self.NOW)["bias"], 0)
        self.assertGreaterEqual(fno_news.headline_bias([self.item(f"crash {i}") for i in range(20)], self.NOW)["bias"], -2)

    def test_event_risk_levels(self):
        self.assertEqual(fno_news.event_risk([self.item("RBI proposes new fraud checks - India Today")], self.NOW)["level"], 0)
        self.assertEqual(fno_news.polarity("Nasdaq drops over 1% as AI rally fades - Mint"), -1)
        soon = fno_news.event_risk([self.item("RBI policy decision tomorrow: what to expect")], self.NOW)
        self.assertEqual(soon["level"], 2)
        self.assertIn("RBI policy", soon["tags"])
        near = fno_news.event_risk([self.item("Fed meeting expected next week")], self.NOW)
        self.assertEqual(near["level"], 1)
        self.assertEqual(fno_news.event_risk([self.item("RBI policy tomorrow", hours=48)], self.NOW)["level"], 0)
        self.assertEqual(fno_news.event_risk([self.item("Nifty ends flat")], self.NOW)["level"], 0)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def service(self, data):
        cfg = make_agent(data).cfg
        cfg.state_dir = self.tmp.name
        return fsvc.FnoService(cfg, FakeFno(), data)

    def test_history_pages_back_dedupes_and_caches_on_disk(self):
        calls = []

        class Paged(FakeData):
            def index_history(self, name, end, months=3):
                calls.append(end)
                n = len(calls)
                if n > 3:
                    return []
                top = date(2026, 11, 23) - timedelta(days=240 * (n - 1))
                return [Bar((top - timedelta(days=i)).isoformat(), 1, 2, 0.5, 1.5, 1) for i in range(240)][::-1]

        svc = self.service(Paged())
        bars = svc.history("NIFTY", date(2026, 11, 24))
        dates = [b.date for b in bars]
        self.assertEqual(dates, sorted(set(dates)))
        self.assertGreaterEqual(len(bars), 600)
        used = len(calls)
        again = self.service(Paged()).history("NIFTY", date(2026, 11, 24))
        self.assertEqual(len(again), len(bars))
        self.assertEqual(len(calls), used)  # second read came from the day's disk cache

    def test_history_stops_when_the_source_repeats_itself(self):
        svc = self.service(FakeData())  # the fake returns the same bars for every end date
        bars = svc.history("NIFTY", date(2026, 11, 24))
        self.assertEqual(len(bars), len({b.date for b in bars}))

    def test_brain_scales_analog_returns_and_survives_short_history(self):
        svc = self.service(FakeData())
        out = svc.brain("NIFTY", date(2026, 11, 24), 7.0)
        self.assertEqual(set(out), {"chart", "analog", "rets", "rv_ann"})
        svc.history = lambda symbol, today: bars_from(walk(400))
        svc.cache.clear()
        out = svc.brain("NIFTY", date(2026, 11, 24), 20.0)  # horizon capped at 10: returns are stretched to the expiry
        self.assertIsNotNone(out["analog"])
        self.assertGreater(max(abs(r) for r in out["rets"]), max(abs(r) for r in out["analog"]["rets"]))

    def test_journal_round_trip_and_hit_rate(self):
        self.assertIsNone(fsvc.journal_stats(self.tmp.name))
        for status in ("target", "stopped", "stopped", "target_hit", "pending"):
            fsvc.journal_add(self.tmp.name, {"id": "2026-11-24-FNO-NIFTY-range", "symbol": "NIFTY", "structure": "Iron condor (credit)",
                                             "mode": "range"}, status, 25000.0)
        st = fsvc.journal_stats(self.tmp.name)
        self.assertEqual(st["n"], 3)  # only closed statuses count
        self.assertAlmostEqual(st["hit"], 1 / 3)
        self.assertEqual(st["by"]["Iron condor (credit)"]["n"], 3)
        self.assertEqual(json.loads(Path(self.tmp.name, "fno_journal.jsonl").read_text().splitlines()[0])["date"], "2026-11-24")

    def test_headlines_are_cached_and_a_broken_feed_is_harmless(self):
        svc = self.service(FakeData())
        calls = []
        orig = news.fetch_feeds
        news.fetch_feeds = lambda urls: calls.append(1) or [news.Item("RBI policy decision tomorrow", "u", "s", datetime.now(timezone.utc))]
        self.addCleanup(setattr, news, "fetch_feeds", orig)
        bias, event = svc.headlines("NIFTY", at(10, 30))
        svc.headlines("NIFTY", at(10, 31))
        self.assertEqual(len(calls), 1)
        self.assertEqual(event["level"], 2)

        def boom(urls):
            raise RuntimeError("feed down")
        news.fetch_feeds = boom
        svc2 = self.service(FakeData())
        self.assertEqual(svc2.headlines("NIFTY", at(10, 30))[1]["level"], 0)


class NonDirectionalTrackingTests(unittest.TestCase):
    def idea(self, mode):
        return {"horizon": "intraday", "mode": mode, "status": "triggered", "lower": 24900.0, "upper": 25100.0,
                "side": "range", "entry": 25000.0, "stop": 24900.0, "target": 25100.0}

    def test_range_idea_is_stopped_on_a_touch_and_reminds_late(self):
        idea = self.idea("range")
        self.assertIsNone(Agent.advance(idea, 25010.0, at(11)))
        self.assertEqual(Agent.advance(idea, 25101.0, at(12)), "stopped")
        late = self.idea("range")
        self.assertEqual(Agent.advance(late, 25000.0, at(15, 20)), "exit_reminder")
        self.assertIsNone(Agent.advance(late, 25000.0, at(15, 25)))

    def test_breakout_idea_hits_target_either_way(self):
        self.assertEqual(Agent.advance(self.idea("breakout"), 24890.0, at(11)), "target")
        self.assertEqual(Agent.advance(self.idea("breakout"), 25110.0, at(11)), "target")


class CliAndMcpTests(unittest.TestCase):
    def run_cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(list(argv))
        return code, buf.getvalue()

    def test_strategies_command_lists_explains_and_rejects(self):
        code, out = self.run_cli("strategies")
        self.assertEqual(code, 0)
        self.assertIn("long_straddle", out)
        code, out = self.run_cli("strategies", "iron-condor")
        self.assertEqual(code, 0)
        self.assertIn("Breakeven at expiry", out)
        code, out = self.run_cli("strategies", "nonsense")
        self.assertEqual(code, 1)

    def test_mcp_strategy_guide_tool(self):
        b = object.__new__(fno_mcp.Backend)
        call = lambda args: fno_mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",  # noqa: E731
                                            "params": {"name": "fno_strategy_guide", "arguments": args}}, b)["result"]
        self.assertIn("bull_call", call({})["content"][0]["text"])
        self.assertIn("Bull call spread", call({"name": "bull call"})["content"][0]["text"])
        self.assertTrue(call({"name": "nope"})["isError"])


if __name__ == "__main__":
    unittest.main()
