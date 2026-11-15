
import os
import time
from pathlib import Path

import requests
import pandas as pd
from dotenv import load_dotenv


# ==================================================
# 1. PROJECT CONFIGURATION
# ==================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

load_dotenv(PROJECT_ROOT / "config" / ".env")

CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN")

BASE_URL = "https://api.dhan.co/v2"

SELECTION_FILE = DATA_DIR / "hal_atm_selection.csv"
CANDLE_FILE = DATA_DIR / "hal_atm_candles.csv"
INDICATOR_FILE = DATA_DIR / "hal_atm_indicators.csv"

INTERVAL = "5"
TIMEZONE = "Asia/Kolkata"

LOOKBACK_DAYS = 10
VOLUME_WINDOW = 20

# Relative volume compares a candle with the SAME time slot on earlier days.
SLOT_SESSIONS = 5        # how many earlier sessions to average
SLOT_MIN_SESSIONS = 2    # fewer than this: fall back to the previous-20-candles method

# Normal NSE session
CANDLE_MIN_TIME = "09:15"
CANDLE_MAX_TIME = "15:25"

# Special Muhurat session
MUHURAT_DATE = "2026-11-08"
MUHURAT_START_TIME = "14:20"
MUHURAT_LAST_CANDLE = "15:35"


# ==================================================
# 2. DHAN API AUTHENTICATION
# ==================================================

def get_headers():

    if not CLIENT_ID or not ACCESS_TOKEN:
        raise ValueError(
            "Dhan credentials missing. Check config/.env"
        )

    return {
        "access-token": ACCESS_TOKEN,
        "client-id": CLIENT_ID,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }


# ==================================================
# 3. FETCH HISTORICAL OPTION CANDLES
# ==================================================

