"""Stage 1: Price + Volume + OI momentum score for every F&O stock (one quote call).
Stage 2: ATM option with gamma and other greeks for shortlisted stocks."""
import math
import time
from datetime import datetime

import pandas as pd

from dhan_api import now_ist, option_chain, quotes

SESSION_MIN = 375  # 09:15 to 15:30
# Score weights and the move that earns full marks for each factor.
W_PRICE, FULL_PRICE_PCT = 40, 3.0   # 3% futures move
W_OI, FULL_OI_PCT = 30, 8.0         # 8% OI addition
W_VOL, FULL_VOL_X = 30, 3.0         # 3x normal volume


def session_fraction():
    """Share of the trading session elapsed (1.0 when the market is closed)."""
    t = now_ist()
    mins = (t.hour * 60 + t.minute) - (9 * 60 + 15)
    if t.weekday() >= 5 or mins <= 0 or mins >= SESSION_MIN:
        return 1.0
    return max(mins, 5) / SESSION_MIN


def _signal(pct, oi_chg):
    if pd.isna(oi_chg):
        return "Price up" if pct > 0 else "Price down"
    if pct > 0:
        return "Long build-up" if oi_chg > 0 else "Short covering"
    return "Short build-up" if oi_chg > 0 else "Long unwinding"


def scan(universe, baseline):
    q = quotes({"NSE_FNO": universe["fut_id"], "NSE_EQ": universe["eq_id"]})
    fq, eq = q.get("NSE_FNO", {}), q.get("NSE_EQ", {})
    frac = session_fraction()
    base = baseline.set_index("symbol").to_dict("index") if len(baseline) else {}

    rows = []
    for r in universe.itertuples():
        f, e = fq.get(str(r.fut_id)), eq.get(str(r.eq_id), {})
        if not f or not f.get("last_price"):
            continue
        ltp = f["last_price"]
        prev = ltp - f["net_change"] if f.get("net_change") is not None \
            else (f.get("ohlc") or {}).get("close")
        if not prev:
            continue
        pct = (ltp / prev - 1) * 100

        b = base.get(r.symbol, {})
        oi = f.get("oi")
        # After market close the latest daily candle already equals live OI; step back one day.
        ref_oi = b.get("oi_prev") if oi == b.get("oi_last") else b.get("oi_last")
        oi_chg = (oi / ref_oi - 1) * 100 if oi and ref_oi and ref_oi == ref_oi else float("nan")

        vol, avg = e.get("volume"), b.get("vol_avg10")
        vol_x = (vol / frac) / avg if vol and avg and avg == avg else float("nan")

        score = W_PRICE * min(abs(pct) / FULL_PRICE_PCT, 1)
        if oi_chg == oi_chg:
            score += W_OI * min(max(oi_chg, 0) / FULL_OI_PCT, 1)
        if vol_x == vol_x:
            score += W_VOL * min(max(vol_x - 1, 0) / (FULL_VOL_X - 1), 1)

        rows.append({
            "Symbol": r.symbol, "Side": "CE" if pct > 0 else "PE", "Score": round(score),
            "Signal": _signal(pct, oi_chg), "Fut LTP": ltp, "Chg %": round(pct, 2),
            "OI chg %": round(oi_chg, 2), "Vol x": round(vol_x, 2),
            "eq_id": r.eq_id, "fut_id": r.fut_id, "lot": r.lot, "expiry": r.expiry,
            "eq_ltp": e.get("last_price"), "eq_open": (e.get("ohlc") or {}).get("open"),
        })
    cols = ["Symbol", "Side", "Score", "Signal", "Fut LTP", "Chg %", "OI chg %", "Vol x",
            "eq_id", "fut_id", "lot", "expiry", "eq_ltp", "eq_open"]
    df = pd.DataFrame(rows, columns=cols)
    # Rank by score; equal scores (several stocks can hit 100) go by the larger price move.
    df["_move"] = df["Chg %"].abs()
    df = df.sort_values(["Score", "_move"], ascending=False).drop(columns="_move")
    df = df.reset_index(drop=True)
    df.insert(0, "Rank", range(1, len(df) + 1))                # across all stocks
    df.insert(1, "Side rank", df.groupby("Side").cumcount() + 1)  # within CE or within PE
    return _add_previous_ranks(df)


def _option_leg(q, lot):
    """(premium, change % vs previous close, lots traded) from one option quote."""
    if not q or not q.get("last_price"):
        return None, None, None
    ltp, chg = q["last_price"], q.get("net_change")
    prev = ltp - chg if chg is not None else None
    pct = round((ltp / prev - 1) * 100, 2) if prev and prev > 0 else None
    return ltp, pct, int((q.get("volume") or 0) / lot) if lot else None


