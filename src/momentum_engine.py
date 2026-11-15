import re
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
INDICATOR_FILE = DATA_DIR / "hal_atm_indicators.csv"
SELECTION_FILE = DATA_DIR / "hal_atm_selection.csv"
OUTPUT_FILE = DATA_DIR / "hal_momentum_ranking.csv"

TIMEZONE = "Asia/Kolkata"
MAX_DATA_AGE_MINUTES = 20
MIN_CANDLES = 50
RECENT_CANDLES_WINDOW = 10
MIN_ACTIVE_CANDLES_LAST_10 = 5
LIQUIDITY_WINDOW = 20
MAX_ZERO_VOLUME_RATIO_20 = 0.80
MIN_SESSION_COMPLETENESS = 0.50
MAX_PARTIAL_SESSIONS_LAST_5 = 1
MUHURAT_DATE = "2026-11-08"
MARKET_OPEN = "09:15"
MARKET_CLOSE = "15:30"
HOLIDAY_FILE = DATA_DIR / "nse_holidays.csv"
STRONG_SCORE = 5
DEVELOPING_SCORE = 4
STRICT_ENTRY_SCORE = 7
# One threshold for both sides: a call and a put are both bought, so each needs a
# RISING premium. (A falling put premium means the stock is going up.)
RSI_BUY_MIN = 55
MIN_RELATIVE_VOLUME = 1.5


def load_files():
    print("\n" + "=" * 70)
    print("DHAN ATM OPTIONS MOMENTUM ENGINE")
    print("=" * 70)
    if not INDICATOR_FILE.exists():
        raise FileNotFoundError(f"Indicator file not found: {INDICATOR_FILE}")
    if not SELECTION_FILE.exists():
        raise FileNotFoundError(f"ATM selection file not found: {SELECTION_FILE}")

    indicators = pd.read_csv(INDICATOR_FILE)
    selection = pd.read_csv(SELECTION_FILE)
    indicators.columns = [str(c).strip().upper() for c in indicators.columns]
    selection.columns = [str(c).strip().upper() for c in selection.columns]

    required_indicators = [
        "TIMESTAMP", "SECURITY_ID", "OPTION_TYPE", "OPEN", "CLOSE",
        "EMA_9", "EMA_21", "EMA_50", "RSI_14", "VWAP",
        "REL_VOLUME", "VOLUME"
    ]
    missing = [c for c in required_indicators if c not in indicators.columns]
    if missing:
        raise ValueError(
            f"Missing indicator columns: {missing}. "
            "The indicator CSV must include VOLUME for liquidity checks."
        )
    required_selection = ["SECURITY_ID", "OPTION_TYPE"]
    missing = [c for c in required_selection if c not in selection.columns]
    if missing:
        raise ValueError(f"Missing selection columns: {missing}")

    print("\nIndicator records:", len(indicators))
    print("Selected contracts:", len(selection))
    return indicators, selection


