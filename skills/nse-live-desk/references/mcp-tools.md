# NSE MCP tool reference

Both servers are remote Streamable HTTP, read-only, no authentication. Verified against the live
endpoints; if a tool is missing in your client, run `tools/list` again, because NSE may change them.

## CM Market Live: `https://mcp.nseindia.in/cmmkt/mcp`

Server name `cm-market-mcp`. Live cash-market data, NSE hours 09:15 to 15:30 IST, Monday to Friday.

| Tool | Arguments | Use it for |
|---|---|---|
| `cm_get_stock_quote` | `symbol` (exact) | One stock: LTP, open, high, low, change, % change, volume, 52-week range, series |
| `cm_get_equity_stocks` | `limit` (max 500), `symbolFilter` (prefix) | Bulk equity snapshot, EQ/BE/BL/BT/IL/IQ series |
| `cm_get_sme_stocks`, `cm_get_bond_stocks`, `cm_get_call_auction_stocks` | `limit`, `symbolFilter` | Other segments. Not for idea scans |
| `cm_get_live_indices` | `group` (all, derivatives, broad, sectoral, strategy, thematic, fixed_income), `nameFilter` | Index levels, sector leadership |
| `cm_get_index_quote` | `indexName` (exact, case-insensitive) | `NIFTY 50`, `NIFTY BANK`, `INDIA VIX`, with 52-week and 1w/1m/1y comparisons |
| `nse_get_market_movers` | `indexName` (empty = all, or NIFTY, BANKNIFTY, NIFTYNEXT50), `limit` (max 50) | **Primary** sorted top gainers and losers |
| `nse_get_gainers`, `nse_get_losers` | `limit` (max 50) | One-direction sorted lists |
| `cm_get_live_gainers`, `cm_get_live_losers`, `cm_get_live_market_data` | none / `index` | Raw lists grouped by index segment, not sorted. Avoid for rankings |
| `cm_get_data_status`, `cm_get_allstocks_status` | none | Freshness check. Call before trusting any live number |

## Bhavcopy: `https://mcp.nseindia.in/bhavcopy/cm/mcp`

Server name nse-bhavcopy-redis-mcp. About 5000 equities, up to 5 years, previous trading day's final
data. Dates are `YYYY-MM-DD`. A holiday or weekend date returns the nearest trading day.

| Tool | Arguments | Use it for |
|---|---|---|
| `nse_lookup_symbol`, `search_symbols` | `query` | Find the exact ticker. `search_symbols` also returns latest close |
| `get_stock_history` | `symbol`, `months` (max 3 per call), `endDate` | Daily OHLCV for ATR, swing levels. Chain with `next_end_date` for longer |
| `get_ltp_by_date`, `get_bulk_quote` | `symbol, date` / `symbols` | Closing price on a date; latest snapshot for several symbols |
| `moving_average` | `symbol`, `days` | SMA 20, 50, 200 |
| `get_volume_analysis` | `symbol`, `days` | Average volume, spike days, 5-day trend |
| `get_52_week_high_low` | `symbol` | 52-week range, position in range. Unadjusted prices |
| `get_corporate_actions` | `symbol`, `fromDate`, `toDate` | Splits, bonuses, dividends with ex-dates. Run before trusting % moves |
| `get_stock_vs_index` | `symbol`, `indexName`, `months`, `date` | Relative return, beta, correlation. Adjusted for splits and bonuses |
| `compare_stocks` | `symbols` (up to 10), `months` | Return and drawdown ranking. Unadjusted prices |
| `get_top_movers`, `get_top_by_volume` | `date`, `n`, `direction` / `sortBy` | Previous-session movers and most active stocks |
| `get_market_breadth`, `get_market_mood` | `date` | A/D ratio; VIX, index and stock advance/decline read |
| `get_index_snapshot`, `get_index_history`, `get_index_performance` | `date`+`filter` / `indexName`+`months`+`endDate` / `indexName`+`date` | Index OHLC, history (up to 12 months per call), multi-period returns |
| `get_index_valuation` | `indexName`, `months`, `date` | P/E, P/B, yield and percentile of NIFTY and other indices |
| `compare_indices`, `get_index_movers` | `indexNames`+`months` / `date`+`period`+`n`+`scope` | Index comparisons; sector rotation by day, week or month |

## Field and naming gotchas

- Index names must match exactly, for example `NIFTY 50`, `NIFTY BANK`, `INDIA VIX`. Use
  `cm_get_live_indices` with `nameFilter` to find the exact string.
- The `index` argument of `cm_get_live_market_data` accepts `gainers` or `loosers` (sic).
- Most Bhavcopy tools list every argument as required. Pass a date explicitly (the last trading day) and
  a sensible default such as `months: 3` or `days: 20` rather than omitting it.
- Pass `limit: 0` to use the default page size on the live tools.
- Both servers rate-limit. Do not loop over the whole universe.
