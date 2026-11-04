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
- `backtest.py` (Actions, mode = backtest) tests the short-hold rules with SYNTHETIC option prices (Black-Scholes from
  real stock prices, IV = 1.1 x 20d realized vol, 6% round-trip spread, Robinhood hours/fees, gaps fill at the open).
  First run 2026-11-02, 25 tickers 2015-2026: EVERY setup loses on average (-4% to -18% per trade). +50% take profit
  in 2 days hits only 1-6% of the time; -40% stop hits 30-65%. Day after a -10% crash + market gap up (INTC on
  2026-11-02): calls -18%/trade (n=108, stock fell 2.6% avg in 2 days, real data); puts +4% but mixed by year, small n.
  Never present any setup as a proven edge.
- `strategies.py` (mode = strategies): SPY/QQQ 2004-2026 with REAL implied vol (VIX/VXN), skew, T-bill rate.
  Put credit spreads: -3% to -4%/trade at base settings, sign flips with the skew assumption -> not usable.
  2-day hold: calls ~0%, puts -10%. LONG CALL 60 DTE delta 0.60 above 200-day, max 21 trading days, +/-50%:
  +4.9% (SPY) / +6.9% (QQQ) per trade, positive in every variant and both halves. Still LESS than plain buy &
  hold (SPY +9.2%/yr, QQQ +14.3%/yr price only): it's leveraged index exposure, not an edge. Note: Yahoo VIX
  history ended 2026-05-22 in that run.
- Research read 2026-11-02: buying options loses on average (Coval-Shumway 2001); retail loses 5-14% around
  earnings (de Silva/So/Smith); index VRP is real (Bondarenko/Cboe PUT) but OTM spreads give much of it back via
  skew; pre-earnings straddles +3.3% gross (Gao/Xing/Zhang 2018) but spreads eat it; 200-day filter cuts drawdowns.
- `paper.py` + workflow "Paper trade" (once per trading day, 10:00-15:55 ET; GitHub cron runs late, so it fires every 30 min): paper-trades LONG60 (SPY/QQQ) and the old TWODAY pick
  with REAL quotes (buy at ask, sell at bid), state in paper/*.csv, summary in paper/report.md. Check it before
  recommending real money; a few weeks of trades prove little either way.

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

## Recommendation given 2026-11-02 (after full control was granted)
- Core money: index ETF buy & hold beat every option strategy tested. Options only with money he can lose;
  LONG60 is the only tested option rule worth paper-trading. Stop the 2-day lotto trades.

## Repo conventions
- Commit to main, end commit messages with the Claude attribution lines.
