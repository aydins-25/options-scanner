# Options scanner

Real-brokerage (Robinhood-style) options data sheet for a watchlist. Not advice. `backtest.py` (Actions, mode = backtest) tests the 1-2 day hold rules on synthetic option prices: so far no setup makes money on average.

- Run: Actions tab → "Options scan" → Run workflow. Inputs: tickers, budget per trade ($, optional), hold days.
- Output: trend, realized vs implied volatility, earnings date, most liquid calls/puts 2-6 weeks out, and (with a
  budget) top near-the-money picks for a short hold. Prices are stale when the market is closed: re-check bid/ask.
