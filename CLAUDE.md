# Options scanner: context for Claude

Own repo (aydins-25/options-scanner), separate from the SMG short scanner (aydins-25/smg-scanner, which has the
stock data setup and backtests). Only for REAL-brokerage options (Robinhood rules,
not SMG: SMG has no options). Owner: Aydin. Started 2026-11-02.

## How it runs
- `scanner.py` (needs yfinance; this cloud session's network is blocked, so run it in GitHub Actions).
- Actions → "Options scan" → Run workflow. Inputs: tickers, budget ($ per trade, blank = no ranking), hold_days.
- Output: per ticker trend (5/20/60d, vs 50/200-day, RSI), 20-day realized vol, next earnings, the 3 most liquid
  calls and puts in the first two expiries 14-45 days out (mid, cost/contract, IV, OI, breakeven), ATM IV vs
  realized. With a budget: TOP PICKS = affordable contracts within 15% of the price, scored by
  (one normal day's move x |delta| - time decay over the hold) / cost. Skips earnings-inside-expiry, >15% spreads, and anything outside delta 0.25-0.60
  (without that filter it ranks far out-of-the-money lottery tickets first).

## Data caveats (learned the hard way)
- Outside market hours Yahoo returns bid/ask 0, OI 0 and garbage IV. The scanner falls back to the last trade price
  and computes IV itself (Black-Scholes, r = 4%). Always re-check bid/ask in Robinhood and use limit orders at mid.
- Earnings dates already in the past must not be flagged as "before expiry" (fixed).
- Nothing here is backtested. Say so every time; never present the ranking as a proven edge.

## Aydin's usage so far
- Watchlist 2026-11-02: NBIS NVDA ORCL INTC HOOD BE MU SNDK GOOG. All down 8-45% over 20 days (tech selloff).
- Style: buys 1 contract, holds 1-2 days (sell by Mon/Tue close). Example budget $220 → only puts fit.
  Pick given: INTC Nov 20 $60 put (~$170), alt GOOG Nov 20 $255 put (~$191). Exit: +50% take profit, -40% cut,
  or end of day 2.
- Avoid: buying options with earnings inside the expiry (IV crush: NVDA Nov 17, NBIS Nov 12 that week),
  $2k-11k contracts (MU, SNDK), far out-of-the-money weeklies.
- Moved here from smg-scanner/options on 2026-11-02.

## Style
- Phone-friendly: short tables, exact contract (ticker, expiry, strike, call/put), cost, breakeven, exit rule.
- Always: worst case = the whole premium. Real money.

## Repo conventions
- Commit to main, end commit messages with the Claude attribution lines.
