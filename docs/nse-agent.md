# NSE agent: an always-on, free trading desk that emails you

`agent/` is a standalone Python program (standard library only, Python 3.10+). It does not need Claude, a
paid API or a server. Through the trading day it watches NSE live data and free news, checks both against the
rules in the [`nse-live-desk`](../skills/nse-live-desk/SKILL.md) skill, and emails you through Gmail only when
there is something worth saying.

It never places orders and never emails anyone but you.

## What it does

| When (IST, Mon to Fri) | What you get |
|---|---|
| About 08:45 to 09:15 | **Pre-open brief**: last close, India VIX, breadth, sector leaders and laggards, top news. |
| From 09:45, every 15 min | **Scan** of the day's biggest movers plus your watchlist. A stock becomes an **idea** only if it passes every rule (trend, 200-day SMA, volume pro-rated to the time of day, beating NIFTY, not stretched, no bad news, tape not risk-off, 2:1 R:R after charges). The email gives trigger, stop, target, quantity for your risk %, and why. |
| Every 5 min | **Updates** on open ideas: triggered, stopped, target hit, invalidated, exit reminder for intraday. |
| Any time | **Market alert** if NIFTY moves 1% or more, or India VIX spikes. **News email** when a headline matters (RBI, Fed, tariffs, a watchlist stock) and, for stocks with an open idea, when bad news lands. |
| About 15:40 | **Close wrap**: how each idea ended. |
| Any time | **Ask it**: email yourself with subject `[NSE Ask] price RELIANCE` (also `index`, `movers`, `ideas`, `news TCS`, `help`) and it replies. |

"Whenever it feels like" is bounded on purpose: at most 15 emails a day (`MAX_EMAILS_PER_DAY`), at most 3
ideas per scan, every alert deduplicated. Silence means nothing met the rules, which is most of the time.

## How it decides (and where AI is used)

The decisions are **deterministic code**, not an LLM, so they are repeatable, testable and free. The rules
mirror the skill's Mode 2 and use the repo's own
[`trading-costs`](../skills/trading-costs/SKILL.md) calculator for charges. An optional free Gemini key only
**summarises news headlines**; it never produces prices, levels or trade calls.

Data comes from NSE's two official MCP servers (no login): CM Market Live for quotes and Bhavcopy for history.
The agent checks NSE's own timestamp on the quote, so on a holiday or outside the session it stays silent
instead of emailing stale prices.

## Setup (10 minutes)

1. **Gmail app password** (free). Turn on 2-Step Verification at https://myaccount.google.com/security, then
   create an app password at https://myaccount.google.com/apppasswords. Also make sure IMAP is enabled
   (Gmail Settings, Forwarding and POP/IMAP) if you want the `[NSE Ask]` replies.
