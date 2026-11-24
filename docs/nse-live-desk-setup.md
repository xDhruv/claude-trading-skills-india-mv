# NSE Live Desk: setup (Mac + Claude)

The `nse-live-desk` skill lets Claude read NSE live data, find trades and email you through the day.
It needs three things: the two NSE MCP servers, a Gmail connector, and a schedule.

## 1. Install the skills

Claude Code:

```
/plugin marketplace add xDhruv/claude-trading-skills-india-mv
/plugin install india-trading-skills@india-trading-skills
```

The plugin's `.mcp.json` registers both NSE servers automatically. Run `/mcp` to confirm
`nse-cm-live` and `nse-bhavcopy` show as connected.

Claude Desktop / claude.ai: upload the `skills/nse-live-desk` folder (and the other skills you want) as
skills, then add the connectors below by hand.

## 2. Add the connectors (no login needed for NSE)

| Connector | URL | Auth |
|---|---|---|
| NSE CM Live | `https://mcp.nseindia.in/cmmkt/mcp` | none |
| NSE Bhavcopy | `https://mcp.nseindia.in/bhavcopy/cm/mcp` | none |
| Gmail | built-in Google connector | your Google sign-in |

In Claude Desktop or claude.ai: Settings, Connectors, Add custom connector, paste each URL. In Claude
Code:

```bash
claude mcp add --transport http nse-cm-live https://mcp.nseindia.in/cmmkt/mcp
claude mcp add --transport http nse-bhavcopy https://mcp.nseindia.in/bhavcopy/cm/mcp
```

Keep tool-approval prompts on for Gmail send until you trust the digests. Scheduled runs that cannot
prompt need the Gmail send tool pre-approved, which is the main thing to review before you enable them.

## 3. Test by hand first

1. "What is RELIANCE trading at? Show data age." Expect a delayed quote with a timestamp.
2. "How are NIFTY 50, NIFTY BANK and INDIA VIX?"
3. "Scan the market for one trade idea with stop, target and quantity for ₹5 lakh capital, 1% risk."
4. "Email that scan to me." Check that it arrives at `dhruvxverma@gmail.com` with the `[NSE Desk]`
   subject.

## 4. Schedule it

Create one scheduled task per row in `skills/nse-live-desk/references/email-digest.md` (Claude Desktop
scheduled tasks, or Claude Code routines). The Mac's timezone must be IST, the schedule should be
Monday to Friday only, and for local scheduled tasks the Mac must be awake with Claude open.

Prompt template (replace the run name):

```
Use the nse-live-desk skill. Run: <Open scan>. It is a market day only if cm_get_data_status shows
fresh data or the pre-open brief applies; on a holiday do nothing. Follow Mode 3: email only my own
address dhruvxverma@gmail.com, dedupe against today's [NSE Desk] emails, reply in thread for idea
updates. Capital ₹5,00,000, risk 1% per trade, max 3 ideas, no order placement.
```

Inbox questions: email yourself a message with the subject starting `[NSE Ask]` (for example
`[NSE Ask] is TATASTEEL near a breakout?`). A scheduled task every 30 minutes during market hours,
with the prompt "Use the nse-live-desk skill, Mode 3 rule 6: answer unread [NSE Ask] mail from me", will
reply in the same thread. Claude cannot react to email instantly; it only sees mail when a run happens.

## Change the recipient, capital or risk

- Recipient: edit the address in `skills/nse-live-desk/SKILL.md` (Mode 3, rule 1) and in the prompt.
- Capital and risk per trade: put them in your scheduled prompt.

## Limits to know

- Live data is delayed 1 to 5 minutes, cash market only. No option chain, OI or tick data. For F&O use a
  broker MCP (see `broker-integration`).
- NSE restricts this data to informational, non-commercial use.
- Ideas are educational analysis, not advice. The skill never places orders.
