"""
Backtest of the strategies the research says have a chance, on SPY and QQQ, where Yahoo has REAL historical implied
volatility: VIX (S&P 500, for SPY) and VXN (Nasdaq-100, for QQQ). Option prices are still Black-Scholes, but driven by
the real day-by-day implied vol, the real T-bill rate (^IRX) and a put skew, so the volatility risk premium is
measured, not assumed.

Strategies (Robinhood: $0 commission + regulatory fees, limit orders, regular hours, one position at a time):
  A. PUT CREDIT SPREAD (sell a put, buy a cheaper one below; max loss = width - credit). Research: index implied vol
     has averaged ~4 points above realized (Bondarenko / Cboe PUT index); 45 DTE, take profit at 50%, exit at 21 DTE
     are tastytrade's heuristics. Optional trend filter: only open when the ETF is above its 200-day average.
  B. LONG CALL with time: 60 DTE, delta 0.60, only above the 200-day average, hold up to 21 trading days, +50%/-50%.
  C. The 2-day hold (delta 0.30, ~14 DTE, +50%/-40%) with real IV, to double-check the first backtest.

Sensitivity: ATM IV = VIX x {0.85, 0.92, 1.0} (VIX sits a bit above ATM IV), skew slope {0, 1.0, 1.5}, and the two
halves of history separately. A strategy only counts if it holds up across these, not just at the best setting.

    python strategies.py   -> output/options/strategies.txt
"""
from math import erf, exp, log, sqrt
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent
N = lambda x: 0.5 * (1 + erf(x / sqrt(2)))
UNDER = {"SPY": ("^VIX", 0.013), "QQQ": ("^VXN", 0.006)}     # ETF: (implied vol index, dividend yield)
FEE = 0.05          # $ per contract per leg per side (Robinhood regulatory pass-through, approx)
HALF = 0.005        # half the bid/ask as a fraction of each leg's price (SPY/QQQ 30-60 DTE quotes are a few cents wide)
MIN_HALF = 0.01     # at least 1 cent per leg


def bs(s, k, t, v, r, q, kind):
    if t <= 0 or v <= 0:
        return max(s - k, 0.0) if kind == "call" else max(k - s, 0.0)
    d1 = (log(s / k) + (r - q + v * v / 2) * t) / (v * sqrt(t))
    d2 = d1 - v * sqrt(t)
    if kind == "call":
        return s * exp(-q * t) * N(d1) - k * exp(-r * t) * N(d2)
    return k * exp(-r * t) * N(-d2) - s * exp(-q * t) * N(-d1)


def iv_at(atm, s, k, b):
    """Put skew: lower strikes trade at higher implied vol. +b vol per unit of log-moneyness below spot."""
    return float(np.clip(atm + b * log(s / k), atm * 0.6, atm * 3))


def strike_for(s, t, atm, r, q, kind, dlt, b, step):
    k = s
    for _ in range(3):                       # iterate: the skew changes the vol, which changes the strike
        v = iv_at(atm, s, k, b)
        x = dlt * exp(q * t)
        d1 = NormalDist().inv_cdf(x) if kind == "call" else -NormalDist().inv_cdf(x)
        k = s * exp((r - q + v * v / 2) * t - d1 * v * sqrt(t))
    return max(round(k / step) * step, step)


def cost(px, half=HALF):
    return max(MIN_HALF, half * px)


def load():
    d = {}
    irx = yf.Ticker("^IRX").history(start="2004-01-01", auto_adjust=False)["Close"]
    irx.index = irx.index.tz_localize(None).normalize()
    for etf, (vix, q) in UNDER.items():
        h = yf.Ticker(etf).history(start="2004-01-01", auto_adjust=False)
        v = yf.Ticker(vix).history(start="2004-01-01", auto_adjust=False)["Close"]
        for x in (h, v):
            x.index = x.index.tz_localize(None).normalize()
        df = pd.DataFrame({"close": h["Close"], "vix": v / 100}).join(irx.rename("r") / 100, how="left")
        df["r"] = df["r"].ffill().fillna(0.02)
        df["sma200"] = df["close"].rolling(200).mean()
        df = df.dropna()
        d[etf] = (df, q)
    return d