2. **Configure.** Copy `agent/.env.example` to `agent/.env` and fill in `SMTP_USER`, `SMTP_APP_PASSWORD`,
   `CAPITAL` and optionally `WATCHLIST`, `HOLDINGS` and `GEMINI_API_KEY` (free at https://aistudio.google.com/apikey).
3. **Test.** From the `agent/` folder:

   ```
   python -m nse_agent check            # reaches both NSE servers? is email configured?
   python -m nse_agent test-email       # sends you a test mail
   python -m nse_agent once --dry-run   # one tick, prints what it would send
   ```

## Run it for free: pick one

**A. Your Mac (best timing).** Needs Python 3.10+ (`brew install python`). Fill in `agent/.env`, then run
`bash agent/mac/install.sh`. It installs a launchd job that starts at login, restarts if it crashes, uses
`caffeinate` to stay awake while running, and logs to `agent/agent.log`. The full walkthrough, with a
minute-by-minute day and every alert type, is in [nse-agent-mac.md](nse-agent-mac.md). It only works while
the Mac is on and awake with the lid open (or on power with a display attached); if the Mac sleeps, it
simply catches up on the next tick without re-sending.

**B. GitHub Actions (no computer needed).** Push this repo (private is fine), then in Settings, Secrets and
variables, Actions, add `SMTP_USER`, `SMTP_APP_PASSWORD`, `EMAIL_TO` and optionally `GEMINI_API_KEY`,
`CAPITAL`, `RISK_PCT`, `WATCHLIST`, `HOLDINGS`. The workflow
[`nse-agent.yml`](../.github/workflows/nse-agent.yml) runs one tick every 15 minutes in market hours, about
550 of your 2,000 free minutes a month. Its state is kept in the Actions cache. GitHub's schedule is
best-effort and may run a few minutes late, so updates are less snappy than on the Mac. Disable the workflow
in the Actions tab to stop it.

**C. Always-free cloud VM.** Oracle Cloud's Always Free tier or any small Linux box: clone the repo, create
`agent/.env`, and run `python3 -m nse_agent run` under `systemd` or `tmux`. Set `TZ=Asia/Kolkata`.

Use only one of A, B or C at a time, otherwise you will get duplicate emails.

## F&O desk: live futures and options

The agent also reads **NIFTY and BANKNIFTY** (plus up to 3 F&O stocks from your watchlist and holdings whose
open interest moves most) and gives a verdict with the reasoning attached. From 09:50 to 14:45, every 20 min
(`FNO_INTERVAL_MIN`), it fetches the option chain, the futures and the OI changes, then:

- scores **8 evidence nodes**: price trend, futures OI buildup (long or short buildup, covering, unwinding),
  put-call ratio and its drift, position between the biggest put and call OI walls, fresh OI near the money,
  max pain into expiry, futures premium, and the market tone, plus relative strength and negative news for
  stocks. A call needs a weighted score of at least 30 of 100, at least 3 agreeing signals and no strong
  opposing one. Otherwise the answer is **neutral** or **conflicted** and you get no trade.
- builds a **defined-risk plan**. A bullish or bearish call gets a long option when options are cheap
  against realised movement, a debit spread when fair, a credit spread when rich. A neutral call can get a
  range structure (iron condor or long butterfly) and a conflicted one a breakout structure (long straddle
  or strangle), each only when the gates in the strategy brain below pass. It picks strikes and an
  expiry with at least 2 days left, sets the stop and target from ATR and the OI walls, sizes lots so the loss
  at the stop stays within `RISK_PCT` of `CAPITAL`, and rejects anything under 1.5 reward-to-risk after charges.
  Futures are shown only as an alternative with a "fits your risk budget" flag. **It never suggests selling
  naked options.**
- emails the **F&O pulse** at the first scan of the day, then an **F&O idea** only when a plan passes, with
  trigger, stop, target, strikes, lots, outlay, max loss, break-even, Greeks, key OI levels, an ASCII
  **knowledge graph** (evidence, the verdict it feeds, the levels that set stop and target) and the reasons it
  did not pick the other structures. Open ideas are tracked through the underlying like cash ideas.

Ask it any time: email yourself `[NSE Ask] fno NIFTY` (or `fno BANKNIFTY`, `fno RELIANCE`), or run
`python -m nse_agent fno NIFTY` in `agent/`. Claude can do the same through the `nse-fno` MCP server, which
`.mcp.json` registers (tools `fno_view`, `fno_option_chain`, `fno_futures`, `fno_oi_spurts`; `fno_view` also
returns the graph as JSON; `fno_strategy_guide` explains any strategy). Start it by hand with
`python -m nse_agent.fno_mcp` from `agent/`.

### The strategy brain

On top of the option-chain evidence the desk reads three more things and weighs them in the same scoring:

- **Chart patterns** from about 2 years of daily index or stock bars (paged from the free NSE history feed,
  cached on disk for the day): higher highs and lows, 20-day breakouts and breakdowns, the 20/50/200-day
  average stack, engulfing candles, RSI extremes, and a Bollinger-band **squeeze** (volatility compressed to
  its lowest fifth, which warns of a bigger move). Chart bias counts at weight 1.25 to 1.5.
- **Historical analogs**: it finds the past days whose trend, RSI, volatility and position in the range look
  most like today (up to 24, spaced apart) and reports how often the market rose over the same number of
  days to expiry, with the 10th and 90th percentile moves. Weight 0.75, and only when the edge is clear.
  The same sample feeds the probability of profit and expected value of each structure.
- **News and events**: fresh headlines (under 36 hours) give a small tone bias, and keyword rules flag
  scheduled events (RBI policy, US Fed, Budget, elections, tariffs, results). A major event today or tomorrow
  stops it selling premium and drops credit structures.

It knows the **25 strategies from NSE's Nifty and Bank Nifty option strategies booklet** (long and short call
and put, bull and bear spreads, straddles, strangles, butterflies, condors, collar, covered call, risk
reversal, futures combinations, box). The explanations are written in this project's own words, not copied
from the booklet. For each, the desk computes the payoff at expiry, break-evens, max profit and loss,
probability of profit and expected value after charges, and draws an ASCII payoff chart in the email. Only
defined-risk structures are ever suggested; short call, short put, short straddle and short strangle are
listed as "not suggested" (unlimited risk) and box spreads and futures combinations as reference only. List
or read any of them with `python -m nse_agent strategies` and `python -m nse_agent strategies iron_condor`.

