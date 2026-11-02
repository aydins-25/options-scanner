"""
Backtest of the short-hold option trades this repo suggests (1 contract, hold 1-2 days, +50% take profit,
-40% stop, else sell at the close of day 2).

Yahoo has no historical option prices, so options are SYNTHETIC: priced with Black-Scholes from real daily stock
prices, with implied vol = IV_MULT x the stock's 20-day realized vol (real IV is often higher, especially after
crashes, and has skew). Stock moves, gaps, and the high/low used for the take-profit and stop are real. Treat the
results as "does the stock move enough, fast enough, to beat decay and spread", not as exact option P&L.

Signal uses data through day d's close. Entry at day d+1's open (you buy in the morning). Day 1 = d+1, day 2 = d+2.
Exit on day 1 or 2 if the stock's high/low would have put the option at +50% / -40% (if both in one day, the stop
counts: conservative), else at day 2's close. Trades with an earnings date between entry and expiry are skipped.

    python backtest.py                      -> output/options/backtest.txt
    TICKERS="INTC GOOG" START=2015-01-01 IV_MULT=1.2 SPREAD=0.06 python backtest.py
"""
import os
from math import erf, exp, log, sqrt
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd
import yfinance as yf

RATE = 0.04
ROOT = Path(__file__).resolve().parent
WATCH = "NBIS NVDA ORCL INTC HOOD BE MU SNDK GOOG"
EXTRA = "AMD TSLA AAPL MSFT META AMZN AVGO PLTR COIN SMCI QCOM NFLX CRM SHOP UBER MRVL"
N = lambda x: 0.5 * (1 + erf(x / sqrt(2)))


def bs(s, k, t, v, kind):
    if t <= 0 or v <= 0:
        return max(s - k, 0) if kind == "call" else max(k - s, 0)
    d1 = (log(s / k) + (RATE + v * v / 2) * t) / (v * sqrt(t))
    d2 = d1 - v * sqrt(t)
    if kind == "call":
        return s * N(d1) - k * exp(-RATE * t) * N(d2)
    return k * exp(-RATE * t) * N(-d2) - s * N(-d1)


def strike_for_delta(s, t, v, kind, dlt):
    """Strike whose Black-Scholes delta is +dlt (call) or -dlt (put)."""
    d1 = NormalDist().inv_cdf(dlt if kind == "call" else 1 - dlt)
    return s * exp((RATE + v * v / 2) * t - d1 * v * sqrt(t))


def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def earnings_dates(sym):
    try:
        e = yf.Ticker(sym).get_earnings_dates(limit=100)
        return sorted({pd.Timestamp(x).tz_localize(None).normalize() for x in e.index}) if e is not None else []
    except Exception:
        return []


def strike_step(s):
    """Typical listed strike spacing (what Robinhood shows comes from the exchanges)."""
    return 0.5 if s < 25 else 1 if s < 100 else 2.5 if s < 250 else 5 if s < 1000 else 10


def first_friday(d, min_days):
    """First Friday expiry at least min_days calendar days out (the scanner's first expiry 14-45 days out)."""
    x = d + pd.Timedelta(days=min_days)
    return x + pd.Timedelta(days=(4 - x.weekday()) % 7)


def trade(h, i, kind, dlt, min_dte, iv_mult, spread, tp, sl, fee):
    """Robinhood-style trade: buy 1 contract with a limit order at the open of row i+1 (options trade 9:30-4:00 ET
    only, no extended hours), resting take-profit limit and stop during regular hours on days i+1 and i+2, else sell
    at the day-2 close. If the stock gaps past a level overnight, the order fills at the opening price, not at the
    level (stops can fill much worse). Returns (return on cash paid incl. fees, day-trade?, cost $), or None."""
    if i + 2 >= len(h):
        return None
    v = max(float(h["rv20"].iloc[i]) * iv_mult, 0.15)
    d0 = h.index[i + 1]
    s0 = float(h["Open"].iloc[i + 1])
    expiry = first_friday(d0, min_dte)
    T = lambda d, frac: ((expiry - d).days + frac) / 365       # frac: 0.75 left at the open, 0 at the close
    step = strike_step(s0)
    k = round(strike_for_delta(s0, T(d0, 0.75), v, kind, dlt) / step) * step
    p0 = bs(s0, k, T(d0, 0.75), v, kind) * (1 + spread / 2)     # pay up half the spread at the open
    p0 = round(p0, 2)                                          # options quote in cents
    if p0 < 0.05:
        return None
    paid = p0 * 100 + fee
    sell = lambda s, d, frac: max(bs(s, k, T(d, frac), v, kind) * (1 - spread / 2), 0.0) * 100 - fee
    tp_px, sl_px = p0 * (1 + tp), p0 * (1 + sl)
    for j in (i + 1, i + 2):
        d = h.index[j]
        o, hi, lo, c = (float(h[x].iloc[j]) for x in ("Open", "High", "Low", "Close"))
        best, worst = (hi, lo) if kind == "call" else (lo, hi)
        if j == i + 2:                                         # overnight gap into day 2
            at_open = sell(o, d, 0.75) + fee
            if at_open <= sl_px * 100 or at_open >= tp_px * 100:
                return (at_open - fee) / paid - 1, False, paid
        dt = j == i + 1
        if bs(worst, k, T(d, 0.4), v, kind) * (1 - spread / 2) <= sl_px:
            return (sl_px * 100 - fee) / paid - 1, dt, paid       # stop first when both hit: conservative
        if bs(best, k, T(d, 0.4), v, kind) * (1 - spread / 2) >= tp_px:
            return (tp_px * 100 - fee) / paid - 1, dt, paid
    return sell(float(h["Close"].iloc[i + 2]), h.index[i + 2], 0.0) / paid - 1, False, paid


