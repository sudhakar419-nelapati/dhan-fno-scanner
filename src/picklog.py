"""Scan log: every scan's picks are written to data/picks_log_<date>.csv, and later scans fill
in what happened to each one 15, 30 and 60 minutes afterwards. This is the evidence for
whether a ranking or a 'Go' is worth acting on.

Two outcomes are recorded per pick, both in the pick's favour (positive = it worked):
  Fut % +Nm    move of the future in the pick's direction (a fall counts as positive for a PE)
  Prem % +Nm   change in the premium of the option that was shown
The file lives in the app's own storage: on Streamlit Cloud it is lost on a restart, so
download it from the page at the end of the day.
"""
import threading
from datetime import datetime

import pandas as pd

from dhan_api import DATA, now_ist, quotes

HORIZONS = (15, 30, 60)      # minutes after the pick
WINDOW = 10                  # an outcome is filled by the first scan within this many minutes
BASE = ["Time", "Symbol", "Side", "Buy", "Scanner", "Chg %", "Vol x", "Signal", "Timing",
        "Streak", "Tradable", "Alert", "Fut LTP", "Premium", "sec_id"]
OUTCOMES = [f"{kind} % +{h}m" for h in HORIZONS for kind in ("Fut", "Prem")]
_lock = threading.Lock()


def log_path(day=None):
    return DATA / f"picks_log_{day or now_ist().date()}.csv"


def load(day=None):
    path = log_path(day)
    if not path.exists():
        return pd.DataFrame(columns=BASE + OUTCOMES)
    df = pd.read_csv(path)
    for c in BASE + OUTCOMES:
        if c not in df.columns:
            df[c] = pd.NA
    return df[BASE + OUTCOMES]


def _minutes_since(clock, now):
    t = datetime.strptime(clock, "%H:%M:%S")
    return (now.hour * 60 + now.minute + now.second / 60) - (t.hour * 60 + t.minute + t.second / 60)


def _fill_outcomes(log, res, now):
    """Fill the outcome cells that have come due, from the current scan and one quote call."""
    age = log["Time"].map(lambda c: _minutes_since(c, now))
    due = {h: log[f"Fut % +{h}m"].isna() & (age >= h) & (age < h + WINDOW) for h in HORIZONS}
    pending = pd.concat(due.values(), axis=1).any(axis=1)
    if not pending.any():
        return log
    fut_now = dict(zip(res["Symbol"], res["Fut LTP"]))
    ids = sorted(set(log.loc[pending, "sec_id"].dropna().astype(int)))
    q = quotes({"NSE_FNO": ids}).get("NSE_FNO", {}) if ids else {}
    for h, mask in due.items():
        for i in log.index[mask]:
            row = log.loc[i]
            fut, sign = fut_now.get(row["Symbol"]), 1 if row["Side"] == "CE" else -1
            if fut and row["Fut LTP"]:
                log.loc[i, f"Fut % +{h}m"] = round((fut / row["Fut LTP"] - 1) * 100 * sign, 2)
            prem = (q.get(str(int(row["sec_id"]))) or {}).get("last_price") if pd.notna(row["sec_id"]) else None
            if prem and pd.notna(row["Premium"]) and row["Premium"]:
                log.loc[i, f"Prem % +{h}m"] = round((prem / row["Premium"] - 1) * 100, 1)
    return log


def record(picks, res, scan_id, live=True):
    """Add this scan's picks (once per scan, market hours only) and fill outcomes now due."""
    with _lock:
        log, now = load(), now_ist()
        before = log.copy()
        if len(log):
            log = _fill_outcomes(log, res, now)
        if live and len(picks) and scan_id not in set(log["Time"]):
            new = picks.reindex(columns=[c for c in BASE if c != "Time"]).copy()
            new.insert(0, "Time", scan_id)
            new = new.reindex(columns=BASE + OUTCOMES)
            log = new if log.empty else pd.concat([log, new], ignore_index=True)
        if not log.equals(before):
            log.to_csv(log_path(), index=False)
        return log


def _group(row):
    timing = str(row["Timing"])
    if timing.endswith("Go"):
        return "Go, held 2+ scans" if row["Streak"] >= 2 else "Go, first scan"
    return "Almost" if timing.endswith("Almost") else "Wait"


def summary(log):
    """Average outcome by timing group. Rows repeat across scans, so treat it as a rough read."""
    if log.empty:
        return pd.DataFrame()
    df = log.assign(Group=log.apply(_group, axis=1))
    order = ["Go, held 2+ scans", "Go, first scan", "Almost", "Wait"]
    out = []
    for name in order:
        g = df[df["Group"] == name]
        if g.empty:
            continue
        row = {"Group": name, "Rows": len(g)}
        for h in HORIZONS:
            fut = pd.to_numeric(g[f"Fut % +{h}m"], errors="coerce").dropna()
            prem = pd.to_numeric(g[f"Prem % +{h}m"], errors="coerce").dropna()
            row[f"Fut % +{h}m"] = round(fut.mean(), 2) if len(fut) else None
            row[f"Prem % +{h}m"] = round(prem.mean(), 1) if len(prem) else None
            row[f"Won +{h}m %"] = round((prem > 0).mean() * 100) if len(prem) else None
        out.append(row)
    return pd.DataFrame(out)