def fetch_option_candles(security_id, option_type):

    print("\n" + "=" * 60)
    print(f"FETCHING {option_type} 5-MINUTE CANDLES")
    print("Security ID:", security_id)
    print("=" * 60)

    now = pd.Timestamp.now(tz=TIMEZONE)

    start_date = now - pd.Timedelta(days=LOOKBACK_DAYS)

    payload = {
        "securityId": str(security_id),
        "exchangeSegment": "NSE_FNO",
        "instrument": "OPTSTK",
        "interval": INTERVAL,
        "oi": False,
        "fromDate": start_date.strftime("%Y-%m-%d %H:%M:%S"),
        "toDate": now.strftime("%Y-%m-%d %H:%M:%S")
    }

    url = f"{BASE_URL}/charts/intraday"

    response = requests.post(
        url,
        headers=get_headers(),
        json=payload,
        timeout=30
    )

    print("HTTP STATUS:", response.status_code)

    if not response.ok:
        print("DHAN ERROR:", response.text[:1000])
        response.raise_for_status()

    result = response.json()

    if not isinstance(result, dict):
        raise ValueError(
            "Unexpected response format from Dhan API."
        )

    if isinstance(result.get("data"), dict):
        result = result["data"]

    required_fields = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "timestamp"
    ]

    missing = [
        field for field in required_fields
        if field not in result
    ]

    if missing:
        raise ValueError(
            f"Missing candle fields in Dhan response: {missing}"
        )

    lengths = {
        len(result[field])
        for field in required_fields
    }

    if len(lengths) != 1:
        raise ValueError(
            "Dhan API returned unequal candle array lengths."
        )

    candles = pd.DataFrame({
        "timestamp": result["timestamp"],
        "open": result["open"],
        "high": result["high"],
        "low": result["low"],
        "close": result["close"],
        "volume": result["volume"]
    })

    if candles.empty:
        raise ValueError(
            f"No candles returned for security ID {security_id}"
        )

    received_count = len(candles)

    # --------------------------------------------------
    # Convert Unix timestamps to Indian Standard Time
    # --------------------------------------------------

    candles["timestamp"] = pd.to_datetime(
        candles["timestamp"],
        unit="s",
        utc=True,
        errors="coerce"
    ).dt.tz_convert(TIMEZONE)

    # --------------------------------------------------
    # Convert numeric columns
    # --------------------------------------------------

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]

    for column in numeric_columns:
        candles[column] = pd.to_numeric(
            candles[column],
            errors="coerce"
        )

    # Remove invalid rows

    candles = candles.dropna(
        subset=numeric_columns + ["timestamp"]
    ).copy()

    valid_count = len(candles)

    # --------------------------------------------------
    # Identify normal and special Muhurat sessions
    # --------------------------------------------------

    candle_date = candles["timestamp"].dt.strftime("%Y-%m-%d")
    candle_time = candles["timestamp"].dt.strftime("%H:%M")
    weekday = candles["timestamp"].dt.dayofweek

    # Normal weekday session

    normal_session = (
            (weekday < 5) &
            (candle_time >= CANDLE_MIN_TIME) &
            (candle_time <= CANDLE_MAX_TIME)
    )

    # Special Muhurat F&O session on Sunday, Nov 8, 2026

    muhurat_session = (
            (candle_date == MUHURAT_DATE) &
            (candle_time >= MUHURAT_START_TIME) &
            (candle_time <= MUHURAT_LAST_CANDLE)
    )

    # Keep either normal or special session candles

    valid_session = normal_session | muhurat_session

    candles = candles[valid_session].copy()

    session_count = len(candles)
    outside_session_removed = valid_count - session_count

    # --------------------------------------------------
    # Sort and remove duplicate timestamps
    # --------------------------------------------------

    candles = candles.sort_values(
        "timestamp"
    ).drop_duplicates(
        subset=["timestamp"],
        keep="last"
    ).reset_index(drop=True)

    duplicate_count = session_count - len(candles)

    # --------------------------------------------------
    # Remove unfinished 5-minute candles
    # Assumes timestamp represents candle start time
    # --------------------------------------------------

    current_time = pd.Timestamp.now(tz=TIMEZONE)

    completed_candle_mask = (
            candles["timestamp"] + pd.Timedelta(minutes=5)
            <= current_time
    )

    candles = candles[completed_candle_mask].copy()

    incomplete_removed = (
            session_count - duplicate_count - len(candles)
    )

    # --------------------------------------------------
    # Add contract information
    # --------------------------------------------------

    candles["option_type"] = str(option_type).upper().strip()
    candles["security_id"] = int(security_id)

    candles = candles.reset_index(drop=True)

    # --------------------------------------------------
    # Display data-cleaning summary
    # --------------------------------------------------

    print("\nDATA CLEANING SUMMARY")
    print("-" * 40)

    print("API records received       :", received_count)
    print("Invalid records removed    :", received_count - valid_count)
    print("Outside-session removed    :", outside_session_removed)
    print("Duplicate records removed  :", duplicate_count)
    print("Incomplete candles removed :", incomplete_removed)
    print("Final valid candles        :", len(candles))

    if not candles.empty:
        print("\nFirst timestamp:", candles["timestamp"].iloc[0])
        print("Last timestamp :", candles["timestamp"].iloc[-1])

    return candles


# ==================================================
# 4. CALCULATE EMA INDICATORS
# ==================================================

def calculate_ema(df):

    df = df.copy()

    df["EMA_9"] = df["close"].ewm(
        span=9,
        adjust=False
    ).mean()

    df["EMA_21"] = df["close"].ewm(
        span=21,
        adjust=False
    ).mean()

    df["EMA_50"] = df["close"].ewm(
        span=50,
        adjust=False
    ).mean()

    return df


# ==================================================
# 5. CALCULATE RSI 14
# ==================================================

