"""The agent loop body: one `tick()` decides what to do right now and emails only when there is something to say."""
import logging
from datetime import date, datetime, time, timedelta, timezone

from . import analysis, clock, fno_render, llm, news, render
from .fno_data import INDEX_SYMBOLS, FnoError, NseFno
from .fno_service import FnoService, journal_add
from .fno_source import make_fno
from .nse import Bar, NseData, McpError

log = logging.getLogger("nse_agent")
SPLIT_WORDS = ("split", "bonus", "demerger", "spin", "consolidation", "arrangement", "amalgamation")
FNO_ERRORS = (FnoError, McpError, KeyError, TypeError, ValueError)


class Agent:
    def __init__(self, cfg, data: NseData, mailer, state, fno: NseFno | None = None):
        self.cfg, self.data, self.mailer, self.state = cfg, data, mailer, state
        self._fno_client, self._fno_service = fno, None

    def fno_service(self) -> FnoService:
        if self._fno_service is None:
            self._fno_client = self._fno_client or make_fno(self.cfg)
            self._fno_service = FnoService(self.cfg, self._fno_client, self.data)
        return self._fno_service

    def fno_view(self, symbol: str, now: datetime, tone: str | None = None):
        """Full F&O view for one underlying, with whatever live context the cash feeds can add."""
        vix = vix_pct = None
        try:
            q = self.data.index_quote("INDIA VIX")
            vix, vix_pct = q.last, q.pct
        except FNO_ERRORS:
            pass
        tone = tone or self.state.data["context"].get("tone")
        negative = tuple(i.title for i in news.symbol_news(symbol, now) if news.is_negative(i)) \
            if symbol not in INDEX_SYMBOLS else ()
        return self.fno_service().view(symbol, now, tone, vix, vix_pct, negative)

    # ---- email -----------------------------------------------------------------------------
    def email(self, subject: str, body: str, reply_to: str | None = None) -> str | None:
        if self.state.data["emails"] >= self.cfg.max_emails_per_day:
            log.warning("daily email cap reached, suppressed: %s", subject)
            return None
        msgid = self.mailer.send(subject, body, reply_to)
        if msgid:
            self.state.data["emails"] += 1
        return msgid

    # ---- data helpers ----------------------------------------------------------------------
    def live_index(self, now: datetime):
        """NIFTY quote if NSE's own timestamp is from today and recent, else None (holiday, closed, stale)."""
        nifty = self.data.index_quote("NIFTY 50")
        if nifty.ts and nifty.ts.date() == now.date() and now - nifty.ts <= timedelta(minutes=20):
            return nifty
        return None

    def context(self, now: datetime) -> dict:
        """Once-a-day data from Bhavcopy: NIFTY 50-day SMA and the previous session's breadth."""
        ctx = self.state.data["context"]
        if ctx.get("ready"):
            return ctx
        try:
            hist = self.data.index_history("NIFTY 50", now.date())
            closes = [b.close for b in hist if b.date != now.date().isoformat()]
            ctx["nifty_sma50"] = analysis.sma(closes, 50)
            ctx["ad_ratio"] = self.data.breadth(now.date()).get("ad_ratio")
            ctx["ready"] = True
        except (McpError, KeyError, TypeError) as exc:
            log.warning("context unavailable: %s", exc)
        return ctx

    def history(self, symbol: str, today: date) -> dict | None:
        cache = self.state.data["cache"]
        if symbol in cache:
            return cache[symbol]
        try:
            bars = [b for b in self.data.history(symbol, today) if b.date < today.isoformat()]
            sma200 = self.data.sma(symbol, 200)
            actions = self.data.corporate_actions(symbol, today - timedelta(days=90), today)
            split = any(w in str(a).lower() for a in actions for w in SPLIT_WORDS)
            outperf = self.data.outperformance(symbol, today)
        except (McpError, KeyError, TypeError, ValueError) as exc:
            log.warning("history failed for %s: %s", symbol, exc)
            return None
        entry = {"bars": [[b.date, b.open, b.high, b.low, b.close, b.volume] for b in bars],
                 "sma200": sma200, "split": split, "outperf": outperf}
        cache[symbol] = entry
        return entry

    @staticmethod
    def bars_of(entry: dict) -> list[Bar]:
        return [Bar(*r) for r in entry["bars"]]

    def symbols_of_interest(self) -> list[str]:
        active = [i["symbol"] for i in self.state.data["ideas"].values() if i.get("kind") != "fno"]
        return list(dict.fromkeys([*self.cfg.watchlist, *self.cfg.holdings, *active]))

    # ---- the main entry ---------------------------------------------------------------------
    def tick(self, now: datetime | None = None) -> str:
        now = now or clock.now_ist()
        st, cfg = self.state, self.cfg
        st.rollover(now.date().isoformat())
        phase = clock.phase(now)
        done = []
        try:
            if phase in ("weekend",):
                return "weekend"
            t = now.time()
            if phase in ("before", "preopen", "open") and time(8, 45) <= t < time(10, 0) and st.once("preopen"):
                self.preopen(now)
                done.append("preopen")
            if phase == "open" or (phase == "after" and t <= time(15, 45)):
                done += self.during_market(now, phase)
            if phase == "after" and t >= time(15, 40) and st.data["context"].get("had_live") and st.once("close"):
                self.close_wrap(now)
                done.append("close")
            if phase != "before" and st.due("news", now, cfg.news_interval_min) and t < time(21, 0):
                st.mark("news", now)
                if self.news_cycle(now):
                    done.append("news")
            if cfg.can_email() and st.due("inbox", now, cfg.inbox_interval_min) and time(8, 30) <= t <= time(16, 30):
                st.mark("inbox", now)
                from .inbox import check_inbox
                if check_inbox(cfg, self.data, self):
                    done.append("inbox")
        finally:
            st.save()
        return ",".join(done) or f"idle({phase})"

    # ---- market hours -----------------------------------------------------------------------
    def during_market(self, now: datetime, phase: str) -> list[str]:
        st, cfg = self.state, self.cfg
        nifty = self.live_index(now)
        if nifty is None:
            log.info("no live NSE data (holiday, closed or stale)")
            return []
        st.data["context"]["had_live"] = True
        if phase == "after":
            return []
        done = []
        vix = self.data.index_quote("INDIA VIX")
        ctx = self.context(now)
        mood = analysis.tone(nifty.pct, nifty.last, ctx.get("nifty_sma50"), vix.pct, ctx.get("ad_ratio"))
        st.data["context"]["tone"] = mood
        if st.due("track", now, cfg.track_interval_min):
            st.mark("track", now)
            if self.track(now):
                done.append("track")
        if self.market_alerts(now, nifty, vix, mood):
            done.append("market")
        if now.time() >= time(9, 45) and st.due("scan", now, cfg.scan_interval_min):
            st.mark("scan", now)
            if self.scan(now, nifty, vix, mood):
                done.append("scan")
        if (cfg.fno_enabled and time(9, 50) <= now.time() <= time(14, 45)
                and st.due("fno", now, cfg.fno_interval_min)):
            st.mark("fno", now)
            if self.fno_scan(now, mood):
                done.append("fno")
        return done

    def market_alerts(self, now, nifty, vix, mood) -> bool:
        lines, keys = [], []
        if abs(nifty.pct) >= 1:
            bucket = int(nifty.pct)
            keys.append(f"nifty:{bucket}")
            lines.append(f"NIFTY {nifty.last:,.0f} is {nifty.pct:+.2f}% on the day (tape: {mood}).")
        if vix.pct >= 8 or vix.last >= 20:
            keys.append(f"vix:{int(vix.last // 2)}")
            lines.append(f"INDIA VIX {vix.last:.1f} ({vix.pct:+.1f}% today). Expect wider swings; "
                         "size down and respect stops.")
        keys = [k for k in keys if k not in self.state.data["alerts"]]
        if not keys:
            return False
        self.state.data["alerts"] += keys
        self.email(f"[NSE Desk] Market alert: {now:%d %b %Y %H:%M}", render.render_market_alert(lines, now))
        return True

    # ---- scanning ---------------------------------------------------------------------------
    def candidates(self) -> list[tuple[str, bool]]:
        cfg = self.cfg
        pool: dict[str, float] = {}
        try:
            g, l = self.data.movers("", 50)
            for m in g + l:
                if m.series == "EQ" and m.ltp > 50 and m.turnover_cr >= cfg.min_turnover_cr and abs(m.pct) >= 1:
                    pool[m.symbol] = m.turnover_cr
            for idx in ("NIFTY", "NIFTYNEXT50", "BANKNIFTY"):
                g, l = self.data.movers(idx, 15)
                for m in g + l:
                    if m.series == "EQ" and abs(m.pct) >= 1:
                        pool.setdefault(m.symbol, m.turnover_cr)
        except (McpError, KeyError, TypeError) as exc:
            log.warning("movers failed: %s", exc)
        ranked = sorted(pool, key=lambda s: -pool[s])[: cfg.max_candidates]
        held = set(cfg.holdings)
        out = [(s, s in held) for s in ranked]
        for s in self.symbols_of_interest():
            if s not in ranked:
                out.append((s, s in held))
        return out

    def scan(self, now, nifty, vix, mood) -> bool:
        st, cfg = self.state, self.cfg
        today = now.date()
        ideas, watch, alerts, vetoed = [], [], [], []
        for symbol, held in self.candidates():
            try:
                q = self.data.stock_quote(symbol)
            except (McpError, KeyError, TypeError, ValueError):
                continue
            if not q.ts or q.ts.date() != today:
                continue
            h = self.history(symbol, today)
            if not h or h["split"]:
                continue
            bars = self.bars_of(h)
            hit = analysis.evaluate(q, bars, h["sma200"], h["outperf"], now, held)
            if not hit:
                continue
            key = f"{hit.kind}:{symbol}:{hit.setup}"
            if hit.kind in ("watch", "alert"):
                if key not in st.data["alerts"]:
                    st.data["alerts"].append(key)
                    (watch if hit.kind == "watch" else alerts).append({"symbol": symbol, "reasons": hit.reasons})
                continue
            if mood == "risk-off" or any(i["symbol"] == symbol for i in st.data["ideas"].values()):
                continue
            late = now.time() >= (time(14, 30) if hit.horizon == "intraday" else time(15, 0))
            if late:
                continue
            p = analysis.plan(hit, q, bars, cfg.capital, cfg.risk_pct, cfg.broker)
            if not p:
                continue
            headlines = news.symbol_news(symbol, now)
            if any(news.is_negative(i) for i in headlines):
                vetoed.append(symbol)
                continue
            p["news"] = [i.title[:90] for i in headlines[:2]]
            ideas.append((hit.metrics.get("vol_ratio", 0), p))
        ideas.sort(key=lambda x: -x[0])
        room = max(0, cfg.max_ideas_per_day - sum(1 for i in st.data["ideas"].values() if i.get("kind") != "fno"))
        chosen = [p for _, p in ideas[: min(cfg.max_ideas_per_scan, room)]]
        if not (chosen or watch or alerts):
            return False
        subject = f"[NSE Desk] {'Idea' if chosen else 'Watch'}: " + ", ".join(
            [p["symbol"] for p in chosen] or [w["symbol"] for w in watch + alerts]) + f" ({now:%d %b %H:%M})"
        msgid = self.email(subject, render.render_scan(now, mood, nifty, vix, chosen, watch, alerts, vetoed))
        for p in chosen:
            p["msgid"] = msgid
            p["id"] = f"{today}-{p['symbol']}"
            st.data["ideas"][p["id"]] = p
        return True

    # ---- tracking ---------------------------------------------------------------------------
    def track(self, now: datetime) -> bool:
        events, fno_events = [], []
        for idea in self.state.data["ideas"].values():
            if idea["status"] not in ("pending", "triggered"):
                continue
            fno = idea.get("kind") == "fno"
            try:
                if fno and idea.get("index_name"):
                    price = self.data.index_quote(idea["index_name"]).last
                else:
                    price = self.data.stock_quote(idea["symbol"]).ltp
            except (McpError, KeyError, TypeError, ValueError):
                continue
            ev = self.advance(idea, price, now)
            if ev:
                (fno_events if fno else events).append((idea, ev, price))
                if fno and ev in ("target", "stopped", "invalidated", "expired"):
                    journal_add(self.cfg.state_dir, idea, ev, price)
        sent = False
        if events:
            self.email(f"[NSE Desk] Update: {', '.join(sorted({i['symbol'] for i, _, _ in events}))}",
                       render.render_updates(events, now), reply_to=events[0][0].get("msgid"))
            sent = True
        if fno_events:
            body = "\n\n".join(fno_render.render_update(i, ev, p, now) for i, ev, p in fno_events)
            self.email(f"[NSE Desk] F&O update: {', '.join(sorted({i['symbol'] for i, _, _ in fno_events}))}",
                       body + "\n\n" + fno_render.FNO_WARNING, reply_to=fno_events[0][0].get("msgid"))
            sent = True
        return sent

    @staticmethod
    def advance(idea: dict, price: float, now: datetime) -> str | None:
        """Move an idea through pending -> triggered -> target/stopped. Short ideas mirror the long logic."""
        late = idea["horizon"] == "intraday" and now.time() >= time(15, 15)
        if idea.get("mode"):  # range or breakout structure: watched between two levels
            if idea["status"] != "triggered":
                return None
            if price >= idea["upper"] or price <= idea["lower"]:
                idea["status"] = "stopped" if idea["mode"] == "range" else "target_hit"
                return "stopped" if idea["mode"] == "range" else "target"
            if late and not idea.get("reminded"):
                idea["reminded"] = True
                return "exit_reminder"
            return None
        sign = -1 if idea.get("side") == "short" else 1
        price, entry, stop, target = (sign * v for v in (price, idea["entry"], idea["stop"], idea["target"]))
        if idea["status"] == "pending":
            if price <= stop:
                idea["status"] = "invalidated"
                return "invalidated"
            if price >= entry:
                idea["status"] = "triggered"
                return "triggered"
            if late:
                idea["status"] = "expired"
                return "expired"
        elif idea["status"] == "triggered":
            if price >= target:
                idea["status"] = "target_hit"
                return "target"
            if price <= stop:
                idea["status"] = "stopped"
                return "stopped"
            if late and not idea.get("reminded"):
                idea["reminded"] = True
                return "exit_reminder"
        return None

    # ---- futures & options ------------------------------------------------------------------
    def fno_stock_picks(self, svc: FnoService) -> list[str]:
        """Stocks whose futures+options open interest moved most today, limited to your own watchlist and holdings."""
        cfg = self.cfg
        mine = set(cfg.watchlist) | set(cfg.holdings)
        rows = [(abs(r["pct"]), s) for s, r in svc.spurts().items() if s in mine and s not in INDEX_SYMBOLS and r["pct"]]
        return [s for _, s in sorted(rows, reverse=True)[: cfg.fno_stocks_per_scan]]

    def fno_scan(self, now: datetime, mood: str | None) -> bool:
        st, cfg = self.state, self.cfg
        svc = self.fno_service()
        hist = st.data.setdefault("fno_hist", {})
        views, errors = [], []
        try:
            symbols = [*cfg.fno_underlyings, *self.fno_stock_picks(svc)]
        except FNO_ERRORS:
            symbols = list(cfg.fno_underlyings)
        vix = self.data.index_quote("INDIA VIX")
        for sym in symbols:
            try:
                first = hist.get(sym, [None])[0]
                prev_pcr = first[1] if first and (now - datetime.fromisoformat(first[0])) >= timedelta(minutes=20) else None
                negative = tuple(i.title for i in news.symbol_news(sym, now) if news.is_negative(i)) \
                    if sym not in INDEX_SYMBOLS else ()
                outperf = None
                if sym not in INDEX_SYMBOLS:
                    h = self.history(sym, now.date())
                    outperf = h["outperf"] if h else None
                v = svc.view(sym, now, mood, vix.last, vix.pct, negative, prev_pcr, outperf)
            except FNO_ERRORS as exc:
                errors.append(f"{sym}: {exc}")
                log.warning("F&O view failed for %s: %s", sym, exc)
                continue
            if v.stale:
                errors.append(f"{sym}: option-chain data is not from the live session")
                continue
            hist.setdefault(sym, []).append([now.isoformat(), v.metrics["pcr_oi"]])
            views.append(v)
        if not views:
            return False
        today = now.date()
        taken = sum(1 for i in st.data["ideas"].values() if i.get("kind") == "fno")
        fresh = []
        for v in views:
            key = f"fno:{v.symbol}:{(v.plan or {}).get('kind') or v.direction}"
            if v.plan and key not in st.data["alerts"] and taken + len(fresh) < cfg.max_fno_ideas_per_day \
                    and now.time() < time(14, 30):
                st.data["alerts"].append(key)
                fresh.append(v)
        pulse = st.once("fno_pulse")
        if not (fresh or pulse):
            return False
        sent = None
        if fresh:
            subject = "[NSE Desk] F&O idea: " + ", ".join(f"{v.symbol} {v.plan.get('kind') or v.direction}" for v in fresh) + f" ({now:%d %b %H:%M})"
            body = "\n\n" + ("=" * 60 + "\n\n").join(fno_render.render_view(v, now) for v in fresh)
            sent = self.email(subject, body.lstrip())
        else:
            sent = self.email(f"[NSE Desk] F&O pulse ({now:%d %b %H:%M})", fno_render.render_pulse(views, errors, now))
        for v in fresh:
            lv, p = v.plan["levels"], v.plan
            idea_id = f"{today}-FNO-{v.symbol}-{p.get('kind') or v.direction}"
            base = {"id": idea_id, "kind": "fno", "symbol": v.symbol, "index_name": INDEX_SYMBOLS.get(v.symbol),
                    "structure": p["structure"], "horizon": "intraday", "msgid": sent, "setup": "f&o " + p["structure"]}
            if lv.get("sg"):
                st.data["ideas"][idea_id] = {**base, "side": "long" if lv["sg"] > 0 else "short", "entry": lv["trigger"],
                                             "stop": lv["stop"], "target": lv["target"], "status": "pending"}
            else:  # range or breakout structure: no entry trigger, watched between two levels
                st.data["ideas"][idea_id] = {**base, "mode": p["kind"], "lower": lv["lower"], "upper": lv["upper"],
                                             "side": "range", "entry": v.spot, "stop": lv["lower"], "target": lv["upper"],
                                             "status": "triggered"}
        return True

    # ---- news -------------------------------------------------------------------------------
    def news_cycle(self, now: datetime) -> bool:
        st, cfg = self.state, self.cfg
        items = news.fetch_feeds(cfg.news_feeds)
        seen = set(st.seen_news)
        symbols = self.symbols_of_interest()
        utc_now = now.astimezone(timezone.utc)
        relevant = news.fresh_relevant(items, seen, symbols, utc_now)
        if not relevant:
            return False
        active = {i["symbol"] for i in st.data["ideas"].values() if i["status"] in ("pending", "triggered")}
        urgent = [i for i in relevant if set(i.symbols) & active and news.is_negative(i)]
        gap_ok = st.due("news_email", now, cfg.news_email_gap_min)
        strong = relevant[0].score >= 5 or sum(1 for i in relevant if i.score >= 3) >= 3
        if not (urgent or (gap_ok and strong)):
            return False
        batch = (urgent + [i for i in relevant if i not in urgent])[:5]
        ctx = st.data["context"]
        snap = f"tape {ctx.get('tone', 'unknown')}" if ctx.get("tone") else "market status unknown"
        summary = llm.summarise_news(cfg, [i.title for i in batch], snap)
        head = "News on an active idea" if urgent else "Market news"
        self.email(f"[NSE Desk] {head}: {now:%d %b %H:%M}", render.render_news(batch, summary, now, head))
        st.mark("news_email", now)
        st.seen_news += [i.key for i in batch]
        return True

    # ---- daily briefs -----------------------------------------------------------------------
    def preopen(self, now: datetime) -> None:
        d = now.date()
        mood = self.data.mood(d)
        bench = mood.get("benchmarks", {})
        n50 = bench.get("Nifty 50", {})
        v = mood.get("india_vix", {})
        sb = mood.get("stock_breadth", {})
        sectors = ""
        try:
            mv = self.data.index_movers(date.fromisoformat(mood["date"]))
            sectors = ("Sectors up: " + ", ".join(f"{x['index']} {x['return_pct']:+.1f}%" for x in mv["gainers"])
                       + ". Sectors down: " + ", ".join(f"{x['index']} {x['return_pct']:+.1f}%" for x in mv["losers"]) + ".")
        except (McpError, KeyError, TypeError):
            pass
        items = news.fresh_relevant(news.fetch_feeds(self.cfg.news_feeds), set(self.state.seen_news),
                                    self.symbols_of_interest(), now.astimezone(timezone.utc),
                                    max_age_h=14, min_score=2)[:5]
        summary = llm.summarise_news(self.cfg, [i.title for i in items], "pre-open")
        body = render.render_preopen(
            f"NIFTY 50 closed {n50.get('close', 0):,.0f} ({n50.get('change_pct', 0):+.2f}%) on {mood.get('date')}.",
            f"INDIA VIX {v.get('level', 0)} ({v.get('change_1d_pct', 0):+.1f}% 1d, "
            f"{v.get('range', {}).get('percentile', 0):.0f}th percentile of the year, {v.get('vs_1w_ago', {}).get('direction', '')}).",
            f"Stocks up {sb.get('up', 0)} vs down {sb.get('down', 0)} ({sb.get('reading', '')}).",
            sectors, [f"- {i.title}" for i in items], summary)
        self.email(f"[NSE Desk] Pre-open: {now:%d %b %Y}", body)
        self.state.seen_news += [i.key for i in items]

    def close_wrap(self, now: datetime) -> None:
        nifty = self.data.index_quote("NIFTY 50")
        ideas = list(self.state.data["ideas"].values())
        watch = [k.split(":")[1] for k in self.state.data["alerts"] if k.startswith("watch:")]
        self.email(f"[NSE Desk] Close: {now:%d %b %Y}", render.render_close(now, nifty, ideas, watch, None))
