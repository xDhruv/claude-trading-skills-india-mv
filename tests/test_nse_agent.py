"""Tests for the standalone NSE agent (agent/nse_agent). No network: MCP data and email are faked."""
import pathlib
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "agent"))

from nse_agent import analysis, clock, engine, inbox, mcp, news  # noqa: E402
from nse_agent.config import Config  # noqa: E402
from nse_agent.engine import Agent  # noqa: E402
from nse_agent.nse import Bar, IndexQuote, Mover, Quote, parse_indian  # noqa: E402
from nse_agent.state import State  # noqa: E402

IST = clock.IST
TODAY = date(2026, 11, 24)  # a Tuesday


def at(h, m=0, day=TODAY):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=IST)


def uptrend_bars(n=60, start=100.0, step=0.5, volume=1_000_000):
    bars = []
    for i in range(n):
        c = start + i * step
        d = (date(2026, 8, 1) + timedelta(days=i)).isoformat()
        bars.append(Bar(d, c - 0.3, c + 1.0, c - 1.0, c, volume))
    return bars


def quote(symbol="ABC", ltp=133.0, pct=2.4, volume=900_000, ts=None, **kw):
    base = dict(symbol=symbol, series="EQ", ltp=ltp, open=130.0, high=ltp, low=129.5, prev=ltp / (1 + pct / 100),
                pct=pct, volume=volume, high52=ltp * 1.01, low52=80.0, ts=ts or at(10, 30))
    base.update(kw)
    return Quote(**base)


class FakeData:
    def __init__(self, live=True):
        ts = at(10, 29) if live else at(15, 30, date(2026, 11, 23))
        self.nifty = IndexQuote("NIFTY 50", 25000, 24900, 0.4, 24950, 25050, 24900, ts)
        self.vix = IndexQuote("INDIA VIX", 14, 14.2, -1.4, 14, 14.5, 13.8, ts)
        self.quotes = {"ABC": quote()}
        self.bars = uptrend_bars()

    def index_quote(self, name):
        return self.vix if "VIX" in name else self.nifty

    def stock_quote(self, symbol):
        return self.quotes[symbol]

    def movers(self, index="", limit=25):
        return [Mover("ABC", "EQ", 133.0, 2.4, 300.0)], []

    def history(self, symbol, end, months=3):
        return self.bars

    def index_history(self, name, end, months=3):
        return uptrend_bars(60, 24000, 10)

    def sma(self, symbol, days):
        return 110.0

    def corporate_actions(self, symbol, start, end):
        return []

    def outperformance(self, symbol, end, months=3):
        return 6.5

    def breadth(self, day):
        return {"ad_ratio": 1.4}


class FakeMailer:
    def __init__(self):
        self.sent = []

    def send(self, subject, body, reply_to=None):
        self.sent.append((subject, body, reply_to))
        return f"<{len(self.sent)}@test>"


def make_agent(data, **cfg_kw):
    cfg = Config(watchlist=("ABC",), capital=500000, risk_pct=1.0, dry_run=True, **cfg_kw)
    tmp = tempfile.TemporaryDirectory()
    agent = Agent(cfg, data, FakeMailer(), State(tmp.name))
    agent._tmp = tmp
    return agent


class ParsingTests(unittest.TestCase):
    def test_indian_units(self):
        self.assertEqual(parse_indian("1,248.68 L"), 124868000.0)
        self.assertEqual(parse_indian("474.25 Cr"), 4742500000.0)
        self.assertEqual(parse_indian("44.18 K"), 44180.0)
        self.assertEqual(parse_indian(12), 12.0)

    def test_nse_timestamps(self):
        self.assertEqual(clock.parse_nse_ts("23-Nov-2026 15:30:00"), at(15, 30, date(2026, 11, 23)))
        self.assertEqual(clock.parse_nse_ts("2026-11-24 10:29:00"), at(10, 29))
        self.assertIsNone(clock.parse_nse_ts("garbage"))

    def test_sse_and_json_decode(self):
        self.assertEqual(mcp._decode('{"a": 1}'), {"a": 1})
        self.assertEqual(mcp._decode('event: message\ndata: {"a": 2}\n\n'), {"a": 2})

    def test_phases(self):
        self.assertEqual(clock.phase(at(8, 0)), "before")
        self.assertEqual(clock.phase(at(9, 10)), "preopen")
        self.assertEqual(clock.phase(at(12)), "open")
        self.assertEqual(clock.phase(at(16)), "after")
        self.assertEqual(clock.phase(at(12, day=date(2026, 11, 28))), "weekend")

    def test_expected_volume_fraction_is_front_loaded(self):
        self.assertGreater(clock.expected_volume_fraction(at(10, 15)), 60 / 375)
        self.assertEqual(clock.expected_volume_fraction(at(15, 30)), 1.0)