Rules for the two non-directional structures: **range** (neutral view) sells an iron condor, or buys a
butterfly near expiry, only when implied volatility is at least 1.1 times realised, no squeeze is building
and no big event is near. **Breakout** (conflicted view) buys a straddle or strangle only when the chart is
squeezing or an event is near and options are not expensive (implied at most 1.0 times realised, 1.15 in a
squeeze). Both need positive expected value after charges, and the breakout needs 1.5 reward to risk.
Set `FNO_NONDIRECTIONAL=0` to allow directional ideas only.

Every F&O idea that closes (target, stop, invalidated, expired) is written to `fno_journal.jsonl` in the state
folder. With 8 or more closed ideas and a hit rate under 35%, the desk adds a caution and lowers its own
confidence; the email shows the track record. This is a log of the agent's own calls, not a backtest.

Be clear about the limits: about two years of one index is a small sample, analogs describe the past and are
not a forecast, probability and expected value are computed to expiry while the plans usually exit earlier,
and margin is not computed.

**Where the data comes from, honestly.** NSE's official MCP servers cover only the cash market. There is no
free official F&O MCP: Zerodha's needs a paid Kite Connect app and a daily login, Dhan's live data needs its
paid Data API, and Upstox's needs a daily login. So there are two sources, chosen by `FNO_SOURCE`:

1. **Angel One SmartAPI (official, recommended if you have an Angel One account).** Free. The agent logs in
   by itself with your client code, MPIN and a TOTP it generates, so there is no daily manual login. It reads
   live option-chain quotes with bid/ask, open interest, IV from Angel's Greeks and change in OI, and futures
   OI buildup. It only calls market-data endpoints and never places orders.
2. **NSE website (unofficial fallback, no account).** The public JSON behind NSE's option-chain page. It is
   delayed by a minute or so, NSE can change or block it, and it often blocks cloud IPs.

With `FNO_SOURCE=auto` (default) the agent uses Angel One when all four `ANGEL_*` values are set and falls back
to the NSE website if an Angel call fails, logging a warning. Every report and email states which source
produced the numbers.

### Set up Angel One (one time, about 10 minutes)

1. Log in at <https://smartapi.angelone.in>, open **My Apps** and create an app. Copy the **API key**.
2. Enable TOTP at <https://smartapi.angelone.in/enable-totp>. Copy the **secret key** printed beside the QR
   code (also add the QR to your authenticator app if you want codes on your phone).
3. Put these in `agent/.env` (git-ignored; never commit or paste them in chat):
   `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE` (your Angel One login ID), `ANGEL_PIN` (your 4-digit MPIN) and
   `ANGEL_TOTP_SECRET`.
4. During market hours run `python -m nse_agent fno NIFTY` in `agent/`. The header should read
   **Angel One SmartAPI chain stamp**. If it says "NSE website", check the log for the Angel error.
   After a failed login the agent waits 5 minutes before trying again so it cannot lock your account.

This Angel One path was built from the official SDK and documentation and tested offline only. It has not been
run against Angel One's live servers by the author, so treat step 4 as the real test. It also covers about 61
strikes around the money, so PCR and OI totals differ slightly from NSE's full chain, and change in OI is fetched
for the nearest 13 strikes only. Because it is an official API, it should also work from GitHub Actions: add the
four values as repository secrets and F&O switches on there.

**What it cannot do.** It does not compute margin (check SPAN in your broker), see live bid-ask beyond the
chain snapshot, or know about events after the data stamp. A stale chain (market closed, holiday) is never
emailed. SEBI studies found roughly 9 in 10 individual F&O traders lose money; treat the output as a checklist
that forces discipline, not a prediction. Start with paper trades.

