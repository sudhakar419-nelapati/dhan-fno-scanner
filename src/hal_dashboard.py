"""HAL option engine viewer: a separate page for the six HAL contracts.

Run from src/ on its own port, so it can stay open next to the scanner:
    streamlit run hal_dashboard.py --server.port 8502

It uses your own scripts as they are: atm_selector.py, option_candles.py and
momentum_engine.py must be in the same folder.
"""
import contextlib
import io
import os
import sys
import time
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import momentum_engine as me  # noqa: E402
import option_candles as oc  # noqa: E402

ENV_FILE = HERE.parent / "config" / ".env"
CHECKS = {  # engine column -> short label, in the order the engine scores them
    "TREND_ALIGNMENT_OK": "EMA trend",
    "PRICE_EMA_OK": "Above EMA 9",
    "VWAP_OK": "Above VWAP",
    "RSI_OK": "RSI",
    "VOLUME_CONFIRMATION": "Volume",
    "ACCELERATION_OK": "3-candle gain",
    "THREE_CANDLE_OK": "2 of 3 rising",
    "CANDLE_DIRECTION_OK": "Last candle up",
}
# Chart series colours: fixed order, checked for colour-blind separation.
SERIES = {"Premium": "#2a78d6", "EMA 9": "#eb6834", "EMA 21": "#1baf7a", "VWAP": "#eda100"}


def quiet(fn, *args, **kwargs):
    """Run one of the engine's functions and return (result, everything it printed)."""
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        result = fn(*args, **kwargs)
    return result, log.getvalue()


def use_latest_token():
    """Re-read config/.env, so a token renewed by the scanner is picked up here too."""
    load_dotenv(ENV_FILE, override=True)
    oc.CLIENT_ID, oc.ACCESS_TOKEN = os.getenv("DHAN_CLIENT_ID"), os.getenv("DHAN_ACCESS_TOKEN")
    if "market_data" in sys.modules:
        md = sys.modules["market_data"]
        md.CLIENT_ID, md.ACCESS_TOKEN = oc.CLIENT_ID, oc.ACCESS_TOKEN


def repick_strikes():
    """Choose the six contracts again from HAL's current price (your atm_selector.py)."""
    use_latest_token()
    import atm_selector as sel
    use_latest_token()                       # atm_selector has just loaded market_data
    if not sel.INSTRUMENT_FILE.exists():
        import instruments
        quiet(instruments.main)              # downloads the contract list once

    def run():
        contracts = sel.load_hal_contracts()
        price = sel.get_hal_price()
        expiry = sel.get_nearest_expiry(contracts)
        month = contracts[contracts["SM_EXPIRY_DATE"] == expiry].copy()
        chosen = sel.build_six_contracts(month, sel.find_six_strikes(month, price), price)
        sel.save_selection(chosen)
        return price
    return quiet(run)


def fetch_candles():
    """Download fresh 5-minute candles for the selected contracts and recalculate the
    indicators (your option_candles.py). Returns (contracts done, skipped, log)."""
    use_latest_token()
    contracts = pd.read_csv(oc.SELECTION_FILE)
    candles, indicators, skipped, log = [], [], [], ""
    for i, c in enumerate(contracts.to_dict("records")):
        try:
            got, out = quiet(oc.fetch_option_candles, int(c["SECURITY_ID"]),
                             str(c["OPTION_TYPE"]).upper().strip())
            log += out
            ind, out = quiet(oc.analyze_option, got)
            candles.append(got)
            indicators.append(ind)
        except Exception as e:               # one bad contract must not stop the rest
            skipped.append(f"{c['DISPLAY_NAME']}: {e}")
        if i < len(contracts) - 1:
            time.sleep(1.5)
    if candles:
        by = ["security_id", "timestamp"]
        pd.concat(candles, ignore_index=True).sort_values(by).to_csv(oc.CANDLE_FILE, index=False)
        pd.concat(indicators, ignore_index=True).sort_values(by).to_csv(oc.INDICATOR_FILE, index=False)
    return len(candles), skipped, log