class AnalysisTests(unittest.TestCase):
    def test_breakout_idea_and_plan(self):
        bars = uptrend_bars()
        hit = analysis.evaluate(quote(), bars, 110.0, 6.5, at(10, 30))
        self.assertEqual(hit.setup, "breakout")
        p = analysis.plan(hit, quote(), bars, 500000, 1.0, "zerodha")
        self.assertIsNotNone(p)
        self.assertGreater(p["entry"], 133.0)
        self.assertLess(p["stop"], p["entry"])
        self.assertGreater(p["target"], p["entry"])
        self.assertGreaterEqual(p["rr_net"], 2.0)
        self.assertLessEqual(p["qty"] * p["entry"], 500000 * 0.25 + p["entry"])
        self.assertLessEqual(p["risk_rs"], 500000 * 0.01 * 1.3)

    def test_no_idea_on_light_volume_or_below_200dma(self):
        bars = uptrend_bars()
        self.assertIsNone(analysis.evaluate(quote(volume=100_000), bars, 110.0, 6.5, at(10, 30)))
        self.assertIsNone(analysis.evaluate(quote(), bars, 200.0, 6.5, at(10, 30)))

    def test_underperformer_is_rejected(self):
        self.assertIsNone(analysis.evaluate(quote(), uptrend_bars(), 110.0, -2.0, at(10, 30)))

    def test_short_history_and_odd_series_skipped(self):
        self.assertIsNone(analysis.evaluate(quote(), uptrend_bars(30), 110.0, 1.0, at(10, 30)))
        self.assertIsNone(analysis.evaluate(quote(series="SM"), uptrend_bars(), 110.0, 1.0, at(10, 30)))

    def test_held_stock_risk_alert(self):
        q = quote(ltp=120.0, pct=-5.0, volume=2_000_000, open=127.0, high=127.5, low=119.5, prev=126.3)
        hit = analysis.evaluate(q, uptrend_bars(), 110.0, 1.0, at(10, 30), held=True)
        self.assertEqual((hit.setup, hit.kind), ("risk_alert", "alert"))

    def test_tone(self):
        self.assertEqual(analysis.tone(0.5, 25000, 24500, -1, 1.2), "trending up")
        self.assertEqual(analysis.tone(-1.8, 24000, 24500, 3, 0.5), "risk-off")
        self.assertEqual(analysis.tone(-0.2, 25000, 24500, 1, 1.0), "choppy")

    def test_tick_rounding(self):
        self.assertEqual(analysis.round_up(100.01), 100.05)
        self.assertEqual(analysis.round_down(100.04), 100.0)


class NewsTests(unittest.TestCase):
    RSS = (b"<rss><channel><item><title>RBI signals repo rate hike; Nifty falls</title><link>http://x/1</link>"
           b"<pubDate>Tue, 24 Nov 2026 04:00:00 GMT</pubDate></item>"
           b"<item><title>Cooking tips &amp; tricks</title><link>http://x/2</link></item>"
           b"<item><title>Infosys faces SEBI probe</title><link>http://x/3</link>"
           b"<pubDate>Tue, 24 Nov 2026 04:10:00 GMT</pubDate></item></channel></rss>")

    def test_parse_score_and_negative(self):
        items = news.parse_rss(self.RSS, "x")
        self.assertEqual(len(items), 3)
        now = datetime(2026, 11, 24, 5, 0, tzinfo=clock.timezone.utc)
        rel = news.fresh_relevant(items, set(), ["INFY"], now)
        titles = [i.title for i in rel]
        self.assertIn("RBI signals repo rate hike; Nifty falls", titles)
        self.assertNotIn("Cooking tips & tricks", titles)
        infy = next(i for i in rel if "Infosys" in i.title)
        self.assertEqual(infy.symbols, ("INFY",))
        self.assertTrue(news.is_negative(infy))

    def test_seen_and_stale_are_dropped(self):
        items = news.parse_rss(self.RSS, "x")
        now = datetime(2026, 11, 24, 5, 0, tzinfo=clock.timezone.utc)
        seen = {i.key for i in items}
        self.assertEqual(news.fresh_relevant(items, seen, ["INFY"], now), [])
        later = now + timedelta(days=2)
        self.assertEqual(news.fresh_relevant(items, set(), ["INFY"], later), [])