def _strike_table(options):
    """symbol -> strikes that list both a call and a put (with their ids), and lot sizes."""
    wide = options.pivot_table(index=["symbol", "strike"], columns="type", values="sec_id",
                               aggfunc="first").dropna().astype(int).reset_index()
    lots = options.drop_duplicates("symbol").set_index("symbol")["lot"].to_dict()
    return {s: g for s, g in wide.groupby("symbol")}, lots


def _spread_pct(q):
    """Bid-ask gap as a % of the premium, from the top of the order book."""
    try:
        bid, ask = q["depth"]["buy"][0]["price"], q["depth"]["sell"][0]["price"]
        return round((ask - bid) / q["last_price"] * 100, 2) if bid and ask and q["last_price"] else None
    except (KeyError, IndexError, TypeError):
        return None


MIN_OPTION_LOTS = 200     # lots traded in a full day below which an option counts as thin
RISK_FREE = 0.065         # yearly rate used when estimating implied volatility


def implied_vol(premium, spot, strike, years, side, r=RISK_FREE):
    """Implied volatility in % from the Black-Scholes price, found by bisection.
    Returns None when it cannot be worked out (no premium, expired, or below intrinsic value)."""
    if not premium or not spot or not strike or years <= 0:
        return None

    def price(vol):
        d1 = (math.log(spot / strike) + (r + vol * vol / 2) * years) / (vol * math.sqrt(years))
        d2 = d1 - vol * math.sqrt(years)
        cdf = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))  # noqa: E731
        call = spot * cdf(d1) - strike * math.exp(-r * years) * cdf(d2)
        return call if side == "CE" else call - spot + strike * math.exp(-r * years)

    low, high = 0.01, 5.0
    if not price(low) <= premium <= price(high):
        return None
    for _ in range(60):
        mid = (low + high) / 2
        low, high = (mid, high) if price(mid) < premium else (low, mid)
    return round((low + high) / 2 * 100, 1)