def prepare_selection(selection):
    selection = selection.copy()
    selection["SECURITY_ID"] = pd.to_numeric(
        selection["SECURITY_ID"], errors="coerce"
    )
    selection["OPTION_TYPE"] = (
        selection["OPTION_TYPE"].astype(str).str.upper().str.strip()
    )

    if "DISPLAY_NAME" not in selection.columns:
        selection["DISPLAY_NAME"] = (
                selection["OPTION_TYPE"] + " " +
                selection["SECURITY_ID"].astype(str)
        )

    if "STRIKE_PRICE" in selection.columns:
        selection["STRIKE_PRICE"] = pd.to_numeric(
            selection["STRIKE_PRICE"], errors="coerce"
        )
    else:
        def extract_strike(name):
            numbers = re.findall(r"\d+(?:\.\d+)?", str(name))
            return float(numbers[-1]) if numbers else None
        selection["STRIKE_PRICE"] = selection["DISPLAY_NAME"].apply(extract_strike)

    selection = selection.dropna(subset=["SECURITY_ID"]).copy()
    selection["SECURITY_ID"] = selection["SECURITY_ID"].astype(int)

    if "MONEYNESS" in selection.columns:
        selection["MONEYNESS"] = (
            selection["MONEYNESS"].astype(str).str.upper().str.strip()
        )
    elif "OPTION_ROLE" in selection.columns:
        selection["MONEYNESS"] = (
            selection["OPTION_ROLE"].astype(str).str.upper().str.strip()
        )
    else:
        if "UNDERLYING_LTP" in selection.columns:
            underlying = pd.to_numeric(
                selection["UNDERLYING_LTP"], errors="coerce"
            ).dropna()
            underlying_price = underlying.iloc[0] if len(underlying) else None
        else:
            underlying_price = None

        strikes = selection["STRIKE_PRICE"].dropna().unique()
        if underlying_price is not None and len(strikes):
            atm_strike = min(strikes, key=lambda x: abs(x - underlying_price))
        elif len(strikes):
            atm_strike = sorted(strikes)[len(strikes) // 2]
        else:
            raise ValueError(
                "Cannot identify ATM strike. Add STRIKE_PRICE or "
                "UNDERLYING_LTP to selection CSV."
            )

        def classify(row):
            strike = row["STRIKE_PRICE"]
            option_type = row["OPTION_TYPE"]
            if pd.isna(strike):
                return "UNKNOWN"
            if strike == atm_strike:
                return "ATM"
            if option_type in ["CE", "CALL"]:
                return "ITM" if strike < atm_strike else "OTM"
            if option_type in ["PE", "PUT"]:
                return "ITM" if strike > atm_strike else "OTM"
            return "UNKNOWN"

        selection["MONEYNESS"] = selection.apply(classify, axis=1)
    return selection


def prepare_indicators(indicators):
    df = indicators.copy()
    df["SECURITY_ID"] = pd.to_numeric(df["SECURITY_ID"], errors="coerce")
    df["TIMESTAMP"] = pd.to_datetime(df["TIMESTAMP"], errors="coerce")

    if df["TIMESTAMP"].dt.tz is None:
        df["TIMESTAMP"] = df["TIMESTAMP"].dt.tz_localize(TIMEZONE)
    else:
        df["TIMESTAMP"] = df["TIMESTAMP"].dt.tz_convert(TIMEZONE)

    numeric_columns = [
        "OPEN", "CLOSE", "EMA_9", "EMA_21", "EMA_50",
        "RSI_14", "VWAP", "REL_VOLUME", "VOLUME"
    ]
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = df.dropna(subset=["TIMESTAMP", "SECURITY_ID", "CLOSE"]).copy()
    df["SECURITY_ID"] = df["SECURITY_ID"].astype(int)
    df = df.sort_values(["SECURITY_ID", "TIMESTAMP"])

    candle_time = df["TIMESTAMP"].dt.strftime("%H:%M")
    df = df[(candle_time >= "09:15") & (candle_time <= "15:25")].copy()
    df = df.drop_duplicates(
        subset=["SECURITY_ID", "TIMESTAMP"], keep="last"
    )
    df["SESSION_DATE"] = df["TIMESTAMP"].dt.date
    return df


def calculate_contract_momentum(contract_df):
    """Score how strongly this option's premium is rising.

    Calls and puts use the same checks. An option buyer profits only when the premium
    goes up, so a put is scored on a rising put premium (which happens when the stock
    falls), not on a falling one."""
    df = contract_df.copy().sort_values("TIMESTAMP").reset_index(drop=True)
    if len(df) < MIN_CANDLES:
        raise ValueError(
            f"Only {len(df)} candles available. Need at least {MIN_CANDLES}."
        )

    option_type = str(df["OPTION_TYPE"].iloc[-1]).upper()
    is_call = option_type in ["CE", "CALL"]
    is_put = option_type in ["PE", "PUT"]
    if not (is_call or is_put):
        raise ValueError(f"Unsupported OPTION_TYPE: {option_type}")

    grouped_close = df.groupby("SESSION_DATE", sort=False)["CLOSE"]
    grouped_rsi = df.groupby("SESSION_DATE", sort=False)["RSI_14"]
    grouped_open = df.groupby("SESSION_DATE", sort=False)["OPEN"]

    df["CHANGE_3_CANDLES"] = grouped_close.transform(lambda x: x.pct_change(3) * 100)
    df["RSI_RISING"] = grouped_rsi.transform(lambda x: x > x.shift(1))
    df["PRICE_RISING"] = grouped_close.transform(lambda x: x > x.shift(1))
    df["RISING_CANDLES_3"] = df.groupby("SESSION_DATE", sort=False)["PRICE_RISING"].transform(
        lambda x: x.rolling(3, min_periods=3).sum()
    )
    df["BULLISH_CANDLE"] = df["CLOSE"] > df["OPEN"]
    df["BEARISH_CANDLE"] = df["CLOSE"] < df["OPEN"]

    # The seven checks, identical for calls and puts: is the PREMIUM trending up?
    df["TREND_ALIGNMENT_OK"] = (df["EMA_9"] > df["EMA_21"]) & (df["EMA_21"] > df["EMA_50"])
    df["PRICE_EMA_OK"] = df["CLOSE"] > df["EMA_9"]
    df["VWAP_OK"] = df["CLOSE"] > df["VWAP"]
    df["RSI_OK"] = (df["RSI_14"] >= RSI_BUY_MIN) & df["RSI_RISING"]
    df["VOLUME_CONFIRMATION"] = df["REL_VOLUME"] >= MIN_RELATIVE_VOLUME
    df["ACCELERATION_OK"] = df["CHANGE_3_CANDLES"] > 0
    df["THREE_CANDLE_OK"] = df["RISING_CANDLES_3"] >= 2
    df["CANDLE_DIRECTION_OK"] = df["BULLISH_CANDLE"]

    score_columns = [
        "TREND_ALIGNMENT_OK", "PRICE_EMA_OK", "VWAP_OK", "RSI_OK",
        "VOLUME_CONFIRMATION", "ACCELERATION_OK", "THREE_CANDLE_OK"
    ]
    # What a rising premium says about the stock: up for a call, down for a put.
    df["DIRECTION"] = "CALL_BUY_STOCK_UP" if is_call else "PUT_BUY_STOCK_DOWN"

    df["MOMENTUM_SCORE"] = df[score_columns].fillna(False).astype(int).sum(axis=1)
    return df


def expected_session_slots(session_date, latest_time=None):
    """
    Expected five-minute bars from session open through latest_time.
    The current data cleaner keeps regular-session candles through 15:25.
    Muhurat session is treated separately.
    """
    if str(session_date) == MUHURAT_DATE:
        start = pd.Timestamp(f"{MUHURAT_DATE} 14:20:00")
    else:
        start = pd.Timestamp(f"{session_date} {MARKET_OPEN}:00")

    if latest_time is None:
        end = pd.Timestamp(f"{session_date} {MARKET_CLOSE}:00")
    else:
        end = pd.Timestamp(latest_time).tz_localize(None) if pd.Timestamp(latest_time).tzinfo else pd.Timestamp(latest_time)

    if end < start:
        return 0
    # Number of five-minute candle timestamps from session start through end.
    return int((end - start).total_seconds() // 300) + 1


def assess_data_quality(analyzed):
    latest = analyzed.iloc[-1]
    recent = analyzed.tail(RECENT_CANDLES_WINDOW)
    recent_20 = analyzed.tail(LIQUIDITY_WINDOW)

    latest_volume = latest["VOLUME"]
    active_last_10 = int((recent["VOLUME"] > 0).sum())
    zero_ratio_20 = float((recent_20["VOLUME"].fillna(0) <= 0).mean())

    latest_volume_positive = pd.notna(latest_volume) and latest_volume > 0
    enough_recent_activity = active_last_10 >= MIN_ACTIVE_CANDLES_LAST_10
    zero_ratio_acceptable = zero_ratio_20 <= MAX_ZERO_VOLUME_RATIO_20

    # Assess coverage in the latest session, not just the last 20 candles.
    latest_date = latest["SESSION_DATE"]
    latest_session = analyzed[analyzed["SESSION_DATE"] == latest_date]
    expected_so_far = expected_session_slots(latest_date, latest["TIMESTAMP"])
    observed = int(latest_session["TIMESTAMP"].nunique())
    session_completeness = min(observed / expected_so_far, 1.0) if expected_so_far else 0.0
    session_complete_enough = session_completeness >= MIN_SESSION_COMPLETENESS

    # Review the five most recent historical sessions (excluding the current/latest session).
    session_dates = sorted(analyzed["SESSION_DATE"].dropna().unique())
    historical_dates = session_dates[-6:-1]
    partial_historical_sessions = 0
    for session_date in historical_dates:
        day = analyzed[analyzed["SESSION_DATE"] == session_date]
        expected = expected_session_slots(session_date)
        coverage = (day["TIMESTAMP"].nunique() / expected) if expected else 0.0
        if coverage < MIN_SESSION_COMPLETENESS:
            partial_historical_sessions += 1

    historical_quality_ok = (
            partial_historical_sessions <= MAX_PARTIAL_SESSIONS_LAST_5
    )

    reasons = []
    if not latest_volume_positive:
        reasons.append("LATEST_CANDLE_ZERO_OR_MISSING_VOLUME")
    if not enough_recent_activity:
        reasons.append("LOW_RECENT_ACTIVITY")
    if not zero_ratio_acceptable:
        reasons.append("HIGH_ZERO_VOLUME_RATIO")
    if not session_complete_enough:
        reasons.append("LATEST_SESSION_INCOMPLETE")
    if not historical_quality_ok:
        reasons.append("MULTIPLE_PARTIAL_HISTORICAL_SESSIONS")

    eligible = (
            latest_volume_positive
            and enough_recent_activity
            and zero_ratio_acceptable
            and session_complete_enough
            and historical_quality_ok
    )

    return {
        "LATEST_CANDLE_VOLUME": latest_volume,
        "ACTIVE_CANDLES_10": active_last_10,
        "ZERO_VOLUME_RATIO_20": zero_ratio_20,
        "LATEST_SESSION_CANDLES": observed,
        "EXPECTED_SESSION_CANDLES_SO_FAR": expected_so_far,
        "SESSION_COMPLETENESS": round(session_completeness, 3),
        "PARTIAL_SESSIONS_LAST_5": partial_historical_sessions,
        "LIQUIDITY_STATUS": "ACTIVE" if eligible else "LOW_LIQUIDITY",
        "DATA_QUALITY": "PASS" if eligible else "REVIEW",
        "MOMENTUM_ELIGIBLE": eligible,
        "QUALITY_REASONS": ";".join(reasons) if reasons else "NONE",
    }

def get_momentum_status(row):
    if not row["MOMENTUM_ELIGIBLE"]:
        return "DATA_REVIEW"
    score = int(row["MOMENTUM_SCORE"])
    if score >= STRONG_SCORE:
        return "STRONG"
    if score >= DEVELOPING_SCORE:
        return "DEVELOPING"
    return "WEAK"


def assess_strict_entry(row):
    """All seven directional momentum checks plus candle-body confirmation."""
    checks = {
        "EMA_TREND": bool(row["TREND_ALIGNMENT_OK"]),
        "PRICE_ON_TREND_SIDE_OF_EMA9": bool(row["PRICE_EMA_OK"]),
        "PRICE_ON_TREND_SIDE_OF_VWAP": bool(row["VWAP_OK"]),
        "RSI_DIRECTIONAL_CONFIRMATION": bool(row["RSI_OK"]),
        "RELATIVE_VOLUME_AT_LEAST_1_5": bool(row["VOLUME_CONFIRMATION"]),
        "THREE_CANDLE_DIRECTION_CONFIRMATION": bool(row["THREE_CANDLE_OK"]),
        "THREE_CANDLE_ACCELERATION": bool(row["ACCELERATION_OK"]),
        "LATEST_CANDLE_DIRECTION": bool(row["CANDLE_DIRECTION_OK"]),
    }
    failed = [name for name, passed in checks.items() if not passed]
    confirmed = (not failed) and int(row["MOMENTUM_SCORE"]) == STRICT_ENTRY_SCORE
    return confirmed, ("ALL_STRICT_CHECKS_PASSED" if confirmed else ";".join(failed))


def analyze_all_contracts(indicators, selection):
    results = []
    skipped = []

    for _, contract in selection.iterrows():
        security_id = int(contract["SECURITY_ID"])
        option_type = contract["OPTION_TYPE"]
        moneyness = contract["MONEYNESS"]
        display_name = contract["DISPLAY_NAME"]

        print(f"\nAnalyzing {display_name} | ID: {security_id}")
        option_data = indicators[
            indicators["SECURITY_ID"] == security_id
            ].copy()

        if option_data.empty:
            print("No candle data found.")
            skipped.append(display_name)
            continue

        try:
            analyzed = calculate_contract_momentum(option_data)
            latest = analyzed.iloc[-1]
            previous = analyzed.iloc[-2]
            quality = assess_data_quality(analyzed)

            result = {
                "DISPLAY_NAME": display_name,
                "SECURITY_ID": security_id,
                "OPTION_TYPE": option_type,
                "MONEYNESS": moneyness,
                "TIMESTAMP": latest["TIMESTAMP"],
                "CLOSE": latest["CLOSE"],
                "EMA_9": latest["EMA_9"],
                "EMA_21": latest["EMA_21"],
                "EMA_50": latest["EMA_50"],
                "RSI_14": latest["RSI_14"],
                "VWAP": latest["VWAP"],
                "REL_VOLUME": latest["REL_VOLUME"],
                "CHANGE_3_CANDLES": latest["CHANGE_3_CANDLES"],
                "RISING_CANDLES_3": latest["RISING_CANDLES_3"],
                "MOMENTUM_SCORE": latest["MOMENTUM_SCORE"],
                "PREVIOUS_SCORE": previous["MOMENTUM_SCORE"],
                "SCORE_IMPROVING": (
                        latest["MOMENTUM_SCORE"] > previous["MOMENTUM_SCORE"]
                ),
                "DIRECTION": latest["DIRECTION"],
                "TREND_ALIGNMENT_OK": bool(latest["TREND_ALIGNMENT_OK"]),
                "PRICE_EMA_OK": bool(latest["PRICE_EMA_OK"]),
                "VWAP_OK": bool(latest["VWAP_OK"]),
                "RSI_OK": bool(latest["RSI_OK"]),
                "VOLUME_CONFIRMATION": bool(latest["VOLUME_CONFIRMATION"]),
                "ACCELERATION_OK": bool(latest["ACCELERATION_OK"]),
                "THREE_CANDLE_OK": bool(latest["THREE_CANDLE_OK"]),
                "CANDLE_DIRECTION_OK": bool(latest["CANDLE_DIRECTION_OK"]),
                "BULLISH_CANDLE": bool(latest["BULLISH_CANDLE"]),
                "BEARISH_CANDLE": bool(latest["BEARISH_CANDLE"]),
                "STRONG_TREND_OK": bool(latest["TREND_ALIGNMENT_OK"] and latest["PRICE_EMA_OK"] and latest["VWAP_OK"]),
                **quality,
            }
            result["MOMENTUM_STATUS"] = get_momentum_status(result)
            result["ENTRY_CONFIRMED"], result["ENTRY_REASON"] = assess_strict_entry(result)
            results.append(result)

            print("Momentum score:", result["MOMENTUM_SCORE"], "/ 7")
            print("Liquidity:", result["LIQUIDITY_STATUS"])
            print("Data quality:", result["DATA_QUALITY"])
            print("Eligible:", result["MOMENTUM_ELIGIBLE"])
            print("Status:", result["MOMENTUM_STATUS"])
            print("Strict entry confirmed:", result["ENTRY_CONFIRMED"])
            print("Entry checks:", result["ENTRY_REASON"])

        except ValueError as error:
            print("Skipped:", error)
            skipped.append(display_name)

    return pd.DataFrame(results), skipped


def rank_contracts(results):
    if results.empty:
        return results

    results = results.copy()
    results["RANK"] = pd.NA
    eligible = results[results["MOMENTUM_ELIGIBLE"]].copy()

    if not eligible.empty:
        eligible["RANK"] = eligible.groupby("OPTION_TYPE")[
            "MOMENTUM_SCORE"
        ].rank(method="dense", ascending=False)
        eligible = eligible.sort_values(
            ["MOMENTUM_SCORE", "CHANGE_3_CANDLES", "REL_VOLUME"],
            ascending=False
        )
        results.loc[eligible.index, "RANK"] = eligible["RANK"]

    results["_ELIGIBLE_SORT"] = results["MOMENTUM_ELIGIBLE"].astype(int)
    results = results.sort_values(
        ["_ELIGIBLE_SORT", "MOMENTUM_SCORE", "CHANGE_3_CANDLES"],
        ascending=False
    ).drop(columns="_ELIGIBLE_SORT")
    return results


def compare_call_put(results, signal_mode='LIVE_MONITORING'):
    print("\n" + "=" * 75)
    print("CALL VS PUT MOMENTUM COMPARISON")
    print("=" * 75)

    if results.empty:
        print("No valid option data available.")
        return

    eligible = results[results["MOMENTUM_ELIGIBLE"]].copy()
    calls = eligible[eligible["OPTION_TYPE"].isin(["CE", "CALL"])]
    puts = eligible[eligible["OPTION_TYPE"].isin(["PE", "PUT"])]

    if calls.empty or puts.empty:
        print("Both CALL and PUT need at least one quality-approved contract.")
        return

    best_call = calls.sort_values("MOMENTUM_SCORE", ascending=False).iloc[0]
    best_put = puts.sort_values("MOMENTUM_SCORE", ascending=False).iloc[0]

    print("\nBEST CALL:", best_call["DISPLAY_NAME"])
    print("Score:", best_call["MOMENTUM_SCORE"], "/ 7")
    print("Status:", best_call["MOMENTUM_STATUS"])
    print("\nBEST PUT:", best_put["DISPLAY_NAME"])
    print("Score:", best_put["MOMENTUM_SCORE"], "/ 7")
    print("Status:", best_put["MOMENTUM_STATUS"])

    call_score = best_call["MOMENTUM_SCORE"]
    put_score = best_put["MOMENTUM_SCORE"]

    if signal_mode == "HISTORICAL":
        print("\nOVERALL INTERPRETATION:")
        print("HISTORICAL COMPARISON ONLY - MARKET IS CLOSED.")
        print("Use this ranking for review, not as a current trading signal.")
        return
    if signal_mode == "STALE_DATA":
        print("\nOVERALL INTERPRETATION:")
        print("STALE DATA - NO CURRENT DIRECTIONAL SIGNAL.")
        return

    print("\nOVERALL INTERPRETATION:")
    call_strong = best_call["MOMENTUM_STATUS"] == "STRONG"
    put_strong = best_put["MOMENTUM_STATUS"] == "STRONG"
    if call_strong and not put_strong:
        print("CALL PREMIUM SHOWING STRONGER MOMENTUM")
    elif put_strong and not call_strong:
        print("PUT PREMIUM SHOWING STRONGER MOMENTUM")
    elif call_strong and put_strong:
        print("BOTH SIDES STRONG - CHECK VOLATILITY AND UNDERLYING DIRECTION")
    elif (
            best_call["MOMENTUM_STATUS"] == "DEVELOPING"
            or best_put["MOMENTUM_STATUS"] == "DEVELOPING"
    ):
        print("DEVELOPING MOMENTUM - WAIT FOR CONFIRMATION")
    else:
        print("NO STRONG MOMENTUM - AVOID CHASING")


def get_market_context(results):
    """
    Classify whether the latest ranking can be treated as current.
    Closed-market rankings remain visible but are explicitly historical.
    """
    now = pd.Timestamp.now(tz=TIMEZONE)
    today = now.date()
    weekday = now.weekday()
    clock = now.strftime("%H:%M")

    latest_time = results["TIMESTAMP"].max() if not results.empty else pd.NaT
    age = (
        (now - latest_time).total_seconds() / 60
        if pd.notna(latest_time) else float("inf")
    )

    holidays = set()
    if HOLIDAY_FILE.exists():
        try:
            holiday_df = pd.read_csv(HOLIDAY_FILE, header=None)
            holidays = set(
                pd.to_datetime(holiday_df.iloc[:, 0], errors="coerce")
                .dropna().dt.date
            )
        except Exception as error:
            print("Holiday calendar could not be read:", error)

    if today in holidays:
        market_status = "MARKET_CLOSED_HOLIDAY"
        signal_mode = "HISTORICAL"
        reason = "NSE holiday"
    elif weekday >= 5:
        market_status = "MARKET_CLOSED_WEEKEND"
        signal_mode = "HISTORICAL"
        reason = "Weekend"
    elif clock < MARKET_OPEN:
        market_status = "PRE_MARKET"
        signal_mode = "HISTORICAL"
        reason = "Before regular market hours"
    elif clock >= MARKET_CLOSE:
        market_status = "MARKET_CLOSED"
        signal_mode = "HISTORICAL"
        reason = "Regular market session has ended"
    elif age > MAX_DATA_AGE_MINUTES:
        market_status = "MARKET_OPEN_DATA_STALE"
        signal_mode = "STALE_DATA"
        reason = f"Latest candle is older than {MAX_DATA_AGE_MINUTES} minutes"
    else:
        market_status = "MARKET_OPEN_DATA_FRESH"
        signal_mode = "LIVE_MONITORING"
        reason = "Market open and latest data within freshness limit"

    print("\n" + "=" * 75)
    print("MARKET SESSION AND DATA FRESHNESS")
    print("=" * 75)
    print("Current time:", now.strftime("%Y-%m-%d %H:%M:%S %Z"))
    if pd.notna(latest_time):
        print("Latest candle:", latest_time)
        print("Data age:", round(age, 2), "minutes")
    print("MARKET STATUS:", market_status)
    print("SIGNAL MODE:", signal_mode)
    print("Reason:", reason)

    if signal_mode == "HISTORICAL":
        print("NOTICE: Showing the last available ranking for reference only.")
        print("Do not treat it as a current or live trading signal.")
    elif signal_mode == "STALE_DATA":
        print("WARNING: Do not treat any contract as a current trading opportunity.")
    else:
        print("Live monitoring is permitted, subject to each contract's data-quality checks.")

    return {
        "MARKET_STATUS": market_status,
        "SIGNAL_MODE": signal_mode,
        "DATA_AGE_MINUTES": round(age, 2) if pd.notna(latest_time) else None,
        "MARKET_REASON": reason,
    }


def apply_market_gate(results, market_context):
    """A strict entry is current only when data is eligible and market gate is live."""
    if results.empty:
        return results

    gated = results.copy()
    gated["MARKET_STATUS"] = market_context["MARKET_STATUS"]
    gated["SIGNAL_MODE"] = market_context["SIGNAL_MODE"]
    gated["DATA_AGE_MINUTES"] = market_context["DATA_AGE_MINUTES"]

    def display_signal(row):
        if not row["MOMENTUM_ELIGIBLE"]:
            return "DATA_REVIEW"
        if market_context["SIGNAL_MODE"] == "STALE_DATA":
            return "STALE_DATA"
        if market_context["SIGNAL_MODE"] == "HISTORICAL":
            return "HISTORICAL"
        if row["ENTRY_CONFIRMED"]:
            return "CONFIRMED"
        if row["MOMENTUM_STATUS"] == "DEVELOPING":
            return "DEVELOPING"
        if row["MOMENTUM_STATUS"] == "STRONG":
            return "WATCH"
        return "WATCH"

    gated["DISPLAY_SIGNAL"] = gated.apply(display_signal, axis=1)
    gated["CURRENT_SIGNAL_ALLOWED"] = (
            (gated["SIGNAL_MODE"] == "LIVE_MONITORING")
            & gated["MOMENTUM_ELIGIBLE"]
            & gated["ENTRY_CONFIRMED"]
    )
    gated["FINAL_SIGNAL"] = gated["DISPLAY_SIGNAL"]
    return gated



def display_ranking(results):
    print("\n" + "=" * 115)
    print("ATM OPTIONS MOMENTUM RANKING")
    print("=" * 115)

    if results.empty:
        print("No contracts available for ranking.")
        return

    columns = [
        "DISPLAY_NAME", "MONEYNESS", "OPTION_TYPE", "CLOSE",
        "RSI_14", "REL_VOLUME", "ACTIVE_CANDLES_10",
        "ZERO_VOLUME_RATIO_20", "SESSION_COMPLETENESS",
        "PARTIAL_SESSIONS_LAST_5", "DATA_QUALITY",
        "MOMENTUM_ELIGIBLE", "MOMENTUM_SCORE", "RANK",
        "DIRECTION", "STRONG_TREND_OK", "MOMENTUM_STATUS",
        "TREND_ALIGNMENT_OK", "PRICE_EMA_OK", "VWAP_OK", "RSI_OK",
        "VOLUME_CONFIRMATION", "ACCELERATION_OK", "THREE_CANDLE_OK",
        "CANDLE_DIRECTION_OK", "ENTRY_CONFIRMED", "ENTRY_REASON",
        "MARKET_STATUS", "SIGNAL_MODE", "DISPLAY_SIGNAL",
        "CURRENT_SIGNAL_ALLOWED", "DATA_AGE_MINUTES"
    ]
    print(results[columns].to_string(index=False))


def main():
    indicators, selection = load_files()
    selection = prepare_selection(selection)
    indicators = prepare_indicators(indicators)

    print("\nCONTRACT DISTRIBUTION:")
    print(selection.groupby(["OPTION_TYPE", "MONEYNESS"]).size())
    if len(selection) < 6:
        print("\nWARNING: Fewer than six contracts selected.")

    results, skipped = analyze_all_contracts(indicators, selection)
    results = rank_contracts(results)

    market_context = get_market_context(results)
    results = apply_market_gate(results, market_context)

    display_ranking(results)
    compare_call_put(results, market_context['SIGNAL_MODE'])

    if not results.empty:
        results.to_csv(OUTPUT_FILE, index=False)
        print("\n" + "=" * 75)
        print("MOMENTUM ENGINE COMPLETED")
        print("=" * 75)
        print("Output:", OUTPUT_FILE)
        print("Contracts analyzed:", len(results))
        print("Eligible contracts:", int(results["MOMENTUM_ELIGIBLE"].sum()))

    if skipped:
        print("\nSKIPPED CONTRACTS:")
        for contract in skipped:
            print("-", contract)


if __name__ == "__main__":
    main()