## Settings

All via `agent/.env` or environment variables. See [.env.example](../agent/.env.example) for the full list.

| Setting | Default | Meaning |
|---|---|---|
| `CAPITAL`, `RISK_PCT` | 500000, 1.0 | Position size = risk % of capital divided by stop distance, capped at 25% of capital |
| `WATCHLIST` | 25 large caps | Always checked, in addition to NSE's top movers |
| `HOLDINGS` | none | Stocks you own; you get risk alerts on sharp drops and 50-day SMA breaks |
| `MAX_EMAILS_PER_DAY` | 15 | Hard cap |
| `SCAN_INTERVAL_MIN` | 15 | How often to scan |
| `FNO_ENABLED`, `FNO_INTERVAL_MIN` | 1, 20 | Turn the F&O desk off, or change how often it scans |
| `FNO_UNDERLYINGS`, `MAX_FNO_IDEAS_PER_DAY` | NIFTY,BANKNIFTY, 4 | What it watches and the daily cap on F&O ideas |
| `FNO_SOURCE` | auto | `auto` (Angel One if configured, else NSE website), `angel` (Angel One only) or `nse` |
| `FNO_NONDIRECTIONAL` | 1 | Allow range (iron condor, butterfly) and breakout (straddle, strangle) ideas; 0 keeps directional only |
| `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_PIN`, `ANGEL_TOTP_SECRET` | none | Angel One SmartAPI login for official F&O data |
| `GEMINI_API_KEY` | none | Optional news summaries |

Note that with the default 1% risk on 5 lakh (₹5,000), one NIFTY lot (65) usually cannot fit a stop-loss, so
most days the F&O answer for a small account is "no trade, here is why". That is the risk rule working, not a
bug. Raise `CAPITAL` and `RISK_PCT` only if you accept the larger loss.

## Limits you should know

- Cash-market data is **delayed 1 to 5 minutes** (NSE's MCP) and F&O data can lag by a minute or more
  (NSE's website, or Angel One's feed). Entries are triggers to verify in your broker, not instant fills.
- NSE's MCP terms are informational and non-commercial. This is for your personal use.
- Free RSS news is headline-level and can be wrong or late. The agent uses it to veto ideas and flag events,
  not to originate trades.
- Rules are a disciplined filter, not a prediction. Many days produce no ideas. Backtest before trusting
  it with real money: see the [`backtest-lab`](../skills/backtest-lab/SKILL.md) skill.
- Educational analysis, not investment advice. You place every order yourself.

## Files

| Path | Purpose |
|---|---|
| `agent/nse_agent/engine.py` | The loop: what to do on each tick |
| `agent/nse_agent/analysis.py` | Setup rules, trade plan, tape tone |
| `agent/nse_agent/nse.py`, `mcp.py` | NSE MCP client and typed wrappers |
| `agent/nse_agent/news.py`, `llm.py` | RSS news scoring and veto; optional Gemini summaries |
| `agent/nse_agent/fno_data.py` | NSE website client: option chain, futures, OI spurts, lot sizes |
| `agent/nse_agent/angel_data.py`, `fno_source.py` | Angel One SmartAPI client (TOTP login, chain, futures) and the source choice with NSE fallback |
| `agent/nse_agent/fno_analysis.py` | OI metrics, evidence nodes, verdict, trade structuring, graph |
| `agent/nse_agent/fno_pricing.py` | Black-Scholes price and Greeks |
| `agent/nse_agent/fno_patterns.py`, `fno_news.py` | Chart patterns and historical analogs; headline tone and event risk |
| `agent/nse_agent/fno_strategies.py` | Strategy catalogue, payoff maths and charts, range and breakout planner |
| `agent/mac/install.sh` | One-command Mac installer (launchd) |
| `agent/nse_agent/fno_service.py`, `fno_render.py` | Glue to the cash data, and the F&O email/report text |
| `agent/nse_agent/fno_mcp.py` | `nse-fno` stdio MCP server for Claude |
| `agent/nse_agent/notify.py`, `inbox.py` | Gmail send and `[NSE Ask]` replies |
| `tests/test_nse_agent.py`, `tests/test_fno.py`, `tests/test_angel.py`, `tests/test_brain.py` | Offline tests with fake NSE and Angel One feeds and a fake mailer |