def top_picks(res, options, timing, count=20, pool=10, side="All", max_spread=1.5, wall_near=1.0):
    """The short list to act on, built in three stages:
      1. SCAN: the `pool` highest-scoring stocks on each side from the futures scan;
      2. TIMING: `timing(fut_id, side)` checks the future's 5-minute candles (4 checks);
      3. TRADABILITY: the ATM option's spread, volume, cost, implied volatility and OI wall.
    Timing is a gate on the order, not a blended score: 'Go' rows come first, then 'Almost',
    then 'Wait', each group in scanner-score order with tradable options ahead of the rest."""
    by_symbol, lots = _strike_table(options)
    live = session_fraction() < 1.0
    rows = []
    for s in (("CE", "PE") if side == "All" else (side,)):
        for r in res[res["Side"] == s].head(pool).to_dict("records"):
            g, spot = by_symbol.get(r["Symbol"]), r.get("eq_ltp")
            if g is None or pd.isna(spot) or not spot:
                continue
            k = g.iloc[(g["strike"] - spot).abs().argmin()]
            rows.append({**r, "strike": float(k["strike"]), "sec_id": int(k[s]),
                         "opt_lot": lots.get(r["Symbol"])})
    if not rows:
        return pd.DataFrame()

    q = quotes({"NSE_FNO": [r["sec_id"] for r in rows]}).get("NSE_FNO", {})
    need_lots = max(MIN_OPTION_LOTS * session_fraction(), 20)   # scaled to the time of day
    for r in rows:
        r.update(timing(int(r["fut_id"]), r["Side"]))
        oq = q.get(str(r["sec_id"])) or {}
        r["premium"] = oq.get("last_price")
        # After the close the order book holds only leftover orders, so no spread is shown.
        r["spread"] = _spread_pct(oq) if live else None
        r["opt_lots"] = int((oq.get("volume") or 0) / r["opt_lot"]) if r["opt_lot"] else None
        expiry = datetime.strptime(str(r["expiry"]), "%Y-%m-%d").replace(hour=15, minute=30)
        years = (expiry - now_ist().replace(tzinfo=None)).total_seconds() / (365 * 24 * 3600)
        r["iv"] = implied_vol(r["premium"], r["eq_ltp"], r["strike"], years, r["Side"])
        r["problems"] = []
        if not r["premium"]:
            r["problems"].append("No option quote")
        if r["opt_lots"] is not None and r["opt_lots"] < need_lots:
            r["problems"].append(f"Thin ({r['opt_lots']} lots)")
        if r["spread"] is not None and r["spread"] > max_spread:
            r["problems"].append(f"Wide spread {r['spread']}%")
        r["tier"] = {4: 2, 3: 1}.get(r["passed"], 0)
    rows.sort(key=lambda r: (r["tier"], not r["problems"], r["Score"]), reverse=True)
    rows = rows[:count]

    # One more quote call finds each pick's OI wall: the heaviest strike in the trade's path.
    ahead = {}
    for r in rows:
        o = options[(options["symbol"] == r["Symbol"]) & (options["type"] == r["Side"])]
        o = o[o["strike"] > r["eq_ltp"]] if r["Side"] == "CE" else o[o["strike"] < r["eq_ltp"]]
        o = o.assign(gap=(o["strike"] - r["eq_ltp"]).abs()).nsmallest(25, "gap")
        ahead[r["Symbol"]] = dict(zip(o["sec_id"].astype(int), o["strike"]))
    ids = [i for d in ahead.values() for i in d]
    wq = quotes({"NSE_FNO": ids}).get("NSE_FNO", {}) if ids else {}

    out = []
    for n, r in enumerate(rows, 1):
        oi = {strike: (wq.get(str(i)) or {}).get("oi") or 0 for i, strike in ahead[r["Symbol"]].items()}
        wall = max(oi, key=oi.get) if oi and max(oi.values()) > 0 else None
        room = round(abs(wall - r["eq_ltp"]) / r["eq_ltp"] * 100, 2) if wall else None
        notes = list(r["problems"])
        if room is not None and room <= wall_near:
            notes.append(f"At wall {wall:g} ({room}%)")
        cost = r["premium"] * r["opt_lot"] if r["premium"] and r["opt_lot"] else None
        out.append({
            "Pick": n, "Symbol": r["Symbol"], "Scanner": r["Score"], "Signal": r["Signal"],
            "Timing": r["Timing"], "Missing": r["Missing"],
            "Buy": f"{r['strike']:g} {r['Side']}", "Premium": r["premium"], "Lot cost": cost,
            "IV %": r["iv"], "Opt lots": r["opt_lots"], "Spread %": r["spread"],
            "Tradable": "No" if r["problems"] else "Yes", "Alert": ", ".join(notes),
            "Wall": wall, "Room %": room, "Candle": r["Candle"],
        })
    return pd.DataFrame(out)


def atm_all(res, options, fixed_at_open=True):
    """ATM call and put for every scanned stock, from ONE quote call (no option-chain limit).
    fixed_at_open keeps the strike nearest the day's opening price, so each stock shows the
    same contract all day and its premium change stays comparable. Otherwise the strike
    follows the current price. No greeks here: the quote call does not return them."""
    by_symbol, lots = _strike_table(options)

    picks = []
    for r in res.to_dict("records"):
        order = ("eq_open", "eq_ltp") if fixed_at_open else ("eq_ltp", "eq_open")
        ref = next((r[k] for k in order if pd.notna(r.get(k)) and r[k] > 0), None)
        g = by_symbol.get(r["Symbol"])
        if ref is None or g is None:
            continue
        row = g.iloc[(g["strike"] - ref).abs().argmin()]
        picks.append((r, float(row["strike"]), int(row["CE"]), int(row["PE"])))
    if not picks:
        return pd.DataFrame()

    q = quotes({"NSE_FNO": [i for p in picks for i in p[2:]]}).get("NSE_FNO", {})
    rows = []
    for r, strike, ce_id, pe_id in picks:
        lot = lots.get(r["Symbol"])
        ce_ltp, ce_pct, ce_lots = _option_leg(q.get(str(ce_id)), lot)
        pe_ltp, pe_pct, pe_lots = _option_leg(q.get(str(pe_id)), lot)
        mine, other = (ce_pct, pe_pct) if r["Side"] == "CE" else (pe_pct, ce_pct)
        # The scanner's side is confirmed when its premium is up today and the other side's is down.
        confirms = "-" if mine is None or other is None else "Yes" if mine > 0 > other else "No"
        rows.append({
            "Rank": r["Rank"], "Symbol": r["Symbol"], "Side": r["Side"], "Score": r["Score"],
            "Confirms": confirms, "Spot": r.get("eq_ltp"), "Strike": strike,
            "CE LTP": ce_ltp, "CE chg %": ce_pct, "CE lots": ce_lots,
            "PE LTP": pe_ltp, "PE chg %": pe_pct, "PE lots": pe_lots,
        })
    return pd.DataFrame(rows)