class StateTests(unittest.TestCase):
    def test_rollover_once_and_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            st = State(d)
            st.rollover("2026-11-24")
            self.assertTrue(st.once("preopen"))
            self.assertFalse(st.once("preopen"))
            st.data["emails"] = 3
            st.seen_news.append("k")
            st.save()
            again = State(d)
            self.assertEqual((again.data["emails"], again.seen_news), (3, ["k"]))
            again.rollover("2026-11-25")
            self.assertEqual(again.data["emails"], 0)
            self.assertEqual(again.seen_news, ["k"])

    def test_due_interval(self):
        with tempfile.TemporaryDirectory() as d:
            st = State(d)
            self.assertTrue(st.due("scan", at(10), 15))
            st.mark("scan", at(10))
            self.assertFalse(st.due("scan", at(10, 10), 15))
            self.assertTrue(st.due("scan", at(10, 15), 15))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self._patches = [(news, "fetch_feeds", news.fetch_feeds), (news, "symbol_news", news.symbol_news)]
        news.fetch_feeds = lambda urls: []
        news.symbol_news = lambda symbol, now, hours=36: []

    def tearDown(self):
        for mod, name, orig in self._patches:
            setattr(mod, name, orig)

    def test_scan_emails_one_idea_once(self):
        agent = make_agent(FakeData())
        self.assertIn("scan", agent.tick(at(10, 30)))
        self.assertEqual(len(agent.mailer.sent), 1)
        subject, body, _ = agent.mailer.sent[0]
        self.assertIn("Idea: ABC", subject)
        self.assertIn("Trigger: buy above", body)
        self.assertIn("not investment advice", body)
        agent.tick(at(10, 50))  # same stock again: no duplicate idea
        self.assertEqual(len(agent.mailer.sent), 1)

    def test_no_activity_when_data_is_stale(self):
        agent = make_agent(FakeData(live=False))
        self.assertTrue(agent.tick(at(11, 0)).startswith("idle"))
        self.assertEqual(agent.mailer.sent, [])

    def test_weekend_is_idle(self):
        agent = make_agent(FakeData())
        self.assertEqual(agent.tick(at(11, day=date(2026, 11, 28))), "weekend")

    def test_negative_news_vetoes_idea(self):
        news.symbol_news = lambda symbol, now, hours=36: [news.Item("ABC faces fraud probe", "u", "s", None)]
        agent = make_agent(FakeData())
        agent.tick(at(10, 30))
        self.assertEqual(agent.mailer.sent, [])
        self.assertEqual(agent.state.data["ideas"], {})

    def test_risk_off_tape_blocks_ideas(self):
        data = FakeData()
        data.nifty = IndexQuote("NIFTY 50", 24000, 24900, -1.8, 24800, 24850, 23950, at(10, 29))
        agent = make_agent(data)
        agent.tick(at(10, 30))
        self.assertEqual(agent.state.data["ideas"], {})
        self.assertTrue(any("Market alert" in s for s, _, _ in agent.mailer.sent))

    def test_email_cap(self):
        agent = make_agent(FakeData(), max_emails_per_day=0)
        agent.tick(at(10, 30))
        self.assertEqual(agent.mailer.sent, [])

    def test_idea_lifecycle(self):
        idea = {"horizon": "swing", "status": "pending", "entry": 134.0, "stop": 128.0, "target": 147.0}
        self.assertIsNone(Agent.advance(idea, 133.0, at(11)))
        self.assertEqual(Agent.advance(idea, 134.1, at(11)), "triggered")
        self.assertEqual(Agent.advance(idea, 147.5, at(12)), "target")
        stopped = dict(idea, status="triggered")
        self.assertEqual(Agent.advance(stopped, 127.9, at(12)), "stopped")
        dead = dict(idea, status="pending")
        self.assertEqual(Agent.advance(dead, 127.0, at(12)), "invalidated")

    def test_intraday_expires_and_reminds(self):
        pend = {"horizon": "intraday", "status": "pending", "entry": 134.0, "stop": 128.0, "target": 147.0}
        self.assertEqual(Agent.advance(pend, 133.0, at(15, 20)), "expired")
        trig = dict(pend, status="triggered")
        self.assertEqual(Agent.advance(trig, 135.0, at(15, 20)), "exit_reminder")
        self.assertIsNone(Agent.advance(trig, 135.0, at(15, 25)))

    def test_track_emails_a_trigger_in_thread(self):
        agent = make_agent(FakeData())
        agent.tick(at(10, 30))
        idea = next(iter(agent.state.data["ideas"].values()))
        agent.data.quotes["ABC"] = quote(ltp=idea["entry"] + 0.5)
        agent.tick(at(10, 40))
        subject, body, reply_to = agent.mailer.sent[-1]
        self.assertIn("Update: ABC", subject)
        self.assertEqual(reply_to, idea["msgid"])


class InboxTests(unittest.TestCase):
    def test_command_parsing(self):
        self.assertEqual(inbox.command_from("[NSE Ask] price TCS", ""), "price TCS")
        self.assertEqual(inbox.command_from("Re: [NSE Ask]", "> quoted\nnews INFY\n"), "news INFY")

    def test_answers_are_read_only(self):
        agent = make_agent(FakeData())
        out = inbox.answer("price ABC", agent.data, agent, at(11))
        self.assertIn("ABC", out)
        self.assertIn("NIFTY 50", inbox.answer("nifty", agent.data, agent, at(11)))
        self.assertIn("Commands", inbox.answer("buy 100 ABC", agent.data, agent, at(11)))
        self.assertIn("No ideas", inbox.answer("ideas", agent.data, agent, at(11)))


if __name__ == "__main__":
    unittest.main()