def load(sym, start):
    h = yf.Ticker(sym).history(start=start, auto_adjust=True)
    if h.empty or len(h) < 80:
        return None
    h.index = h.index.tz_localize(None).normalize()
    c = h["Close"]
    h["ret1"] = c.pct_change()
    h["ret20"] = c / c.shift(20) - 1
    h["vs50"] = c / c.rolling(50).mean() - 1
    h["rsi"] = rsi(c)
    h["rv20"] = h["ret1"].rolling(20).std() * np.sqrt(252)
    h["stock2d"] = h["Close"].shift(-2) / h["Open"].shift(-1) - 1   # entry open -> day-2 close, real
    return h.dropna(subset=["ret20", "vs50", "rsi", "rv20"])


SETUPS = {
    # name: (description, condition on row r and next-day market gap g)
    "every_day": ("every ticker, every day (baseline)", lambda r, g: True),
    "oversold": ("RSI < 30 and down > 15% in 20 days (INTC/GOOG/HOOD now)", lambda r, g: r.rsi < 30 and r.ret20 < -0.15),
    "oversold_gap_up": ("oversold + QQQ opens > +0.4% next morning (like today's futures)",
                        lambda r, g: r.rsi < 30 and r.ret20 < -0.15 and g > 0.004),
    "crash_day": ("day after a -10% or worse one-day drop (INTC Friday)", lambda r, g: r.ret1 <= -0.10),
    "crash_gap_up": ("day after -10% drop + QQQ opens > +0.4%", lambda r, g: r.ret1 <= -0.10 and g > 0.004),
    "downtrend": ("down > 8% in 20 days, below 50-day avg, RSI 30-50", lambda r, g: r.ret20 < -0.08 and r.vs50 < 0 and 30 <= r.rsi <= 50),
    "uptrend": ("up > 8% in 20 days, above 50-day avg, RSI 50-70", lambda r, g: r.ret20 > 0.08 and r.vs50 > 0 and 50 <= r.rsi <= 70),
}


def summarize(rets, stock, dtr=None):
    a = np.array(rets)
    if len(a) == 0:
        return "   n=0"
    s = np.array(stock)
    return (f"n={len(a):5d}  win {np.mean(a > 0):4.0%}  avg {a.mean():+6.1%}  median {np.median(a):+6.1%}  "
            f"hit +50% {np.mean(a >= 0.4999):4.0%}  hit -40% {np.mean(a <= -0.3999):4.0%}  "
            f"$/trade on $200 {a.mean() * 200:+6.1f}  worst {a.min():+5.0%}  day-trade {np.mean(dtr) if dtr is not None else 0:3.0%}  | stock 2d {s.mean():+5.1%}")