# Earlier scans kept in memory for the "previous rank" columns: (time, date, {symbol: ranks}).
# Shared by all browser sessions; empty again after the app restarts.
_snapshots = []
_MIN_AGE = 240   # compare with a scan at least 4 minutes old, so a quick manual
                 # refresh does not turn every move into zero
previous_scan_time = None  # IST time of the scan the moves were measured against


def _add_previous_ranks(df):
    global previous_scan_time
    now, today = time.time(), now_ist().date()
    _snapshots[:] = [s for s in _snapshots if s[1] == today]      # ranks do not carry overnight
    old = [s for s in _snapshots if now - s[0] >= _MIN_AGE]
    base = old[-1] if old else None
    previous_scan_time = base[3] if base else None

    prev, prev_side = [], []
    for sym, side in zip(df["Symbol"], df["Side"]):
        p = base[2].get(sym) if base else None
        prev.append(p[0] if p else pd.NA)
        # A stock that flipped between CE and PE has no earlier rank on its new side.
        prev_side.append(p[1] if p and p[2] == side else pd.NA)
    df.insert(1, "Prev rank", pd.array(prev, dtype="Int64"))
    df.insert(2, "Move", df["Prev rank"] - df["Rank"])            # +5 = climbed five places
    df["Prev side rank"] = pd.array(prev_side, dtype="Int64")
    df["Side move"] = df["Prev side rank"] - df["Side rank"]

    if not _snapshots or now - _snapshots[-1][0] >= _MIN_AGE:
        ranks = {s: (r, sr, sd) for s, r, sr, sd in
                 zip(df["Symbol"], df["Rank"], df["Side rank"], df["Side"])}
        _snapshots.append((now, today, ranks, now_ist().strftime("%H:%M")))
        del _snapshots[:-3]
    return df


def _oi_wall(oc, spot, side):
    """The strike with the most open interest in the trade's path: the heaviest call strike
    above spot for a CE (resistance), the heaviest put strike below spot for a PE (support).
    Room % is how far the stock is from it; Wall OI chg % is today's change in that OI."""
    key_side, best = side.lower(), None
    for k, legs in oc.items():
        strike, leg = float(k), legs.get(key_side) or {}
        ahead = strike > spot if side == "CE" else strike < spot
        if ahead and (leg.get("oi") or 0) > (best[1].get("oi") or 0 if best else 0):
            best = (strike, leg)
    if not best:
        return {"Wall": None, "Room %": None, "Wall OI chg %": None}
    strike, leg = best
    prev = leg.get("previous_oi")
    return {
        "Wall": f"{strike:g}",
        "Room %": round(abs(strike - spot) / spot * 100, 2),
        "Wall OI chg %": round((leg["oi"] / prev - 1) * 100, 1) if prev else None,
    }


def atm_option(eq_id, expiry, side, underlying_seg="NSE_EQ"):
    """ATM strike of the given side ('CE' or 'PE') with price, liquidity and greeks."""
    chain = option_chain(eq_id, underlying_seg, expiry)
    spot, oc = chain.get("last_price"), chain.get("oc") or {}
    if not spot or not oc:
        return None
    key = min(oc, key=lambda k: abs(float(k) - spot))
    o = oc[key].get(side.lower())
    if not o:
        return None
    g = o.get("greeks") or {}
    ltp, bid, ask = o.get("last_price") or 0, o.get("top_bid_price") or 0, o.get("top_ask_price") or 0
    gamma = g.get("gamma") or 0
    wall = _oi_wall(oc, spot, side)
    return {
        "Strike": f"{float(key):g} {side}", "Spot": spot, **wall, "Opt LTP": ltp,
        "IV %": round(o.get("implied_volatility") or 0, 1),
        "Delta": round(g.get("delta") or 0, 2),
        "Gamma": round(gamma, 5),
        # Change in delta for a 1% move in the stock: comparable across price levels.
        "Gamma/1%": round(gamma * spot * 0.01, 3),
        "Theta": round(g.get("theta") or 0, 2),
        "Opt OI": o.get("oi"),
        "Opt OI chg %": round(((o.get("oi") or 0) / o["previous_oi"] - 1) * 100, 1)
        if o.get("previous_oi") else None,
        "Opt Vol": o.get("volume"),
        "Spread %": round((ask - bid) / ltp * 100, 2) if ltp and bid and ask else None,
    }
