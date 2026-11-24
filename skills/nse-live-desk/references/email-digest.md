# Email formats and schedule prompts

All times are IST on market days (Monday to Friday, skipping NSE holidays). Recipient is always the
user's own address from `docs/nse-live-desk-setup.md`.

## Daily schedule

| Time | Run | Subject |
|---|---|---|
| 09:00 | Pre-open brief (Bhavcopy only, live data not yet fresh) | `[NSE Desk] Pre-open: <date>` |
| 09:45 | Opening scan (Mode 2), after the first 30 minutes settle | `[NSE Desk] Open scan: <date>` |
| 11:30 | Mid-morning scan and idea status updates | `[NSE Desk] Midday: <date>` |
| 13:30 | Afternoon scan and idea status updates | `[NSE Desk] Afternoon: <date>` |
| 15:00 | Last-hour check: risk alerts, exit reminders for intraday ideas | `[NSE Desk] Last hour: <date>` |
| 15:45 | Close wrap: results of ideas, breadth, tomorrow's watchlist | `[NSE Desk] Close: <date>` |
| every 30 min, 09:15 to 15:30 | Inbox check for `[NSE Ask]` questions | reply in thread |

Intraday ideas must carry an "exit by 15:15" line.

## Scan email

```
Subject: [NSE Desk] <Open scan|Midday|...>: 24 Nov 2026

Tape: <trending up | choppy | risk-off>. NIFTY 24,xxx (+0.4%), BANK NIFTY xx,xxx (+0.2%), VIX 13.2 (-3%).
Data: NSE CM Live, delayed ~2 min, as of 10:12 IST.

IDEA 1: <SYMBOL>  <setup>  <intraday|swing>
  Trigger: above ₹x (break of 20-day high)
  Stop:    ₹x  (1.2 ATR)     Target: ₹x (2.4R)
  Qty: n  Risk: ₹x  Cost + breakeven: ₹x / ₹x
  Why: +2.8% on 1.9x volume, 1.2% below 52-week high, above 50 and 200-day SMA, beats NIFTY by 4.1 pts (3m)
  Invalid if: closes back below ₹x or NIFTY turns negative

Watchlist: <SYMBOL> (washout, needs reclaim of ₹x)
Risk alerts: <SYMBOL> -5.2% on 2.3x volume, below 50-day SMA

Educational analysis from delayed NSE data, not investment advice. Verify in your broker before acting.
```

If no idea qualifies, send only when something material changed (index move over 1%, VIX spike, a held
or watched stock alert). Otherwise send nothing.

## Idea update (reply in the same thread)

```
<SYMBOL>: TRIGGERED at ₹x (10:41). Stop ₹x, target ₹x. Delayed quote as of 10:41.
<SYMBOL>: STOP HIT near ₹x (11:20). Loss about ₹x including costs. Review before re-entering.
<SYMBOL>: TARGET HIT near ₹x (13:02). Consider trailing the rest (trailing-stops).
<SYMBOL>: INVALIDATED. Index reversed / price closed below ₹x.
```

## Pre-open brief

Previous close for NIFTY, BANK NIFTY and VIX (`get_index_snapshot`), previous-session breadth and mood,
sector leaders and laggards (`get_index_movers`, 1D and 1W), top movers and most active names of the
previous session with a corporate-action check, and a "levels to watch" list. Mention GIFT Nifty and
global cues as not covered by these servers unless the user provides them.

## Close wrap

Today's idea results (triggered, hit target, stopped, not triggered), closing index levels, breadth,
sector leaders, two or three swing-watchlist names with trigger levels for tomorrow, and a reminder of
any results or ex-dates tomorrow (`event-calendar`).

## Inbox reply

Reply in the same thread. Start with the direct answer, then the data and its age, then one line of
risk context. If the question needs data these servers do not have, say so and name the source that does.
