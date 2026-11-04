"""
Paper trading with REAL option quotes (Yahoo, during market hours). No money, no broker: it logs what you would have
paid (the ask) and what you would have got (the bid), so after a few weeks there is a real track record.

Strategies tracked (one open position per strategy and ticker):
  LONG60  SPY and QQQ calls, ~60 days out, delta ~0.60, only when the ETF closed above its 200-day average.
          Exit at +50% / -50% on the bid, or after 21 trading days. The only option strategy that held up in
          strategies.py (2004-2026, real VIX/VXN). It is leveraged index exposure, not a hidden edge.
  TWODAY  The scanner's #1 pick for the watchlist at a $220 budget, held up to 2 trading days, +50% / -40%.
          The old style, tracked for comparison (the backtests say it loses).

Runs once per trading day during market hours (first scheduled run that lands 10:00-15:55 ET) (GitHub Actions "Paper trade", scheduled). State lives in paper/*.csv and
paper/report.md, committed back to the repo by the workflow.
"""
import os
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

import scanner as sc

ROOT = Path(__file__).resolve().parent
DIR = ROOT / "paper"
OPEN, CLOSED, REPORT = DIR / "open.csv", DIR / "closed.csv", DIR / "report.md"
COLS = ["strategy", "sym", "contract", "kind", "expiry", "strike", "opened", "entry", "entry_mid", "tp", "sl",
        "max_days", "underlying_at_entry"]
WATCH = (os.environ.get("WATCH") or "NBIS NVDA ORCL INTC HOOD BE MU SNDK GOOG").split()
BUDGET = float(os.environ.get("BUDGET") or 220)
FEE = 0.05   # $/contract/side, approx regulatory fees (Robinhood commission is $0)


def read(p, cols):
    return pd.read_csv(p) if p.exists() and p.stat().st_size else pd.DataFrame(columns=cols)


def quote(sym, expiry, contract):
    ch = yf.Ticker(sym).option_chain(expiry)
    for d in (ch.calls, ch.puts):
        row = d[d["contractSymbol"] == contract]
        if len(row):
            r = row.iloc[0]
            return float(r["bid"] or 0), float(r["ask"] or 0)
    return 0.0, 0.0


def trading_days(a, b):
    return int(np.busday_count(pd.Timestamp(a).date(), pd.Timestamp(b).date()))


def long60_signal(sym, today):
    t = yf.Ticker(sym)
    h = t.history(period="1y", auto_adjust=False)["Close"]
    spot, sma = float(h.iloc[-1]), float(h.tail(200).mean())
    if spot < sma:
        return None, f"{sym}: no entry, ${spot:.2f} is below its 200-day average ${sma:.2f} (rule: only above)"
    exps = [e for e in t.options if 50 <= (pd.Timestamp(e) - today).days <= 75]
    if not exps:
        return None, f"{sym}: no expiry 50-75 days out"
    e = min(exps, key=lambda x: abs((pd.Timestamp(x) - today).days - 60))
    days = (pd.Timestamp(e) - today).days
    c = t.option_chain(e).calls
    c = c[(c["bid"] > 0) & (c["ask"] > 0) & ((c["strike"] / spot - 1).abs() <= 0.15)].copy()
    c = c[(c["ask"] - c["bid"]) / ((c["ask"] + c["bid"]) / 2) <= 0.10]          # stale/garbage quotes have wide spreads
    if c.empty:
        return None, f"{sym}: no clean live quotes near the money (market closed?)"
    c["mid"] = (c["bid"] + c["ask"]) / 2
    c["iv"] = [sc.implied_vol(m, spot, k, days / 365, "call") for m, k in zip(c["mid"], c["strike"])]
    c["delta"] = [sc.delta(spot, k, days / 365, v, "call") for k, v in zip(c["strike"], c["iv"])]
    c = c.dropna(subset=["delta"])
    c = c[c["delta"].between(0.50, 0.70) & (c["iv"] < 1.0)]
    if c.empty:
        return None, f"{sym}: no strike with delta 0.50-0.70 and sane IV in the live quotes"
    o = c.iloc[(c["delta"] - 0.60).abs().argsort().iloc[0]]
    pos = dict(strategy="LONG60", sym=sym, contract=o["contractSymbol"], kind="call", expiry=e, strike=o["strike"],
               opened=str(today.date()), entry=float(o["ask"]), entry_mid=float(o["mid"]), tp=0.5, sl=-0.5,
               max_days=21, underlying_at_entry=spot)
    msg = (f"{sym}: BUY 1 {sym} {e} ${o['strike']:g} CALL, limit ~${o['mid']:.2f} (bid {o['bid']:.2f} / ask "
           f"{o['ask']:.2f}) = ~${o['mid'] * 100:,.0f}. delta {o['delta']:.2f}, IV {o['iv']:.0%}, {days} days. "
           f"Sell at +50% (${o['ask'] * 1.5:.2f}) or -50% (${o['ask'] * 0.5:.2f}) or after 21 trading days.")
    return pos, msg


