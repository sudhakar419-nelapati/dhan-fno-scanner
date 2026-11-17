"""Background logger: scans and logs on its own, with no browser page open.

One thread per app process. Every five minutes during market hours it runs the same three
stages as the page (scan, timing, tradability) with fixed settings, writes the picks to the
scan log and fills in the 15, 30 and 60-minute outcomes of earlier picks.

It needs today's Dhan token, which lives in the app's memory once it has been saved from the
sidebar (or comes from config/.env on a laptop). Without a valid token it waits. It stops
when the app process stops: on Streamlit Cloud that means a restart or the app going to sleep.
"""
import threading
import time

import pandas as pd

import dhan_api
import picklog
import scanner
import timing
from dhan_api import DhanError, now_ist
from universe import load_baseline, load_option_master, load_universe

# Fixed settings, so every day's log is built the same way whatever the sidebar shows.
SETTINGS = {"count": 20, "side": "All", "max_spread": 2.5, "wall_near": 1.0, "min_score": 35}
START_MIN, END_MIN = 9 * 60 + 20, 15 * 60 + 40    # first candle complete; outcomes after close
OFFSET_SEC = 25                                    # after each 5-minute mark, so the candle is in

status = {"started": None, "last": None, "picks": None, "scans": 0, "error": None}
_thread, _lock = None, threading.Lock()


def _timing(fut_id, side):
    try:
        return timing.check(timing.candles(fut_id), side)
    except DhanError:
        return {"passed": None, "tier": 0, "Timing": "No data", "Missing": "", "Candle": None,
                "candle_ts": None}


def run_once():
    """One scan-and-log pass. Returns the number of picks found."""
    uni = load_universe()
    res = scanner.scan(uni, load_baseline(uni))
    live = scanner.session_fraction() < 1.0
    stamp = now_ist().strftime("%H:%M:%S")
    # After the close nothing new is logged; the pass only fills in outcomes still due.
    picks = scanner.top_picks(res, load_option_master(), _timing, **SETTINGS) if live else pd.DataFrame()
    picklog.record(picks, res, stamp, live=live)
    return len(picks)


def _due(t):
    return t.weekday() < 5 and START_MIN <= t.hour * 60 + t.minute <= END_MIN


def _loop():
    while True:
        if _due(now_ist()) and dhan_api.current_token():
            try:
                n = run_once()
                status.update(last=now_ist().strftime("%H:%M:%S"), picks=n,
                              scans=status["scans"] + 1, error=None)
            except Exception as e:  # noqa: BLE001  the thread must survive any failure
                status.update(error=f"{now_ist():%H:%M:%S} {type(e).__name__}: {e}"[:200])
        t = now_ist()
        time.sleep(300 - (t.minute % 5 * 60 + t.second) + OFFSET_SEC)


def start():
    """Start the thread once per process. Safe to call on every page run."""
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, name="scan-logger", daemon=True)
            _thread.start()
            status["started"] = now_ist().strftime("%H:%M:%S")
    return status