def run_spread(df, q, dte=45, dlt=0.30, width_pct=0.01, tp=0.50, exit_dte=21, stop=None, trend=True,
               atm_mult=0.92, b=1.0, half=HALF):
    """Sequential put credit spreads. Returns a list of (entry date, return on max risk, days held)."""
    out, i, idx = [], 0, df.index
    while i < len(df) - 1:
        row = df.iloc[i]
        if trend and row.close < row.sma200:
            i += 1
            continue
        s, r, atm = row.close, row.r, row.vix * atm_mult
        exp_date = idx[i] + pd.Timedelta(days=dte)
        t = dte / 365
        step = 1.0
        ks = strike_for(s, t, atm, r, q, "put", dlt, b, step)
        width = max(step, round(s * width_pct / step) * step)
        kl = ks - width
        ps, pl = bs(s, ks, t, iv_at(atm, s, ks, b), r, q, "put"), bs(s, kl, t, iv_at(atm, s, kl, b), r, q, "put")
        credit = (ps - cost(ps, half)) - (pl + cost(pl, half))                 # sell short leg at bid, buy long leg at ask
        if credit <= 0.05:
            i += 1
            continue
        risk = (width - credit) * 100 + 2 * FEE
        j, pnl = i, None
        while j < len(df) - 1:
            j += 1
            rj = df.iloc[j]
            left = (exp_date - idx[j]).days
            tj, atmj = max(left, 0) / 365, rj.vix * atm_mult
            vs, vl = bs(rj.close, ks, tj, iv_at(atmj, rj.close, ks, b), rj.r, q, "put"), \
                bs(rj.close, kl, tj, iv_at(atmj, rj.close, kl, b), rj.r, q, "put")
            if left <= 0:                                           # expiry: settle at intrinsic, no spread cost
                debit = min(max(ks - rj.close, 0) - max(kl - rj.close, 0), width)
                pnl = (credit - debit) * 100 - 2 * FEE
                break
            debit = min((vs + cost(vs, half)) - max(vl - cost(vl, half), 0), width)
            p = credit - debit
            if p >= tp * credit or (exit_dte and left <= exit_dte) or (stop and -p >= stop * credit):
                pnl = p * 100 - 4 * FEE
                break
        if pnl is None:
            break
        out.append((idx[i], pnl / risk, (idx[j] - idx[i]).days))
        i = j + 1
    return out


def run_long(df, q, kind="call", dte=60, dlt=0.60, hold=21, tp=0.50, sl=-0.50, trend=True, atm_mult=0.92, b=1.0,
             every=1):
    """Long options. hold = max trading days. every = consider a new entry every N days (2-day test: every day)."""
    out, i, idx = [], 0, df.index
    while i < len(df) - hold - 1:
        row = df.iloc[i]
        if trend and row.close < row.sma200:
            i += 1
            continue
        s, r, atm = row.close, row.r, row.vix * atm_mult
        t = dte / 365
        k = strike_for(s, t, atm, r, q, kind, dlt, b, 1.0)
        p = bs(s, k, t, iv_at(atm, s, k, b), r, q, kind)
        paid = (p + cost(p)) * 100 + FEE
        if p < 0.05:
            i += 1
            continue
        for j in range(i + 1, i + hold + 1):
            rj = df.iloc[j]
            tj = max(dte - (idx[j] - idx[i]).days, 0) / 365
            v = bs(rj.close, k, tj, iv_at(rj.vix * atm_mult, rj.close, k, b), rj.r, q, kind)
            got = (v - cost(v)) * 100 - FEE
            ret = got / paid - 1
            if ret >= tp or ret <= sl:
                break
        out.append((idx[i], ret, (idx[j] - idx[i]).days))
        i = j + 1 if every == 0 else i + every
    return out


def stats(trades, risk_frac=0.10, start=None, end=None):
    t = [x for x in trades if (start is None or x[0] >= start) and (end is None or x[0] < end)]
    if not t:
        return "n=0", np.nan
    r = np.array([x[1] for x in t])
    eq = np.cumprod(1 + risk_frac * r)
    years = max((t[-1][0] - t[0][0]).days / 365.25, 0.5)
    cagr = eq[-1] ** (1 / years) - 1
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return (f"n={len(r):4d} win {np.mean(r > 0):4.0%} avg {r.mean():+6.1%} worst {r.min():+5.0%} "
            f"days {np.mean([x[2] for x in t]):4.0f} | risking {risk_frac:.0%}/trade: {cagr:+5.1%}/yr maxDD {dd:+4.0%}"), r.mean()


