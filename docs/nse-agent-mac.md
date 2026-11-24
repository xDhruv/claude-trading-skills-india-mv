# Run the NSE desk on your MacBook, automatically

This is the practical guide: install once, then it runs every market day and emails you. What the agent
decides and why is in [nse-agent.md](nse-agent.md). Cost: zero. Gmail, NSE's public data, free RSS news and
(optionally) your own Angel One account.

## 1. One-time setup (about 15 minutes)

1. **Python 3.10 or newer.** Check with `python3 --version`. If older or missing, download the macOS installer
   from <https://www.python.org/downloads/macos/> (works on Intel and Apple Silicon, no Homebrew needed;
   pick 3.12), open it and click through. Then close and reopen Terminal. `brew install python` also works.
   The installer script fixes Python's HTTPS certificates for the python.org build automatically.
   Nothing else to install; the agent uses only Python's standard library.
2. **Get the code** (your private fork): `git clone https://github.com/xDhruv/claude-trading-skills-india-mv.git`
   (push the latest commits from your Windows machine first, or you will clone an older version).
3. **Gmail app password.** Turn on 2-Step Verification, then create an app password at
   <https://myaccount.google.com/apppasswords>. This is not your Gmail password and can be revoked any time.
4. **Run the installer** from the repo folder: `bash agent/mac/install.sh`. The first run creates
   `agent/.env` (permissions 600) and stops. Open it and set at least:
   - `SMTP_USER`, `SMTP_APP_PASSWORD`, `EMAIL_TO`
   - `CAPITAL` and `RISK_PCT` (they size every idea; 1% of 5 lakh is only ₹5,000 of risk, so expect many
     "no trade" answers on a small account)
   - the four `ANGEL_*` values if you want Angel One's official F&O data (setup steps are in
     [nse-agent.md](nse-agent.md#set-up-angel-one-one-time-about-10-minutes))
   - optionally `WATCHLIST`, `HOLDINGS`, `GEMINI_API_KEY`
5. **Run the installer again.** It checks Python, runs `python -m nse_agent check`, sends a test email, writes
   `~/Library/LaunchAgents/com.xdhruv.nse-agent.plist` with your real paths and starts the job. If the test
   email does not arrive, fix `.env` and re-run; the script is safe to repeat.
6. **Confirm it is alive:** `tail -f agent/agent.log` should show "agent started". Try the F&O desk by hand
   (market open or not): `cd agent && python3 -m nse_agent fno NIFTY`.

To remove it: `bash agent/mac/install.sh --uninstall`.

## 2. Keeping the Mac awake (the part that bites)

The agent only works while the Mac is awake. launchd restarts it after a crash or a reboot, and
`caffeinate -i` prevents *idle* sleep while it runs, but nothing can run while the Mac is asleep.

- Keep the lid **open** and the charger **plugged in** from about 08:45 to 15:45 IST. Closing the lid sleeps
  a MacBook unless an external display and power are attached.
- To wake a sleeping Mac for the pre-open brief: `sudo pmset repeat wakeorpoweron MTWRF 08:40:00`. It wakes a
  sleeping Mac; it does not power on one you shut down. Cancel it with `sudo pmset repeat cancel`.
- If the Mac does sleep mid-day, the next tick catches up without re-sending anything already sent. You
  lose timeliness for that stretch, not correctness.
- Set your Mac's time zone sensibly. The job pins `TZ=Asia/Kolkata`, so the schedule is right even if you
  travel, but check that the clock itself is correct.
- If you cannot keep a laptop open, use the GitHub Actions or free cloud VM options in
  [nse-agent.md](nse-agent.md#run-it-for-free-pick-one). Never run two at once or you get duplicate emails.

## 3. What happens during a day (all times IST, Monday to Friday)

The loop wakes every minute, and each job runs on its own timer. Weekends do nothing; holidays are detected
because NSE's data is stale, and stale data is never emailed.

| Time | What it does |
|---|---|
| 08:30 to 16:30 | Checks your Gmail every 10 min for `[NSE Ask]` commands (see section 5) |
| 08:45 to 10:00 | **Pre-open brief**, once: tone, global cues, what to watch |
| every 20 min until 21:00 | Reads free RSS news; emails only if strong or if it hits an open idea |
| 09:15 onward | Tracks every open idea every 5 min; watches NIFTY for a 1% move and INDIA VIX for a spike |
| 09:45 to 15:30 | Cash-market scan every 15 min: movers, volume, setups, risk alerts on your holdings |
| 09:50 to 14:45 | **F&O scan** every 20 min: option chain, futures, OI, charts, analogs, news, event risk |
| after 14:30 | New ideas stop being emailed (not enough time left); open ones keep being tracked |
| 15:15 | Intraday ideas get an "exit before the close" reminder, once |
| 15:40 to 15:45 | **Close wrap**: how every idea ended, if the market was live today |

### What the F&O scan does on each pass

1. Pulls the NIFTY and BANKNIFTY chain (plus up to 3 stocks from your watchlist and holdings whose F&O open
   interest is moving). Angel One first if configured, otherwise NSE's website.
2. Loads about two years of daily bars (cached for the day), reads chart patterns, finds the closest
   historical analogs, reads fresh headlines and flags scheduled events.
3. Scores all the evidence into a verdict: **bullish, bearish, neutral or conflicted**. A call needs a weighted
   score of at least 30, at least 3 agreeing signals and no strong opposing signal.
4. Tests the strategy catalogue against your rules: expected value after charges, probability of profit,
   reward to risk, liquidity, event risk, and whether one lot fits your risk budget. Defined-risk structures
   only; it never suggests selling naked options.
5. If something passes, you get an email. If not, you get nothing, and the reasons are logged, and shown on
   demand with `[NSE Ask] fno NIFTY`.

## 4. The emails you will get

Every subject starts with `[NSE Desk]`, so one Gmail filter catches everything. The hard cap is 15 emails a
day (`MAX_EMAILS_PER_DAY`); when it is hit, the rest are logged, not sent. Each alert has a dedupe key, so
the same condition is not repeated.

| Subject | When | What is inside |
|---|---|---|
| `Pre-open: <date>` | once, 08:45 to 10:00 | Overall tone and what to watch today |
| `Idea: SYMBOL...` / `Watch: ...` | cash scan finds a setup | Entry trigger, stop, target, size for your capital, why, news check |
| `F&O pulse (<time>)` | first F&O scan of the day | Verdict per underlying, key levels, why there is or is no trade |
| `F&O idea: NIFTY bullish...` | a plan passes every gate | See below |
| `Update: ...` | a cash idea triggers, hits target, stops or is invalidated | What happened, what to do |
| `F&O update: ...` | the same for F&O ideas | Same, using the underlying level |
| `Market alert: <time>` | NIFTY moves 1% or more, or VIX jumps 8% or reaches 20 | One-line facts, nothing to trade |
| `Market news: ...` / `News on an active idea: ...` | strong headlines, or bad news on an open idea | Headlines plus an optional plain-English summary |
| `Close: <date>` | 15:40 | Day's ideas and outcomes |
| `Re: [NSE Ask] ...` | you asked it something | The answer |

### Anatomy of an F&O idea email

- **Verdict and confidence**, the data source and its timestamp (so you know how fresh it is).
- **Structure**: for example a bull call spread, a long straddle or an iron condor, with exact strikes, expiry,
  lots, outlay or credit, max loss, break-evens, and the Greeks.
- **Trigger, stop and target** on the underlying (or the two watch levels for range and breakout trades), with
  why each level was chosen (ATR, OI walls, the short strikes).
- **PAYOFF AT EXPIRY**: an ASCII chart of profit and loss against the index level, marking spot.
- **Odds**: probability of profit and expected value after charges, from both the market-implied volatility
  and the historical analog sample.
- **EVIDENCE (score x weight)**: each signal with its sign. Then **CHART PATTERNS**, **HISTORICAL ANALOGS**,
  and **NEWS AND EVENTS**.
- **STRATEGIES CONSIDERED**: what else was tried and why it was rejected.
- **KNOWLEDGE GRAPH**: evidence, the verdict they feed and the levels that set the stop and target.
- **Track record**: how this desk's own earlier F&O ideas ended (once there are enough of them).

Alerts on your phone: in Gmail, create a filter for subject `[NSE Desk]`, apply a label such as `NSE`, and
turn on notifications for that label only. Use a filter on `Idea` or `F&O idea` if you want only the
actionable ones to buzz.

## 5. Asking it things

Email yourself (to the same `EMAIL_TO`) with the command in the subject. It replies within about 10 minutes
during 08:30 to 16:30. Read-only commands only:

```
[NSE Ask] price RELIANCE
[NSE Ask] index NIFTY
[NSE Ask] movers
[NSE Ask] ideas
[NSE Ask] news TCS
[NSE Ask] fno NIFTY        (or BANKNIFTY, or an F&O stock)
```

On the Mac you can also run `cd agent && python3 -m nse_agent fno NIFTY` or `python3 -m nse_agent strategies
iron_condor` to read how a strategy works. Claude can call the same data through the `nse-fno` MCP server.

## 6. Operating it

- **Log:** `tail -f agent/agent.log`. Warnings such as "Angel One failed, using NSE website" or "daily email cap
  reached" show up here.
- **Status:** `launchctl print gui/$(id -u)/com.xdhruv.nse-agent | head -20`
- **Restart after editing `.env`:** `launchctl kickstart -k gui/$(id -u)/com.xdhruv.nse-agent`
- **Update the code:** `git pull`, then the restart command above.
- **Dry run (prints, sends nothing, separate state):** `cd agent && python3 -m nse_agent once --dry-run`
- **State:** `agent/.nse_agent_state/` holds today's ideas, the daily history cache and `fno_journal.jsonl`,
  the permanent record of closed F&O ideas. It is git-ignored.
- **Secrets:** `agent/.env` is git-ignored and mode 600. Never paste it into a chat or commit it. The agent
  only reads market data from Angel One and never calls an order endpoint.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| No emails at all | `python3 -m nse_agent test-email`; check the app password and `EMAIL_TO` |
| "no live NSE data" in the log | Market closed, a holiday, or NSE is slow. It retries next tick |
| F&O header says "NSE website", not Angel One | Wrong `ANGEL_*` value or TOTP secret; the log has the reason. It waits 5 min between login attempts |
| Nothing from the F&O desk all day | Normal. Gates rejected everything, or one lot does not fit `RISK_PCT` of `CAPITAL` |
| Silent for an hour mid-day | The Mac slept. Keep it awake and plugged in |
| Job not running | `bash agent/mac/install.sh` again; read `agent/agent.log` |

## 8. Honest limits

- It cannot place orders and is not meant to. You act on every idea in your own broker.
- Data is delayed by a minute or more; entries are triggers to verify, not fills. Check the lot size, margin
  (SPAN) and bid/ask in your broker; the agent does not compute margin.
- Chart patterns and historical analogs come from about two years of one index. They are a small, noisy
  sample, not a forecast. Probability and expected value are computed to expiry, while plans exit earlier.
- Many days produce no idea. That is the filter working.
- SEBI's studies found roughly 9 in 10 individual F&O traders lose money. Paper-trade it first and read the
  journal before risking real money. This is educational analysis, not investment advice.
