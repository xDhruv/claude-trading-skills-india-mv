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

**A. Your Mac (best timing).** Needs Python 3.10+ (`brew install python`). Edit the paths in
`agent/launchd/com.xdhruv.nse-agent.plist`, copy it to `~/Library/LaunchAgents/` and run
`launchctl load ~/Library/LaunchAgents/com.xdhruv.nse-agent.plist`. It starts at login, restarts if it
crashes, uses `caffeinate` to stay awake while running, and logs to `agent/agent.log`. It sleeps outside
market hours. It only works while the Mac is on and awake with the lid open (or on power with a display
attached); if the Mac sleeps, it simply catches up on the next tick without re-sending.

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

## Settings

All via `agent/.env` or environment variables. See [.env.example](../agent/.env.example) for the full list.

| Setting | Default | Meaning |
|---|---|---|
| `CAPITAL`, `RISK_PCT` | 500000, 1.0 | Position size = risk % of capital divided by stop distance, capped at 25% of capital |
| `WATCHLIST` | 25 large caps | Always checked, in addition to NSE's top movers |
| `HOLDINGS` | none | Stocks you own; you get risk alerts on sharp drops and 50-day SMA breaks |
| `MAX_EMAILS_PER_DAY` | 15 | Hard cap |
| `SCAN_INTERVAL_MIN` | 15 | How often to scan |
| `GEMINI_API_KEY` | none | Optional news summaries |

## Limits you should know

- NSE data is **delayed 1 to 5 minutes** and covers the **cash market only** (no futures, options or ticks).
  Entries are triggers to verify in your broker, not instant fills.
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
| `agent/nse_agent/notify.py`, `inbox.py` | Gmail send and `[NSE Ask]` replies |
| `tests/test_nse_agent.py` | Offline tests with a fake NSE feed and fake mailer |