def twoday_signal(today):
    cands = []
    for sym in WATCH:
        try:
            t = yf.Ticker(sym)
            tr, er = sc.trend(t), sc.earnings(t)
            for e in [x for x in t.options if 14 <= (pd.Timestamp(x) - today).days <= 45][:2]:
                ch, days = t.option_chain(e), (pd.Timestamp(e) - today).days
                for kind, chain in (("call", ch.calls), ("put", ch.puts)):
                    a = sc.affordable(chain, tr["spot"], kind, days, BUDGET, tr["rv20"], 2)
                    if len(a):
                        a["sym"], a["expiry"], a["spot"] = sym, e, tr["spot"]
                        a["earn"] = bool(er and today <= pd.Timestamp(er) <= pd.Timestamp(e))
                        cands.append(a)
        except Exception as ex:
            print(f"TWODAY {sym}: {ex}")
    if not cands:
        return None, "TWODAY: nothing affordable"
    c = pd.concat(cands)
    c = c[~c["earn"] & c["spread"].notna() & (c["spread"] <= 0.15) & c["delta"].abs().between(0.25, 0.60)
          & (c["ask"] > 0) & (c["ask"] * 100 <= BUDGET)]
    if c.empty:
        return None, "TWODAY: nothing passes the filters with live quotes"
    o = c.sort_values("score", ascending=False).iloc[0]
    pos = dict(strategy="TWODAY", sym=o["sym"], contract=o["contractSymbol"], kind=o["kind"], expiry=o["expiry"],
               strike=o["strike"], opened=str(today.date()), entry=float(o["ask"]), entry_mid=float(o["mid"]),
               tp=0.5, sl=-0.4, max_days=2, underlying_at_entry=float(o["spot"]))
    return pos, (f"TWODAY (comparison only): {o['sym']} {o['expiry']} ${o['strike']:g} {o['kind'].upper()} "
                 f"ask ${o['ask']:.2f}")


