"""Stage 2, timing: is the move alive right now?

Four independent checks on the FUTURE's own 5-minute candles. Futures are liquid and have
no time decay or strike changes, so their candles are a cleaner read than an option's.
For a CE pick the checks look for a rising future, for a PE pick a falling one.
"""
import pandas as pd

from dhan_api import intraday, now_ist

MIN_CANDLES = 30
RSI_UP, RSI_DOWN = 55, 45
MIN_REL_VOLUME = 1.2           # average of the last three candles against the same time on earlier days
SLOT_SESSIONS, SLOT_MIN_SESSIONS, SESSION_WINDOW = 5, 2, 20
LABELS = {"trend": "trend", "vwap": "VWAP", "volume": "volume", "rsi": "RSI"}


def candles(fut_id, days=10):
    """5-minute candles of one stock future, IST timestamps (may be empty)."""
    raw = intraday(fut_id, days, instrument="FUTSTK")
    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    if not all(c in raw for c in cols) or not raw["timestamp"]:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame({c: raw[c] for c in cols})
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    return df


def indicators(df):
    """Clean the candles and add EMA 9/21, RSI 14, session VWAP and relative volume."""
    df = df.dropna().sort_values("timestamp").drop_duplicates("timestamp").copy()
    clock = df["timestamp"].dt.strftime("%H:%M")
    df = df[(clock >= "09:15") & (clock <= "15:25")]
    # Drop the candle still forming (timestamps mark the start of each 5 minutes).
    df = df[df["timestamp"] + pd.Timedelta(minutes=5) <= pd.Timestamp(now_ist())].reset_index(drop=True)
    if df.empty:
        return df
    close, day = df["close"], df["timestamp"].dt.date
    clock = df["timestamp"].dt.strftime("%H:%M")
    df["ema9"] = close.ewm(span=9, adjust=False).mean()
    df["ema21"] = close.ewm(span=21, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    df["rsi"] = 100 - 100 / (1 + gain / loss)
    df.loc[(loss == 0) & (gain > 0), "rsi"] = 100
    df.loc[(gain == 0) & (loss > 0), "rsi"] = 0
    df.loc[(gain == 0) & (loss == 0), "rsi"] = 50

    typical = (df["high"] + df["low"] + close) / 3
    vol_sum = df["volume"].groupby(day).cumsum()
    df["vwap"] = (typical * df["volume"]).groupby(day).cumsum() / vol_sum.where(vol_sum > 0)

    # Relative volume: each candle against the same time slot on earlier weekdays, so it has
    # a value from the first candle of the day. Falls back to the session's previous 20 candles.
    weekday = df["timestamp"].dt.dayofweek < 5
    slot_avg = df[weekday].groupby(clock[weekday], sort=False)["volume"].transform(
        lambda x: x.shift(1).rolling(SLOT_SESSIONS, min_periods=SLOT_MIN_SESSIONS).mean())
    session_avg = df.groupby(day, sort=False)["volume"].transform(
        lambda x: x.shift(1).rolling(SESSION_WINDOW, min_periods=SESSION_WINDOW).mean())
    avg = slot_avg.reindex(df.index).fillna(session_avg)
    df["rel_volume"] = df["volume"] / avg.where(avg > 0)
    return df


def check(df, side):
    """Run the four checks on the latest candle for a 'CE' (rising) or 'PE' (falling) pick.
    Returns {"passed": 0-4 or None, "Timing": text, "Missing": failed checks, "Candle": time}."""
    blank = {"passed": None, "Timing": "No data", "Missing": "", "Candle": None}
    if df is None or len(df) < MIN_CANDLES:
        return blank
    d = indicators(df)
    if len(d) < MIN_CANDLES:
        return blank
    last, up = d.iloc[-1], side == "CE"
    same_day = d[d["timestamp"].dt.date == last["timestamp"].date()]
    rel_vol = same_day["rel_volume"].tail(3).mean()
    ok = {
        "trend": last["ema9"] > last["ema21"] if up else last["ema9"] < last["ema21"],
        "vwap": last["close"] > last["vwap"] if up else last["close"] < last["vwap"],
        "volume": rel_vol >= MIN_REL_VOLUME,
        "rsi": last["rsi"] >= RSI_UP if up else last["rsi"] <= RSI_DOWN,
    }
    ok = {k: bool(v) for k, v in ok.items()}          # a comparison with a blank counts as failed
    n = sum(ok.values())
    word = "Go" if n == 4 else "Almost" if n == 3 else "Wait"
    return {
        "passed": n, "Timing": f"{n}/4 {word}",
        "Missing": ", ".join(LABELS[k] for k in LABELS if not ok[k]),
        "Candle": last["timestamp"].strftime("%d %b %H:%M"),
    }