def main():
    data = load()
    mid = pd.Timestamp("2015-07-01")
    L = [f"STRATEGY BACKTEST {pd.Timestamp.now():%Y-%m-%d %H:%M} UTC. SPY with real VIX, QQQ with real VXN as implied vol "
         "(Black-Scholes + put skew + real T-bill rate). One position at a time per ETF. Bid/ask half-spread 0.5% of each leg "
         f"(min $0.01), ${FEE:.2f}/contract/leg/side fees. Not advice. Past results are no promise.", ""]
    for etf, (df, q) in data.items():
        bh = df.close
        yrs = (bh.index[-1] - bh.index[0]).days / 365.25
        L.append(f"##### {etf}  {df.index[0]:%Y-%m-%d} to {df.index[-1]:%Y-%m-%d}   buy & hold (price only) "
                 f"{(bh.iloc[-1] / bh.iloc[0]) ** (1 / yrs) - 1:+.1%}/yr, maxDD {(bh / bh.cummax() - 1).min():+.0%}")
        L.append("  A. PUT CREDIT SPREADS (return per trade is on max risk; 'risking 10%' = 10% of account at max loss per trade)")
        base = dict(dte=45, dlt=0.30, tp=0.50, exit_dte=21, trend=True, atm_mult=0.92, b=1.0)
        grid = [("base: 45 DTE, short 0.30 delta, 1% wide, TP 50%, exit 21 DTE, above 200d", {}),
                ("  no trend filter", dict(trend=False)),
                ("  short 0.16 delta", dict(dlt=0.16)),
                ("  short 0.20 delta", dict(dlt=0.20)),
                ("  30 DTE, exit 10 DTE", dict(dte=30, exit_dte=10)),
                ("  hold to expiry (no TP, no 21 DTE exit)", dict(tp=99, exit_dte=0)),
                ("  + stop at 2x credit", dict(stop=2.0)),
                ("  IV = VIX x 0.85 (harsher)", dict(atm_mult=0.85)),
                ("  IV = VIX x 1.00", dict(atm_mult=1.0)),
                ("  no skew", dict(b=0.0)),
                ("  steep skew 1.5", dict(b=1.5)),
                ("  bid/ask 3x wider (1.5% of each leg)", dict(half=0.015)),
                ("  harsh combo: IV x0.85, skew 1.5, bid/ask 1.5%", dict(atm_mult=0.85, b=1.5, half=0.015))]
        for name, kw in grid:
            tr = run_spread(df, q, **{**base, **kw})
            s_all, _ = stats(tr)
            s1, m1 = stats(tr, end=mid)
            s2, m2 = stats(tr, start=mid)
            L.append(f"   {name:55s} {s_all}")
            L.append(f"   {'':55s}   1st half avg {m1:+.1%}  2nd half avg {m2:+.1%}")
        L.append("  B. LONG CALL 60 DTE, delta 0.60, above 200d, max 21 trading days, +50%/-50%")
        for name, kw in [("base", {}), ("  no trend filter", dict(trend=False)), ("  IV = VIX x 0.85", dict(atm_mult=0.85))]:
            tr = run_long(df, q, every=0, **kw)
            s_all, _ = stats(tr)
            _, m1 = stats(tr, end=mid)
            _, m2 = stats(tr, start=mid)
            L.append(f"   {name:55s} {s_all}   halves {m1:+.1%} / {m2:+.1%}")
        L.append("  C. THE 2-DAY HOLD with real IV (delta 0.30, 14 DTE, +50%/-40%, entered every day)")
        for kind in ("call", "put"):
            tr = run_long(df, q, kind=kind, dte=14, dlt=0.30, hold=2, tp=0.50, sl=-0.40, trend=False, every=1)
            s_all, _ = stats(tr, risk_frac=0.02)
            L.append(f"   {kind:55s} {s_all}")
        L.append("")
    text = "\n".join(L)
    out = ROOT / "output" / "options"
    out.mkdir(parents=True, exist_ok=True)
    (out / "strategies.txt").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