def main():
    tickers = (os.environ.get("TICKERS") or f"{WATCH} {EXTRA}").split()
    start = os.environ.get("START") or "2015-01-01"
    iv_mult = float(os.environ.get("IV_MULT") or 1.1)
    spread = float(os.environ.get("SPREAD") or 0.06)          # round-trip bid/ask cost as a fraction of mid
    fee = float(os.environ.get("FEE") or 0.05)                # Robinhood: $0 commission; pass-through regulatory fees ~ a few cents/contract/side
    budget = float(os.environ.get("BUDGET") or 220)
    dte, tp, sl = 14, 0.50, -0.40                              # first Friday expiry >= 14 days out
    qqq = yf.Ticker("QQQ").history(start=start, auto_adjust=True)
    qqq.index = qqq.index.tz_localize(None).normalize()
    gap = (qqq["Open"] / qqq["Close"].shift(1) - 1).shift(-1)      # gap on the NEXT morning, keyed by signal day

    rows, skipped_earn, loaded = [], 0, []
    for sym in tickers:
        h = load(sym, start)
        if h is None:
            continue
        loaded.append(f"{sym}({h.index[0]:%Y})")
        er = earnings_dates(sym)
        er_arr = np.array(er, dtype="datetime64[ns]") if er else None
        for i, (d, r) in enumerate(h.iloc[:-2].iterrows()):
            g = float(gap.get(d, np.nan))
            names = [n for n, (_, f) in SETUPS.items() if f(r, g if g == g else 0.0)]
            if not names:
                continue
            if er_arr is not None:
                lo, hi = np.datetime64(h.index[i + 1]), np.datetime64(first_friday(h.index[i + 1], dte))
                if ((er_arr >= lo) & (er_arr <= hi)).any():
                    skipped_earn += 1
                    continue
            for kind in ("call", "put"):
                for dlt in (0.30, 0.50):
                    res = trade(h, i, kind, dlt, dte, iv_mult, spread, tp, sl, fee)
                    if res is None:
                        continue
                    ret, dtrade, cost = res
                    st = r.stock2d if kind == "call" else -r.stock2d
                    for n in names:
                        rows.append((n, kind, dlt, sym, d.year, ret, st, dtrade, cost))

    df = pd.DataFrame(rows, columns=["setup", "kind", "delta", "sym", "year", "ret", "stock", "daytrade", "cost"])
    L = [f"BACKTEST {pd.Timestamp.now():%Y-%m-%d %H:%M} UTC  (SYNTHETIC option prices, real stock prices)",
         f"Robinhood rules: 1 contract (x100), limit buy at next day's 9:30 open, first Friday expiry >= {dte} days out, "
         f"listed strike steps, +50% take-profit / -40% stop working 9:30-4:00 only (overnight gaps fill at the open), "
         f"else sell day-2 close. $0 commission + ${fee:.2f}/contract/side fees, round-trip spread {spread:.0%}. "
         f"IV = {iv_mult} x 20d realized vol. Earnings inside expiry skipped ({skipped_earn:,} signal-days).",
         "Day-1 exits are DAY TRADES (PDT rule: margin account under $25k = max 3 per 5 trading days; cash account: "
         "proceeds settle next day). Return is on cash paid incl. fees; worst case per trade = -100%.",
         "Tickers (first year of data): " + " ".join(loaded),
         "'stock 2d avg' = real stock move from entry open to day-2 close, in the trade's direction (no model).", ""]
    for name, (desc, _) in SETUPS.items():
        L.append(f"== {name}: {desc}")
        for kind in ("call", "put"):
            for dlt in (0.30, 0.50):
                x = df[(df.setup == name) & (df.kind == kind) & (df.delta == dlt)]
                L.append(f"   {kind.upper():4s} delta {dlt:.2f}  " + summarize(x.ret, x.stock, x.daytrade))
        L.append("")
    L.append("BY YEAR, avg return per trade (delta 0.30)  - a real edge should be positive in most years")
    for name in SETUPS:
        for kind in ("call", "put"):
            x = df[(df.setup == name) & (df.kind == kind) & (df.delta == 0.30)]
            if len(x):
                by = x.groupby("year")["ret"].agg(["mean", "size"])
                L.append(f"   {name:16s} {kind:4s} " + "  ".join(f"{y}:{m:+.0%}({n})" for y, (m, n) in by.iterrows()))
    L.append("")
    L.append("WATCHLIST ONLY (delta 0.30)")
    w = df[df.sym.isin(WATCH.split()) & (df.delta == 0.30)]
    for name in SETUPS:
        for kind in ("call", "put"):
            x = w[(w.setup == name) & (w.kind == kind)]
            L.append(f"   {name:16s} {kind:4s} " + summarize(x.ret, x.stock, x.daytrade))
    L.append("")
    L.append(f"ONLY CONTRACTS COSTING <= ${budget:,.0f} (what you can actually buy with your budget), all tickers")
    b = df[df.cost <= budget]
    for name in SETUPS:
        for kind in ("call", "put"):
            for dlt in (0.30, 0.50):
                x = b[(b.setup == name) & (b.kind == kind) & (b.delta == dlt)]
                L.append(f"   {name:16s} {kind:4s} d{dlt:.2f} " + summarize(x.ret, x.stock, x.daytrade))
    text = "\n".join(L)
    out = ROOT / "output" / "options"
    out.mkdir(parents=True, exist_ok=True)
    (out / "backtest.txt").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