def main():
    DIR.mkdir(exist_ok=True)
    today = pd.Timestamp.now(tz="America/New_York").tz_localize(None).normalize()
    now = pd.Timestamp.now(tz="America/New_York")
    if today.weekday() >= 5:
        print("weekend, nothing to do")
        return
    # GitHub's scheduler runs hours late, so the workflow fires every 30 min through the day and the FIRST run that
    # lands in market hours (10:00-15:55 ET, live quotes) does the day's work; later runs that day skip.
    last = DIR / "last_run.txt"
    if not os.environ.get("FORCE"):
        if not (10 * 60 <= now.hour * 60 + now.minute <= 15 * 60 + 55):
            print(f"{now:%H:%M} ET is outside market hours 10:00-15:55, skipping")
            return
        if last.exists() and last.read_text().strip() == str(today.date()):
            print(f"already ran today ({today.date()}), skipping")
            return
    op, cl = read(OPEN, COLS), read(CLOSED, COLS + ["closed", "exit", "pnl", "ret", "why"])
    log = [f"Run {pd.Timestamp.now(tz='America/New_York'):%Y-%m-%d %H:%M} ET"]

    # 1. manage open positions with live bids
    keep = []
    for _, p in op.iterrows():
        bid, ask = quote(p["sym"], p["expiry"], p["contract"])
        if bid <= 0 and ask <= 0:
            log.append(f"{p['strategy']} {p['contract']}: no live quote, holding")
            keep.append(p)
            continue
        ret = (bid * 100 - FEE) / (p["entry"] * 100 + FEE) - 1
        held = trading_days(p["opened"], today)
        why = "take profit" if ret >= p["tp"] else "stop" if ret <= p["sl"] else "time" if held >= p["max_days"] else None
        if why:
            pnl = (bid - p["entry"]) * 100 - 2 * FEE
            cl.loc[len(cl)] = {**p.to_dict(), "closed": str(today.date()), "exit": bid, "pnl": round(pnl, 2),
                               "ret": round(ret, 4), "why": why}
            log.append(f"CLOSE {p['strategy']} {p['contract']} at bid ${bid:.2f}: {ret:+.0%} (${pnl:+.0f}), {why}")
        else:
            log.append(f"HOLD {p['strategy']} {p['contract']}: bid ${bid:.2f} vs paid ${p['entry']:.2f} ({ret:+.0%}), "
                       f"day {held}/{p['max_days']}")
            keep.append(p)
    op = pd.DataFrame(keep, columns=COLS)

    # 2. new entries (one open position per strategy+ticker)
    def has(strategy, sym=None):
        return ((op["strategy"] == strategy) & ((op["sym"] == sym) if sym else True)).any()

    signals = []
    for sym in ("SPY", "QQQ"):
        if has("LONG60", sym):
            continue
        pos, msg = long60_signal(sym, today)
        signals.append(msg)
        if pos:
            op.loc[len(op)] = pos
    if not has("TWODAY"):
        pos, msg = twoday_signal(today)
        signals.append(msg)
        if pos:
            op.loc[len(op)] = pos

    dry = bool(os.environ.get("FORCE"))
    if not dry:
        last.write_text(str(today.date()) + "\n")
        op.to_csv(OPEN, index=False)
        cl.to_csv(CLOSED, index=False)

    # 3. report
    R = [f"# Paper trading (real quotes, no money)\n", f"_Last run: {log[0][4:]}. Buys at the ask, sells at the bid, "
         f"${FEE:.2f}/contract/side fees. Not advice; small samples prove nothing._\n", "## Today's signals"]
    R += [f"- {s}" for s in signals] or ["- none (positions already open)"]
    R += ["", "## Open positions", "| strategy | contract | opened | paid |", "|---|---|---|---|"]
    R += [f"| {p.strategy} | {p.sym} {p.expiry} ${p.strike:g} {p.kind} | {p.opened} | ${p.entry:.2f} |" for p in op.itertuples()]
    R += ["", "## Results so far", "| strategy | trades | win % | total $ | avg % |", "|---|---|---|---|---|"]
    for s, g in cl.groupby("strategy"):
        R.append(f"| {s} | {len(g)} | {(g['pnl'] > 0).mean():.0%} | ${g['pnl'].sum():+,.0f} | {g['ret'].mean():+.1%} |")
    R += ["", "## Log (this run)"] + [f"- {x}" for x in log[1:]]
    if dry:
        R.insert(1, "**DRY RUN (forced outside 14:45-15:55 ET): nothing saved, quotes may be stale.**\n")
    else:
        REPORT.write_text("\n".join(R) + "\n")
    print("\n".join(R))


if __name__ == "__main__":
    main()