def score():
    """Score the saved indicator file with your momentum_engine.py.
    Returns (ranking, per-candle history, market context, skipped, log) or None if no files."""
    if not (me.INDICATOR_FILE.exists() and me.SELECTION_FILE.exists()):
        return None

    def run():
        ind, sel = pd.read_csv(me.INDICATOR_FILE), pd.read_csv(me.SELECTION_FILE)
        ind.columns = [str(c).strip().upper() for c in ind.columns]
        sel.columns = [str(c).strip().upper() for c in sel.columns]
        sel, ind = me.prepare_selection(sel), me.prepare_indicators(ind)
        results, skipped = me.analyze_all_contracts(ind, sel)
        results = me.rank_contracts(results)
        context = me.get_market_context(results)
        results = me.apply_market_gate(results, context)
        history = {}
        for sid, g in ind.groupby("SECURITY_ID"):
            try:
                history[int(sid)] = me.calculate_contract_momentum(g)
            except ValueError:
                pass
        return results, history, context, skipped
    (results, history, context, skipped), log = quiet(run)
    return results, history, context, skipped, log


def verdict(results, mode):
    """One-line CE-versus-PE reading, the same logic as the engine's compare_call_put."""
    if results.empty:
        return "No contracts could be scored."
    ok = results[results["MOMENTUM_ELIGIBLE"]]
    calls = ok[ok["OPTION_TYPE"].isin(["CE", "CALL"])].sort_values("MOMENTUM_SCORE", ascending=False)
    puts = ok[ok["OPTION_TYPE"].isin(["PE", "PUT"])].sort_values("MOMENTUM_SCORE", ascending=False)
    if calls.empty or puts.empty:
        return "Needs at least one quality-approved call and one put to compare."
    c, p = calls.iloc[0], puts.iloc[0]
    head = (f"Best call {c['DISPLAY_NAME']} scores {int(c['MOMENTUM_SCORE'])}/7; "
            f"best put {p['DISPLAY_NAME']} scores {int(p['MOMENTUM_SCORE'])}/7. ")
    if mode == "HISTORICAL":
        return head + "Market is closed: this is a review of the last session, not a live signal."
    if mode == "STALE_DATA":
        return head + "Data is stale: fetch fresh candles before reading it."
    cs, ps = c["MOMENTUM_STATUS"], p["MOMENTUM_STATUS"]
    if cs == "STRONG" and ps != "STRONG":
        return head + "Call premium has the stronger momentum."
    if ps == "STRONG" and cs != "STRONG":
        return head + "Put premium has the stronger momentum."
    if cs == "STRONG" and ps == "STRONG":
        return head + "Both premiums are rising: volatility is expanding, check the stock's direction."
    if "DEVELOPING" in (cs, ps):
        return head + "Momentum is developing: wait for confirmation."
    return head + "No strong momentum on either side."


def ranking_table(results):
    """The engine's ranking as a compact display table."""
    t = pd.DataFrame({
        "Contract": results["DISPLAY_NAME"],
        "Type": results["OPTION_TYPE"],
        "Money": results["MONEYNESS"],
        "Premium": results["CLOSE"],
        "Score": results["MOMENTUM_SCORE"].astype(int).astype(str) + "/7",
        "Status": results["MOMENTUM_STATUS"],
        "Signal": results["DISPLAY_SIGNAL"],
    })
    for col, label in CHECKS.items():
        t[label] = results[col].map({True: "Yes", False: "No"})
    t["RSI"] = results["RSI_14"].round(1)
    t["Rel vol"] = results["REL_VOLUME"].round(2)
    t["3-candle %"] = results["CHANGE_3_CANDLES"].round(2)
    t["Liquidity"] = results["LIQUIDITY_STATUS"]
    t["Data"] = results["DATA_QUALITY"]
    t["Notes"] = results["QUALITY_REASONS"].replace("NONE", "")
    return t.reset_index(drop=True)


def session_frame(hist, session_date):
    """One contract, one session, ready for the chart and the table view."""
    g = hist[hist["SESSION_DATE"] == session_date]
    return pd.DataFrame({
        "Time": g["TIMESTAMP"].dt.tz_convert(me.TIMEZONE).dt.tz_localize(None),
        "Premium": g["CLOSE"], "EMA 9": g["EMA_9"], "EMA 21": g["EMA_21"], "VWAP": g["VWAP"],
        "Score": g["MOMENTUM_SCORE"], "RSI": g["RSI_14"].round(1),
        "Rel vol": g["REL_VOLUME"].round(2), "Volume": g["VOLUME"],
    }).reset_index(drop=True)


