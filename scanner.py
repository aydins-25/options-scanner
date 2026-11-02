"""
Options scanner for a watchlist. Real-brokerage (Robinhood-style) options, not SMG. Separate from the SMG scanner.

For each ticker: trend, realized vs implied volatility, earnings date, the most liquid near-the-money calls and
puts 2 to 6 weeks out. With a budget, it also ranks every contract you can afford for a short hold (1-2 days).

This is a data sheet plus a heuristic ranking, not a tested strategy: nothing here has been backtested.

    python scanner.py NVDA ORCL ...                  -> output/options/options_scan.txt
    BUDGET=220 HOLD_DAYS=2 python scanner.py INTC GOOG
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from math import erf, exp, log, sqrt

RATE = 0.04

ROOT = Path(__file__).resolve().parent


def pct(x):
    return f"{x:+.1%}" if pd.notna(x) else "n/a"


def trend(t):
    h = t.history(period="1y", auto_adjust=True)
    c = h["Close"]
    r = c.pct_change()
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    return {
        "spot": c.iloc[-1], "ret5": c.iloc[-1] / c.iloc[-6] - 1, "ret20": c.iloc[-1] / c.iloc[-21] - 1,
        "ret60": c.iloc[-1] / c.iloc[-61] - 1, "vs50": c.iloc[-1] / c.tail(50).mean() - 1,
        "vs200": c.iloc[-1] / c.tail(200).mean() - 1 if len(c) >= 200 else np.nan,
        "rsi": float(100 - 100 / (1 + up.iloc[-1] / dn.iloc[-1])) if dn.iloc[-1] > 0 else 100.0,
        "rv20": r.tail(20).std() * np.sqrt(252), "hi52": c.max(), "lo52": c.min(),
        "date": h.index[-1].date(),
    }


def earnings(t):
    try:
        cal = t.calendar
        d = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if d:
            return pd.Timestamp(d[0]).date()
    except Exception:
        pass
    return None


def bs_price(spot, k, t, vol, kind):
    if vol <= 0 or t <= 0:
        return max(spot - k, 0) if kind == "call" else max(k - spot, 0)
    d1 = (log(spot / k) + (RATE + vol * vol / 2) * t) / (vol * sqrt(t))
    d2 = d1 - vol * sqrt(t)
    n = lambda x: 0.5 * (1 + erf(x / sqrt(2)))
    if kind == "call":
        return spot * n(d1) - k * exp(-RATE * t) * n(d2)
    return k * exp(-RATE * t) * n(-d2) - spot * n(-d1)


def implied_vol(price, spot, k, t, kind):
    """Volatility implied by an option price (Yahoo's own IV is garbage outside market hours)."""
    lo, hi = 0.01, 5.0
    if not price or price <= bs_price(spot, k, t, lo, kind):
        return np.nan
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if bs_price(spot, k, t, mid, kind) < price else (lo, mid)
    return (lo + hi) / 2


def delta(spot, k, t, vol, kind):
    if not vol or vol != vol or t <= 0:
        return np.nan
    d1 = (log(spot / k) + (RATE + vol * vol / 2) * t) / (vol * sqrt(t))
    n = 0.5 * (1 + erf(d1 / sqrt(2)))
    return n if kind == "call" else n - 1


def affordable(chain, spot, kind, days, budget, rv, hold):
    """Every contract within 15% of the price that costs <= budget, scored for a short hold.
    score = (expected $ swing from one normal day's move - time decay over the hold) / cost. Heuristic, untested."""
    d = chain.copy()
    d["mid"] = ((d["bid"] + d["ask"]) / 2).where((d["bid"] > 0) & (d["ask"] > 0), d["lastPrice"])
    d = d[(d["strike"] / spot - 1).abs().le(0.15) & (d["mid"] > 0.05) & (d["mid"] * 100 <= budget)].copy()
    if d.empty:
        return d
    t = days / 365
    d["iv"] = [implied_vol(m, spot, k, t, kind) for m, k in zip(d["mid"], d["strike"])]
    d = d[d["iv"].notna()]
    d["delta"] = [delta(spot, k, t, v, kind) for k, v in zip(d["strike"], d["iv"])]
    d["decay"] = [bs_price(spot, k, t, v, kind) - bs_price(spot, k, max(t - hold / 365, 1 / 365), v, kind)
                  for k, v in zip(d["strike"], d["iv"])]
    d["swing"] = d["delta"].abs() * spot * rv / np.sqrt(252)
    d["cost"] = d["mid"] * 100
    d["spread"] = ((d["ask"] - d["bid"]) / d["mid"]).where(d["bid"] > 0)
    d["score"] = (d["swing"] - d["decay"]) * 100 / d["cost"]
    d["kind"], d["days"] = kind, days
    return d


def pick(chain, spot, kind, days):
    """Most liquid strikes from ~5% in the money to ~10% out of the money."""
    d = chain.copy()
    d["mid"] = (d["bid"] + d["ask"]) / 2
    d["mid"] = d["mid"].where(d["mid"] > 0, d["lastPrice"])
    d["spread"] = (d["ask"] - d["bid"]) / d["mid"]
    lo, hi = (spot * 0.95, spot * 1.10) if kind == "call" else (spot * 0.90, spot * 1.05)
    d = d[(d["strike"] >= lo) & (d["strike"] <= hi) & (d["mid"] > 0)]
    d["oi"] = d["openInterest"].fillna(0)
    d = d.sort_values("oi", ascending=False).head(3).sort_values("strike")
    d["iv"] = [implied_vol(m, spot, k, days / 365, kind) for m, k in zip(d["mid"], d["strike"])]
    d["breakeven"] = d["strike"] + d["mid"] if kind == "call" else d["strike"] - d["mid"]
    d["move"] = d["breakeven"] / spot - 1
    return d


def main(tickers, budget=None, hold=2):
    today = pd.Timestamp.now().normalize()
    cands = []
    L = [f"OPTIONS SNAPSHOT {pd.Timestamp.now():%Y-%m-%d %H:%M} UTC. Prices are the latest Yahoo quotes "
         "(stale outside market hours: re-check bid/ask in your broker before ordering).",
         "Option prices: live bid/ask mid, or the last trade when the market is closed. IV is computed from that price. Not backtested. 'Move to breakeven' = how far the stock must go by expiry for the option to not lose money.", ""]
    summary = []
    for sym in tickers:
        t = yf.Ticker(sym)
        try:
            tr = trend(t)
        except Exception as e:
            L.append(f"{sym}: no price data ({e})")
            continue
        er = earnings(t)
        exps = [e for e in t.options if 14 <= (pd.Timestamp(e) - today).days <= 45]
        L.append("=" * 100)
        L.append(f"{sym}  ${tr['spot']:.2f} (close {tr['date']})   5d {pct(tr['ret5'])}  20d {pct(tr['ret20'])}  60d {pct(tr['ret60'])}   "
                 f"vs 50-day avg {pct(tr['vs50'])}  vs 200-day {pct(tr['vs200'])}   RSI {tr['rsi']:.0f}")
        L.append(f"   52-week range ${tr['lo52']:.2f} - ${tr['hi52']:.2f}   realized vol (20d) {tr['rv20']:.0%}   "
                 f"next earnings: {er or 'unknown'}")
        atm_iv = np.nan
        for e in exps[:2]:
            ch = t.option_chain(e)
            days = (pd.Timestamp(e) - today).days
            calls, puts = pick(ch.calls, tr["spot"], "call", days), pick(ch.puts, tr["spot"], "put", days)
            if np.isnan(atm_iv):
                near = ch.calls.iloc[(ch.calls["strike"] - tr["spot"]).abs().argsort()[:1]]
                if len(near):
                    m = (near["bid"].iloc[0] + near["ask"].iloc[0]) / 2 or near["lastPrice"].iloc[0]
                    atm_iv = implied_vol(m, tr["spot"], near["strike"].iloc[0], days / 365, "call")
            exp_move = tr["spot"] * atm_iv * np.sqrt(days / 365) if pd.notna(atm_iv) else np.nan
            L.append(f"   EXPIRY {e} ({days} days)" + (f"  EARNINGS BEFORE EXPIRY ({er})" if er and today <= pd.Timestamp(er) <= pd.Timestamp(e) else "")
                     + (f"   market expects +/-${exp_move:.2f} ({exp_move / tr['spot']:.0%}) by then" if pd.notna(exp_move) else ""))
            if budget:
                for kind, chain in (("call", ch.calls), ("put", ch.puts)):
                    a = affordable(chain, tr["spot"], kind, days, budget, tr["rv20"], hold)
                    if len(a):
                        a["sym"], a["expiry"], a["spot"] = sym, e, tr["spot"]
                        a["earn"] = bool(er and today <= pd.Timestamp(er) <= pd.Timestamp(e))
                        a["trend20"], a["rsi"] = tr["ret20"], tr["rsi"]
                        cands.append(a)
            for kind, d in (("CALL", calls), ("PUT ", puts)):
                for _, o in d.iterrows():
                    L.append(f"      {kind} ${o['strike']:<7g} mid ${o['mid']:.2f} (${o['mid'] * 100:,.0f}/contract)  "
                             f"bid/ask {o['bid']:.2f}/{o['ask']:.2f} spread {o['spread']:.0%}  IV {o['iv']:.0%}  "
                             f"OI {int(o['oi']):,}  vol {int(o['volume']) if pd.notna(o['volume']) else 0:,}  "
                             f"breakeven ${o['breakeven']:.2f} ({o['move']:+.1%})")
        ivrv = atm_iv / tr["rv20"] if pd.notna(atm_iv) and tr["rv20"] > 0 else np.nan
        L.append(f"   ATM implied vol {atm_iv:.0%} vs realized {tr['rv20']:.0%}  (ratio {ivrv:.2f}: "
                 + ("options EXPENSIVE vs recent moves" if ivrv > 1.3 else "options CHEAP vs recent moves" if ivrv < 0.9 else "about fair")
                 + ")" if pd.notna(ivrv) else "   implied vol n/a")
        summary.append((sym, tr, atm_iv, ivrv, er))
    L += ["", "SUMMARY (trend, option price level, earnings)"]
    for sym, tr, iv, ivrv, er in summary:
        L.append(f"   {sym:5s} ${tr['spot']:>8.2f}  20d {pct(tr['ret20']):>7}  vs50 {pct(tr['vs50']):>7}  RSI {tr['rsi']:3.0f}  "
                 f"IV {iv:4.0%}  IV/RV {ivrv:4.2f}  earnings {er or 'unknown'}")
    if budget:
        L += ["", f"TOP PICKS for a ${budget:,.0f} budget, {hold}-day hold (heuristic: one normal day's move vs time decay, per $; untested)"]
        if cands:
            c = pd.concat(cands)
            # no earnings inside, no wide spreads, and near the money only: cheap far-out contracts score high on
            # "move per $" but usually expire worthless (first test ranked an INTC $75 call with the stock at $66 #1)
            c = c[~c["earn"] & (c["spread"].isna() | (c["spread"] <= 0.15)) & c["delta"].abs().between(0.25, 0.60)]
            for i, (_, o) in enumerate(c.sort_values("score", ascending=False).drop_duplicates(["sym", "kind"]).head(5).iterrows(), 1):
                L.append(f"   {i}. {o['sym']} {o['expiry']} ${o['strike']:g} {o['kind'].upper()}  ~${o['cost']:,.0f}  "
                         f"delta {o['delta']:+.2f}  IV {o['iv']:.0%}  decay over {hold}d ~${o['decay'] * 100:,.0f}  "
                         f"one normal day's move ~${o['swing'] * 100:,.0f}  (stock {pct(o['trend20'])} in 20d, RSI {o['rsi']:.0f})")
            if c.empty:
                L.append("   nothing affordable near the money (delta 0.25-0.60) without earnings inside: raise the budget or skip")
        else:
            L.append("   nothing affordable")
    text = "\n".join(L)
    out = ROOT / "output" / "options"
    out.mkdir(parents=True, exist_ok=True)
    (out / "options_scan.txt").write_text(text)
    print(text)


if __name__ == "__main__":
    b = float(os.environ.get("BUDGET") or 0) or None
    main([s.upper() for s in sys.argv[1:]] or ["NVDA"], b, int(os.environ.get("HOLD_DAYS") or 2))
