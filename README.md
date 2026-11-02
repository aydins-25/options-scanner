# Options scanner

Real-brokerage (Robinhood-style) options data sheet for a watchlist. Not backtested, not advice.

- Run: Actions tab → "Options scan" → Run workflow. Inputs: tickers, budget per trade ($, optional), hold days.
- Output: trend, realized vs implied volatility, earnings date, most liquid calls/puts 2-6 weeks out, and (with a
  budget) top near-the-money picks for a short hold. Prices are stale when the market is closed: re-check bid/ask.