def calculate_rsi(df, period=14):

    df = df.copy()

    delta = df["close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    rs = avg_gain / avg_loss

    df["RSI_14"] = 100 - (
            100 / (1 + rs)
    )

    # Continuous upward movement

    df.loc[
        (avg_loss == 0) & (avg_gain > 0),
        "RSI_14"
    ] = 100

    # Continuous downward movement

    df.loc[
        (avg_gain == 0) & (avg_loss > 0),
        "RSI_14"
    ] = 0

    # Completely flat prices

    df.loc[
        (avg_gain == 0) & (avg_loss == 0),
        "RSI_14"
    ] = 50

    return df


# ==================================================
# 6. CALCULATE INTRADAY VWAP
# ==================================================

def calculate_vwap(df):

    df = df.copy()

    typical_price = (
                            df["high"] +
                            df["low"] +
                            df["close"]
                    ) / 3

    df["_price_volume"] = (
            typical_price * df["volume"]
    )

    df["_trade_date"] = df["timestamp"].dt.date

    cumulative_pv = df.groupby(
        "_trade_date",
        sort=False
    )["_price_volume"].cumsum()

    cumulative_volume = df.groupby(
        "_trade_date",
        sort=False
    )["volume"].cumsum()

    df["VWAP"] = (
            cumulative_pv /
            cumulative_volume.replace(0, float("nan"))
    )

    df.drop(
        columns=["_price_volume", "_trade_date"],
        inplace=True
    )

    return df


# ==================================================
# 7. CALCULATE RELATIVE VOLUME
# ==================================================

def calculate_relative_volume(df):

    df = df.copy()

    df["_trade_date"] = df["timestamp"].dt.date
    df["_slot"] = df["timestamp"].dt.strftime("%H:%M")

    # Method 1 (preferred): average volume of the SAME 5-minute slot over the previous
    # sessions, for example today's 09:20 candle against the 09:20 candles of the last
    # five days. It has a value from the first candle of the day, and it allows for
    # volume being naturally heavier at the open and the close.
    # The Muhurat session is left out of the comparison: it is not a normal day.

    normal = df["timestamp"].dt.strftime("%Y-%m-%d") != MUHURAT_DATE

    slot_average = df[normal].groupby(
        "_slot",
        sort=False
    )["volume"].transform(
        lambda x: x.shift(1).rolling(
            window=SLOT_SESSIONS,
            min_periods=SLOT_MIN_SESSIONS
        ).mean()
    )

    # Method 2 (fallback): the previous 20 candles of the same session. Used when a
    # slot has too little history, such as the first days in the file.

    session_average = df.groupby(
        "_trade_date",
        sort=False
    )["volume"].transform(
        lambda x: x.shift(1).rolling(
            window=VOLUME_WINDOW,
            min_periods=VOLUME_WINDOW
        ).mean()
    )

    # Column name kept as AVG_VOLUME_20 so the other scripts keep working.

    df["AVG_VOLUME_20"] = slot_average.reindex(df.index).fillna(session_average)

    df["REL_VOLUME"] = (
            df["volume"] /
            df["AVG_VOLUME_20"].replace(
                0,
                float("nan")
            )
    )

    df.drop(
        columns=["_trade_date", "_slot"],
        inplace=True
    )

    return df


# ==================================================
# 8. CALCULATE PRICE MOMENTUM
# ==================================================

def calculate_momentum(df):

    df = df.copy()

    df["_trade_date"] = df["timestamp"].dt.date

    # Price changes must not compare different sessions.

    grouped_close = df.groupby(
        "_trade_date",
        sort=False
    )["close"]

    df["PRICE_CHANGE_%"] = (
            grouped_close.pct_change(
                fill_method=None
            ) * 100
    )

    df["CHANGE_3_CANDLES_%"] = (
            grouped_close.pct_change(
                periods=3,
                fill_method=None
            ) * 100
    )

    # Five independent momentum conditions

    df["EMA_BULLISH"] = (
            (df["EMA_9"] > df["EMA_21"]) &
            (df["EMA_21"] > df["EMA_50"])
    )

    df["ABOVE_VWAP"] = (
            df["close"] > df["VWAP"]
    )

    df["RSI_STRONG"] = (
            df["RSI_14"] >= 55
    )

    df["VOLUME_STRONG"] = (
            df["REL_VOLUME"] >= 1.5
    )

    df["PRICE_RISING"] = (
            df["PRICE_CHANGE_%"] > 0
    )

    # Calculate momentum score

    score = (
            df["EMA_BULLISH"].astype(int) +
            df["ABOVE_VWAP"].astype(int) +
            df["RSI_STRONG"].astype(int) +
            df["VOLUME_STRONG"].astype(int) +
            df["PRICE_RISING"].astype(int)
    )

    # Do not assign scores during indicator warm-up.

    required_columns = [
        "EMA_9",
        "EMA_21",
        "EMA_50",
        "RSI_14",
        "VWAP",
        "REL_VOLUME",
        "PRICE_CHANGE_%"
    ]

    ready = df[required_columns].notna().all(axis=1)

    df["MOMENTUM_SCORE"] = score.astype("Int64")

    df.loc[
        ~ready,
        "MOMENTUM_SCORE"
    ] = pd.NA

    # Momentum classification

    df["MOMENTUM_STATUS"] = "WARMUP"

    df.loc[
        ready,
        "MOMENTUM_STATUS"
    ] = "WEAK"

    df.loc[
        ready & (score >= 3),
        "MOMENTUM_STATUS"
    ] = "DEVELOPING"

    df.loc[
        ready & (score >= 4),
        "MOMENTUM_STATUS"
    ] = "STRONG"

    df.drop(
        columns=["_trade_date"],
        inplace=True
    )

    return df


# ==================================================
# 9. ANALYZE EACH OPTION
# ==================================================

def analyze_option(candles):

    if candles.empty:
        raise ValueError(
            "No candles available for analysis."
        )

    df = candles.copy()

    df["timestamp"] = pd.to_datetime(
        df["timestamp"]
    )

    df = df.sort_values(
        "timestamp"
    ).reset_index(drop=True)

    if df["timestamp"].duplicated().any():
        raise ValueError(
            "Duplicate candle timestamps found."
        )

    if len(df) < 50:
        raise ValueError(
            "Insufficient candles for EMA 50. "
            "Need at least 50 completed candles."
        )

    df = calculate_ema(df)
    df = calculate_rsi(df)
    df = calculate_vwap(df)
    df = calculate_relative_volume(df)
    df = calculate_momentum(df)

    return df


# ==================================================
# 10. DISPLAY LATEST MOMENTUM
# ==================================================

def safe_value(value, decimals=2):

    if pd.isna(value):
        return "N/A"

    return round(float(value), decimals)


def display_momentum(df, option_type):

    latest = df.iloc[-1]

    print("\n")
    print("=" * 60)
    print(f"HAL {option_type} MOMENTUM ANALYSIS")
    print("=" * 60)

    print("Timestamp       :", latest["timestamp"])
    print("Security ID     :", latest["security_id"])
    print("Close           :", safe_value(latest["close"]))
    print("EMA 9           :", safe_value(latest["EMA_9"]))
    print("EMA 21          :", safe_value(latest["EMA_21"]))
    print("EMA 50          :", safe_value(latest["EMA_50"]))
    print("VWAP            :", safe_value(latest["VWAP"]))
    print("RSI 14          :", safe_value(latest["RSI_14"]))
    print("Average Volume  :", safe_value(latest["AVG_VOLUME_20"]))
    print("Relative Volume :", safe_value(latest["REL_VOLUME"]))
    print("Price Change %  :", safe_value(latest["PRICE_CHANGE_%"]))
    print("3-Candle Change :", safe_value(latest["CHANGE_3_CANDLES_%"]))

    score = latest["MOMENTUM_SCORE"]

    if pd.isna(score):
        print("Momentum Score  : WARMUP")
    else:
        print("Momentum Score  :", int(score), "/ 5")

    print(
        "Momentum Status :",
        latest["MOMENTUM_STATUS"]
    )


# ==================================================
# 11. DATA QUALITY SUMMARY
# ==================================================

def display_data_quality(df):

    print("\n")
    print("=" * 60)
    print("INDICATOR DATA QUALITY REPORT")
    print("=" * 60)

    columns = [
        "RSI_14",
        "VWAP",
        "AVG_VOLUME_20",
        "REL_VOLUME",
        "PRICE_CHANGE_%",
        "CHANGE_3_CANDLES_%"
    ]

    for column in columns:

        missing = df[column].isna().sum()

        print(
            f"{column:<22}: {missing} missing"
        )

    print("\nMomentum classification:")

    print(
        df["MOMENTUM_STATUS"].value_counts(
            dropna=False
        ).to_string()
    )

    # --------------------------------------------------
    # Timestamp quality check
    # --------------------------------------------------

    print("\nTIMESTAMP QUALITY CHECK")
    print("-" * 40)

    candle_date = df["timestamp"].dt.strftime("%Y-%m-%d")
    candle_time = df["timestamp"].dt.strftime("%H:%M")
    weekday = df["timestamp"].dt.dayofweek

    normal_session = (
            (weekday < 5) &
            (candle_time >= CANDLE_MIN_TIME) &
            (candle_time <= CANDLE_MAX_TIME)
    )

    muhurat_session = (
            (candle_date == MUHURAT_DATE) &
            (candle_time >= MUHURAT_START_TIME) &
            (candle_time <= MUHURAT_LAST_CANDLE)
    )

    valid_session = normal_session | muhurat_session

    invalid_session_count = (~valid_session).sum()

    weekend_count = (
            (weekday >= 5) & (~muhurat_session)
    ).sum()

    muhurat_count = muhurat_session.sum()

    print("Invalid session records :", invalid_session_count)
    print("Invalid weekend records :", weekend_count)
    print("Muhurat session records :", muhurat_count)

    # --------------------------------------------------
    # Daily candle count
    # --------------------------------------------------

    print("\nDAILY CANDLE COUNT BY CONTRACT")

    daily_counts = (
        df.groupby(
            [
                "security_id",
                df["timestamp"].dt.date
            ]
        )
        .size()
        .rename("CANDLE_COUNT")
    )

    print(daily_counts.to_string())


# ==================================================
# 12. MAIN EXECUTION
# ==================================================

def main():

    print("\n")
    print("=" * 60)
    print("DHAN ATM OPTIONS 5-MINUTE CANDLE ANALYZER")
    print("=" * 60)

    if not SELECTION_FILE.exists():
        raise FileNotFoundError(
            "Run atm_selector.py first."
        )

    contracts = pd.read_csv(SELECTION_FILE)

    if contracts.empty:
        raise ValueError(
            "No selected contracts found."
        )

    required_columns = [
        "OPTION_TYPE",
        "SECURITY_ID",
        "DISPLAY_NAME"
    ]

    missing = [
        col for col in required_columns
        if col not in contracts.columns
    ]

    if missing:
        raise ValueError(
            f"Selection file missing columns: {missing}"
        )

    all_candles = []
    all_indicators = []

    for index, contract in contracts.iterrows():

        option_type = str(
            contract["OPTION_TYPE"]
        ).upper().strip()

        security_id = int(
            contract["SECURITY_ID"]
        )

        symbol = contract["DISPLAY_NAME"]

        print("\nProcessing:", symbol)

        # Fetch candles

        candles = fetch_option_candles(
            security_id,
            option_type
        )

        if candles.empty:
            print(
                f"WARNING: No valid candles for {symbol}. Skipping."
            )

            if index < len(contracts) - 1:
                time.sleep(1.5)

            continue

        # Analyze candles

        indicators = analyze_option(candles)

        # Display latest results

        display_momentum(
            indicators,
            option_type
        )

        all_candles.append(candles)
        all_indicators.append(indicators)

        # Pause between API requests

        if index < len(contracts) - 1:
            time.sleep(1.5)

    if not all_candles:
        raise ValueError(
            "No candle data was collected for any contract."
        )

    # Combine all contracts

    combined_candles = pd.concat(
        all_candles,
        ignore_index=True
    )

    combined_indicators = pd.concat(
        all_indicators,
        ignore_index=True
    )

    # Sort combined data

    combined_candles = combined_candles.sort_values(
        ["security_id", "timestamp"]
    ).reset_index(drop=True)

    combined_indicators = combined_indicators.sort_values(
        ["security_id", "timestamp"]
    ).reset_index(drop=True)

    # Save candle history

    combined_candles.to_csv(
        CANDLE_FILE,
        index=False
    )

    # Save calculated indicators

    combined_indicators.to_csv(
        INDICATOR_FILE,
        index=False
    )

    # Data quality report

    display_data_quality(combined_indicators)

    print("\n")
    print("=" * 60)
    print("CANDLE ANALYSIS COMPLETED SUCCESSFULLY")
    print("=" * 60)

    print("Candle file:", CANDLE_FILE)
    print("Indicator file:", INDICATOR_FILE)
    print("Total candles:", len(combined_candles))

    print(
        "Total contracts:",
        combined_indicators["security_id"].nunique()
    )


if __name__ == "__main__":
    main()