def main():
    import streamlit as st

    st.set_page_config(page_title="HAL Option Momentum", layout="wide")
    st.title("HAL Option Momentum")
    st.caption("Six HAL contracts scored on their own 5-minute premium candles. "
               "A point is earned for each of seven checks that the premium is rising.")

    b1, b2, b3 = st.columns([1, 1, 3])
    do_fetch = b1.button("Fetch latest candles", type="primary")
    do_pick = b2.button("Re-pick ATM strikes")
    auto = b3.checkbox("Fetch automatically every 5 minutes", value=False)
    if st.session_state.pop("_timer_fetch", False):
        do_fetch = True

    if do_pick:
        try:
            with st.spinner("Choosing the six contracts from HAL's current price..."):
                price, _ = repick_strikes()
            st.success(f"Strikes re-picked around HAL at {price}. Now fetch the candles.")
        except Exception as e:
            st.error(f"Could not re-pick strikes: {e}")
    if do_fetch:
        if not oc.SELECTION_FILE.exists():
            st.error("No contracts selected yet: click 'Re-pick ATM strikes' first.")
        else:
            with st.spinner("Downloading candles for six contracts (about 10 seconds)..."):
                done, skipped, _ = fetch_candles()
            if done:
                st.success(f"Fetched {done} contracts at {pd.Timestamp.now(tz=me.TIMEZONE):%H:%M:%S} IST.")
            else:
                st.error("No candles fetched. A 401 error means the token in config/.env has expired.")
            for s in skipped:
                st.warning(f"Skipped {s}")

    scored = score()
    if scored is None:
        st.info("No data files yet. Click 'Re-pick ATM strikes', then 'Fetch latest candles'.")
        return
    results, history, context, skipped, log = scored
    if results.empty:
        st.warning("None of the selected contracts has enough candles to score.")
        return

    m1, m2, m3 = st.columns(3)
    m1.metric("Market", context["MARKET_STATUS"].replace("_", " ").title())
    m2.metric("Signal mode", context["SIGNAL_MODE"].replace("_", " ").title())
    age = context["DATA_AGE_MINUTES"]
    m3.metric("Latest candle age", "-" if age is None else
              f"{age:.0f} min" if age < 120 else f"{age / 60:.1f} hours")
    st.info(verdict(results, context["SIGNAL_MODE"]))

    st.subheader("Ranking")
    table = ranking_table(results)

    def tint(v):
        return ("background-color: #e8f6e8" if v == "Yes" else
                "background-color: #fdeaea" if v == "No" else "")
    st.dataframe(table.style.format(precision=2, na_rep="-").map(tint, subset=list(CHECKS.values())),
                 hide_index=True)
    st.caption("Status: Strong is 5 or more of 7, Developing is 4. Signal shows Confirmed only "
               "when all seven checks pass, the last candle is up, and the market is open with "
               "fresh data. Data = Review means the contract trades too thinly to trust.")
    for s in skipped:
        st.warning(f"Not scored: {s}")

    st.subheader("Premium chart")
    names = dict(zip(results["DISPLAY_NAME"], results["SECURITY_ID"]))
    c1, c2 = st.columns([2, 1])
    name = c1.selectbox("Contract", list(names))
    hist = history.get(int(names[name]))
    if hist is None:
        st.warning("No candle history for this contract.")
    else:
        dates = sorted(hist["SESSION_DATE"].unique(), reverse=True)
        day = c2.selectbox("Session", dates, format_func=lambda d: f"{d:%a %d %b %Y}")
        frame = session_frame(hist, day)
        st.line_chart(frame, x="Time", y=list(SERIES), color=list(SERIES.values()),
                      y_label="Premium (Rs)", x_label="Time (IST)")
        st.caption("Score per candle (0 to 7)")
        st.bar_chart(frame, x="Time", y="Score", color=SERIES["Premium"],
                     y_label="Score", x_label="Time (IST)", height=180)
        with st.expander("Candle data for this session"):
            st.dataframe(frame.style.format(precision=2, na_rep="-"), hide_index=True)

    with st.expander("Engine log"):
        st.code(log or "(nothing printed)")

    if auto:
        st.session_state["_finished_at"] = time.time()

        @st.fragment(run_every=300)
        def _tick():
            if time.time() - st.session_state["_finished_at"] >= 298:
                st.session_state["_timer_fetch"] = True
                st.rerun()
        _tick()


if __name__ == "__main__":
    main()
