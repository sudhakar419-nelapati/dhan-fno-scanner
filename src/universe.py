"""F&O stock universe (from Dhan's scrip master) and the daily volume / OI baseline."""
import threading
from datetime import date

import pandas as pd
import requests

from dhan_api import DATA, DhanError, daily, now_ist

MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
# Stops two browser sessions from building the same cache file at once.
_LOCK = threading.Lock()
NEEDED = ["EXCH_ID", "INSTRUMENT", "SECURITY_ID", "UNDERLYING_SECURITY_ID",
          "UNDERLYING_SYMBOL", "SERIES", "LOT_SIZE", "SM_EXPIRY_DATE",
          "STRIKE_PRICE", "OPTION_TYPE", "TICK_SIZE"]


def build_universe(m, today):
    """Pure transform: scrip master DataFrame -> one row per F&O stock."""
    missing = [c for c in NEEDED if c not in m.columns]
    if missing:
        raise DhanError(f"Scrip master is missing columns {missing}. Found: {list(m.columns)}")

    nse = m[m["EXCH_ID"] == "NSE"]
    fut = nse[nse["INSTRUMENT"] == "FUTSTK"].copy()
    fut["expiry"] = pd.to_datetime(fut["SM_EXPIRY_DATE"], errors="coerce").dt.date
    fut = fut[fut["expiry"] >= today]
    if fut.empty:
        raise DhanError("No live FUTSTK rows found in scrip master - check the file format.")
    # Keep only the current-month contract. The master also carries a few placeholder
    # rows with far-off expiry dates, and this drops them.
    fut = fut[fut["expiry"] == fut["expiry"].min()]
    fut = fut.drop_duplicates("UNDERLYING_SYMBOL")

    eq = nse[(nse["INSTRUMENT"] == "EQUITY") & (nse["SERIES"] == "EQ")]
    eq_map = dict(zip(eq["UNDERLYING_SYMBOL"], eq["SECURITY_ID"]))

    return pd.DataFrame({
        "symbol": fut["UNDERLYING_SYMBOL"].values,
        "eq_id": [int(eq_map.get(s, uid)) for s, uid in
                  zip(fut["UNDERLYING_SYMBOL"], fut["UNDERLYING_SECURITY_ID"])],
        "fut_id": fut["SECURITY_ID"].astype(int).values,
        "lot": fut["LOT_SIZE"].astype(int).values,
        "expiry": fut["expiry"].astype(str).values,
    }).sort_values("symbol").reset_index(drop=True)


def build_options(m, today):
    """Pure transform: scrip master -> every current-month stock option
    (symbol, strike, type, sec_id, lot)."""
    nse = m[(m["EXCH_ID"] == "NSE") & (m["INSTRUMENT"] == "OPTSTK")].copy()
    nse["expiry"] = pd.to_datetime(nse["SM_EXPIRY_DATE"], errors="coerce").dt.date
    nse = nse[(nse["expiry"] >= today) & nse["OPTION_TYPE"].isin(["CE", "PE"])]
    if nse.empty:
        raise DhanError("No live OPTSTK rows found in scrip master - check the file format.")
    nse = nse[nse["expiry"] == nse["expiry"].min()]      # current month only
    return pd.DataFrame({
        "symbol": nse["UNDERLYING_SYMBOL"].values,
        "strike": pd.to_numeric(nse["STRIKE_PRICE"], errors="coerce").values,
        "type": nse["OPTION_TYPE"].values,
        "sec_id": nse["SECURITY_ID"].astype(int).values,
        "lot": nse["LOT_SIZE"].astype(int).values,
        # Price step in rupees. The master gives it in paise: 5 for most stocks, 1 for cheap ones.
        "tick": (pd.to_numeric(nse["TICK_SIZE"], errors="coerce") / 100).fillna(0.05).values,
    }).dropna(subset=["strike"])


def _master_file():
    """Path of today's scrip master. It is the same file your instruments.py writes, and is
    downloaded again only when it is not from today."""
    raw = DATA / "dhan_instruments.csv"
    fresh = raw.exists() and date.fromtimestamp(raw.stat().st_mtime) == date.today()
    if not fresh:
        resp = requests.get(MASTER_URL, timeout=120)
        resp.raise_for_status()
        raw.write_bytes(resp.content)
    return raw


def load_option_master():
    """Every current-month stock option contract. Cached per day in data/."""
    with _LOCK:
        today = now_ist().date()
        cache = DATA / f"options_{today}.csv"
        if cache.exists():
            cached = pd.read_csv(cache)
            if "tick" in cached.columns:      # an older cache without tick sizes is rebuilt
                return cached
        o = build_options(pd.read_csv(_master_file(), low_memory=False), today)
        o.to_csv(cache, index=False)
        return o


def load_universe():
    """One row per F&O stock: symbol, eq_id, fut_id, lot, expiry. Cached per day in data/."""
    with _LOCK:
        today = now_ist().date()
        cache = DATA / f"universe_{today}.csv"
        if cache.exists():
            return pd.read_csv(cache)

        u = build_universe(pd.read_csv(_master_file(), low_memory=False), today)
        u.to_csv(cache, index=False)
        return u


def load_baseline(universe, progress=None):
    """Per stock: 10-day average cash volume and the last two daily futures OI values.
    About 2 calls per stock (roughly 2 minutes), run once a day and cached in data/."""
    with _LOCK:
        cache = DATA / f"baseline_{now_ist().date()}.csv"
        if cache.exists():
            return pd.read_csv(cache)

        rows, n = [], len(universe)
        for i, r in enumerate(universe.itertuples(), 1):
            row = {"symbol": r.symbol, "vol_avg10": None, "oi_last": None, "oi_prev": None}
            try:
                vol = daily(r.eq_id, "NSE_EQ", "EQUITY").get("volume") or []
                if vol:
                    row["vol_avg10"] = sum(vol[-10:]) / len(vol[-10:])
            except DhanError:
                pass
            try:
                oi = daily(r.fut_id, "NSE_FNO", "FUTSTK", oi=True).get("open_interest") or []
                if oi:
                    row["oi_last"] = oi[-1]
                    row["oi_prev"] = oi[-2] if len(oi) > 1 else None
            except DhanError:
                pass
            rows.append(row)
            if progress:
                progress(i / n, r.symbol)

        b = pd.DataFrame(rows)
        if b["vol_avg10"].notna().any() or b["oi_last"].notna().any():
            b.to_csv(cache, index=False)  # do not cache a fully failed run
        return b
