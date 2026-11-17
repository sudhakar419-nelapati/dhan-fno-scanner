"""F&O momentum dashboard for CE / PE option buying.

The page shows a short list of picks built in three stages: the futures scan chooses the
stocks, the future's 5-minute candles time the entry, and the ATM option is checked for
tradability. Everything else is optional
detail, switched on from the sidebar.

Local:  streamlit run src/dashboard.py   (credentials from config/.env, no password)
Cloud:  Streamlit Community Cloud, main file src/dashboard.py, with these Secrets:
            APP_PASSWORD   = "..."   required, the page is locked without it
            DHAN_CLIENT_ID = "..."
        The daily Dhan access token is pasted into the sidebar after signing in.
"""
import hashlib
import hmac
import sys
import time
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dhan_api  # noqa: E402
import picklog  # noqa: E402
import scanner  # noqa: E402
import timing  # noqa: E402
import worker  # noqa: E402
from dhan_api import ROOT, DhanError, now_ist, quotes  # noqa: E402
from scanner import atm_all, atm_option, scan, session_fraction, top_picks  # noqa: E402
from universe import load_baseline, load_option_master, load_universe  # noqa: E402

st.set_page_config(page_title="F&O Momentum - CE / PE", layout="wide")

IS_LOCAL = (ROOT / "config" / ".env").exists()


def secret(name):
    try:
        return st.secrets.get(name)
    except Exception:  # no secrets file at all (normal on a local run)
        return None


# ---------------------------------------------------------------- password gate
password = secret("APP_PASSWORD")
if not password and not IS_LOCAL:
    st.error("This app is locked: add APP_PASSWORD to the app's Secrets, then reboot it.")
    st.stop()
if password and not st.session_state.get("signed_in"):
    st.title("F&O Momentum")
    entered = st.text_input("Password", type="password")
    if entered:
        if hmac.compare_digest(entered.encode(), str(password).encode()):
            st.session_state["signed_in"] = True
            st.rerun()
        time.sleep(2)  # slows down guessing
        st.error("Wrong password.")
    st.stop()

# ---------------------------------------------------------------- Dhan credentials
dhan_api.set_credentials(client_id=secret("DHAN_CLIENT_ID"))
if not dhan_api.current_token() and secret("DHAN_ACCESS_TOKEN"):
    dhan_api.set_credentials(token=secret("DHAN_ACCESS_TOKEN"))


@st.cache_data(ttl=300, show_spinner=False)
def token_status(fingerprint):
    """(ok, message, expiry text). Keyed by a hash of the token, so a new token is checked at once."""
    try:
        validity = dhan_api.profile().get("tokenValidity")
        return True, f"Token valid till {validity or 'unknown'}", validity
    except DhanError as e:
        return False, str(e), None


RENEW_AFTER_HOUR = 14      # renew once a day after 14:00 IST, so the token outlasts next morning
RENEW_IF_UNDER_MIN = 60    # and at any time when less than an hour is left


def renew_due(validity):
    """True when the token should be renewed now. `validity` is Dhan's 'dd/mm/yyyy HH:MM' (IST)."""
    try:
        expiry = datetime.strptime(validity.strip(), "%d/%m/%Y %H:%M")
    except (AttributeError, ValueError):
        return False               # unknown format: leave the token alone
    now = now_ist().replace(tzinfo=None)
    if expiry <= now:
        return False               # already expired: only a new token from web.dhan.co helps
    if expiry - now < timedelta(minutes=RENEW_IF_UNDER_MIN):
        return True
    tomorrow_cutoff = datetime.combine(now.date() + timedelta(days=1), dtime(RENEW_AFTER_HOUR))
    return now.hour >= RENEW_AFTER_HOUR and expiry < tomorrow_cutoff


def auto_renew(validity):
    """Renew when due. A failed attempt is retried at most every 10 minutes."""
    since_fail, _, _ = dhan_api.renew_status()
    if not renew_due(validity) or (since_fail is not None and since_fail < 600):
        return
    try:
        dhan_api.renew_token(dhan_api.current_token())
    except DhanError:
        return                     # shown in the sidebar through renew_status()
    token_status.clear()
    st.rerun()


def fingerprint():
    return hashlib.sha256((dhan_api.current_token() or "").encode()).hexdigest()[:16]


st.title("F&O Momentum: Top Picks")

if st.checkbox("Full-width view (hide sidebar)", value=False):
    st.markdown("""<style>
        [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
        [data-testid="collapsedControl"] {display: none;}
        </style>""", unsafe_allow_html=True)

with st.sidebar:
    pick_count = st.slider("Top picks to show", 3, 20, 20)   # 20 = every option-scored stock
    pick_side = st.radio("Side", ("All", "CE", "PE"), horizontal=True)
    pool_min = st.slider("Minimum scanner score", 0, 100, 35, 5)
    max_spread = st.slider("Flag spread above (%)", 0.5, 5.0, 2.5, 0.5)
    wall_near = st.slider("Flag wall within (%)", 0.25, 3.0, 1.0, 0.25)
    auto = st.checkbox("Auto-refresh", value=True)
    every = st.number_input("Refresh every (sec)", 60, 900, 300, 30)
    detail = st.checkbox("Show detail tables", value=False)
    if detail:   # these only affect the detail tables
        top_n = st.slider("Stocks per side", 3, 10, 5)
        min_score = st.slider("Minimum score", 0, 100, 30)
        strike_mode = st.radio("ATM strike for the all-stocks table",
                               ("Fixed at day's open", "Follows current price"))
        debug = st.checkbox("Show API check", value=False)
    refresh = st.button("Refresh now")

    st.divider()
    token_ok, token_msg, token_validity = (
        token_status(fingerprint()) if dhan_api.current_token() else (False, "No token yet", None))
    if token_ok:
        auto_renew(token_validity)
    (st.success if token_ok else st.warning)(token_msg)
    _, renew_error, renewed_at = dhan_api.renew_status()
    if renew_error:
        st.warning(f"Auto-renew failed: {renew_error}")
    elif renewed_at:
        st.caption(f"Auto-renewed at {renewed_at} IST")
    with st.form("token_form", clear_on_submit=True):
        new_token = st.text_input("New Dhan access token", type="password")
        if st.form_submit_button("Save token") and new_token.strip():
            dhan_api.set_credentials(token=new_token)
            dhan_api.save_token_locally(new_token.strip())   # keeps config/.env in step
            st.cache_data.clear()
            st.rerun()

if not token_ok:
    st.info("Paste today's Dhan access token in the sidebar. Generate it at web.dhan.co "
            "under My Profile > Access DhanHQ APIs; it lasts 24 hours.")
    st.stop()


@st.cache_resource(show_spinner=False)
def _start_logger():
    """Starts the background logger once per app process (it then runs without any page open)."""
    return worker.start()


logger_status = _start_logger()


# ---------------------------------------------------------------- data (shared by all sessions)
@st.cache_data(show_spinner="Loading F&O universe...")
def _universe(day):
    return load_universe()


def _baseline(universe_df):
    # load_baseline caches to data/ itself, so this is slow only on the first run of the day.
    bar = st.progress(0.0, "Building daily volume / OI baseline (once a day, ~2 min)...")
    b = load_baseline(universe_df, lambda p, s: bar.progress(p, f"Baseline: {s}"))
    bar.empty()
    return b


@st.cache_data(ttl=55, show_spinner="Scanning futures quotes...")
def _scan(day, _uni, _base):
    result = scan(_uni, _base)
    return result, now_ist().strftime("%H:%M:%S"), scanner.previous_scan_time


@st.cache_data(show_spinner="Loading option contracts...")
def _options(day):
    return load_option_master()


@st.cache_data(ttl=55, show_spinner="Fetching ATM options for all stocks...")
def _atm_all(scan_time, fixed_at_open, _res, _opts):
    # scan_time is part of the cache key, so this follows every new scan.
    return atm_all(_res, _opts, fixed_at_open)


def show_all_options(df):
    """All-stocks ATM table: call columns tinted green, put columns tinted red."""
    if df.empty:
        st.info("No option quotes returned.")
        return

    def tint(row):
        return ["background-color: #e8f6e8; color: #1a1a1a" if c.startswith("CE") else
                "background-color: #fdeaea; color: #1a1a1a" if c.startswith("PE") else "" for c in row.index]

    formats = {c: "{:g}" for c in ("Strike", "Score", "Rank", "CE lots", "PE lots")}
    styled = df.style.format(formats, precision=2, na_rep="-").apply(tint, axis=1)
    st.dataframe(styled, hide_index=True, height=420)


@st.cache_data(ttl=90, show_spinner=False)
def _timing_cached(fut_id, side, slot):
    # `slot` is the current 5-minute candle, so a new candle always gets a fresh download.
    return timing.check(timing.candles(fut_id), side)


def _timing(fut_id, side):
    """Timing checks for one future. A failed download is not cached, so it is retried."""
    t = now_ist()
    try:
        return _timing_cached(fut_id, side, f"{t:%d %H}:{t.minute // 5 * 5:02d}")
    except DhanError:
        return {"passed": None, "tier": 0, "Timing": "No data", "Missing": "", "Candle": None,
                "candle_ts": None}


@st.cache_data(ttl=55, show_spinner=False)
def _picks(scan_time, count, side, spread_limit, wall_limit, score_floor, _res, _opts):
    # scan_time is part of the cache key, so the picks follow every new scan.
    picks = top_picks(_res, _opts, _timing, count=count, side=side, max_spread=spread_limit,
                      wall_near=wall_limit, min_score=score_floor, scan_id=scan_time)
    try:      # the log must never stop the page
        picklog.record(picks, _res, scan_time, live=session_fraction() < 1.0)
    except Exception as e:  # noqa: BLE001
        st.session_state["_log_error"] = str(e)[:200]
    return picks


def show_log():
    """Today's scan log: average outcome by timing group, and the file to download."""
    with st.expander("Scan log: how today's picks did"):
        if st.session_state.get("_log_error"):
            st.warning(f"Scan log error: {st.session_state['_log_error']}")
        if logger_status.get("error"):
            st.warning(f"Background logger error at {logger_status['error']}")
        elif logger_status.get("last"):
            st.caption(f"Background logger: running since {logger_status['started']}, last scan "
                       f"{logger_status['last']} ({logger_status['scans']} scans since the app started). "
                       "It scans every 5 minutes in market hours with no page open.")
        else:
            st.caption("Background logger: started, waiting for market hours and a valid token.")
        log = picklog.load()
        if log.empty:
            st.info("Nothing logged yet. Picks are logged on every scan during market hours.")
            return
        table = picklog.summary(log)
        if table.drop(columns=["Group", "Rows"]).notna().any().any():
            st.dataframe(table.style.format(precision=2, na_rep="-"), hide_index=True)
        else:
            st.info("Outcomes appear 15 minutes after the first logged scan.")
        st.caption("Fut % = move of the future in the pick's direction. Prem % = change in the "
                   "option's premium. Won = share of rows with a higher premium. Each is measured "
                   "15, 30 and 60 minutes after the scan. A stock is logged once per 5-minute "
                   "candle while it stays in the table, so rows overlap.")
        st.download_button(f"Download log ({len(log)} rows)", log.to_csv(index=False),
                           file_name=picklog.log_path().name, mime="text/csv")
        if not IS_LOCAL:
            st.caption("On the cloud this file is lost when the app restarts or sleeps: "
                       "download it before the end of the day.")


def show_picks(df):
    if df.empty:
        st.info("No picks: no stock is above the minimum scanner score right now.")
        return
    lean = df.drop(columns=["Wall", "Room %", "Side", "sec_id", "Fut LTP"])
    # The candle the timing checks used sits right after the Timing column.
    lean.insert(lean.columns.get_loc("Timing") + 1, "Candle",
                lean.pop("Candle").map(lambda v: "-" if pd.isna(v) else str(v)))
    lean["Since"] = lean["Since"].map(lambda v: "-" if pd.isna(v) else str(v))   # not "None"
    if lean["Spread %"].isna().all():     # market closed: no spread is measured, so hide the column
        lean = lean.drop(columns=["Spread %"])

    def mark(row):
        # Green needs a Go that has held for at least two candles, on a tradable option.
        go = (str(row["Timing"]).endswith("Go") and row["Tradable"] == "Yes"
              and row["Streak"] >= 2)
        return ["background-color: #e8f6e8; color: #1a1a1a" if go and c in ("Symbol", "Timing") else
                "background-color: #ffe9a8; color: #1a1a1a" if row["Alert"] and c == "Alert" else ""
                for c in row.index]

    formats = {"Lot cost": "{:,.0f}", "Scanner": "{:g}", "Opt lots": "{:,.0f}", "IV %": "{:.1f}",
               "Streak": "{:g}", "Chg %": "{:+.2f}", "Vol x": "{:.1f}"}
    st.dataframe(lean.style.format(formats, precision=2, na_rep="-").apply(mark, axis=1),
                 hide_index=True, height=35 * (len(lean) + 1) + 3)   # tall enough for every row
    if not df["Candle"].notna().any():
        st.caption("No futures candles were returned.")


@st.cache_data(ttl=110, show_spinner=False)
def _atm(eq_id, expiry, side):
    return atm_option(eq_id, expiry, side)


def with_options(df):
    """Add ATM option columns (gamma etc.) to a shortlist. One call per stock, 3 s apart."""
    out = []
    for r in df.to_dict("records"):
        row = {"Rank": r["Side rank"], "Prev": r["Prev side rank"], "Move": r["Side move"]}
        row.update({k: r[k] for k in ("Symbol", "Score", "Signal", "Chg %", "OI chg %", "Vol x")})
        try:
            row.update(_atm(r["eq_id"], r["expiry"], r["Side"]) or {"Strike": "no chain data"})
        except DhanError as e:
            row["Strike"] = f"chain error: {e}"[:80]
        row["Lot"] = r["lot"]
        out.append(row)
    df = pd.DataFrame(out)
    # Shown as text so an empty cell reads "-" (Streamlit prints "None" for empty numbers).
    if "Prev" in df.columns:
        df["Prev"] = df["Prev"].map(lambda v: "-" if pd.isna(v) else str(int(v)))
        df["Move"] = df["Move"].map(lambda v: "-" if pd.isna(v) else f"{int(v):+d}")   # +3 or -2
    return df


def alerts(df):
    """Text flags per row, so a problem is visible without relying on colour alone."""
    def one(row):
        notes = []
        if pd.notna(row.get("Room %")) and row["Room %"] <= wall_near:
            notes.append("At wall")
        if pd.notna(row.get("Spread %")) and row["Spread %"] > max_spread:
            notes.append("Wide spread")
        return ", ".join(notes)
    return df.apply(one, axis=1)


def show(title, df):
    st.subheader(title)
    if df.empty:
        st.info("No stocks above the minimum score.")
        return
    df = df.copy()
    df.insert(df.columns.get_loc("Symbol") + 1, "Alert", alerts(df))

    def wide_spread(v):
        return "background-color: #fde2e2; color: #1a1a1a" if pd.notna(v) and v > max_spread else ""

    def at_wall(row):
        near = pd.notna(row.get("Room %")) and row["Room %"] <= wall_near
        return ["background-color: #ffe9a8; color: #1a1a1a" if near and c in ("Symbol", "Alert", "Wall", "Room %")
                else "" for c in row.index]

    # Gamma values are small (around 0.0005 to 0.02), so they need more decimals than the rest.
    formats = {"Gamma": "{:.5f}", "Gamma/1%": "{:.3f}"}
    fine = {c: f for c, f in formats.items() if c in df.columns}
    styled = df.style.format(fine, precision=2, na_rep="-").apply(at_wall, axis=1)
    if "Spread %" in df.columns:
        styled = styled.map(wide_spread, subset=["Spread %"])
    st.dataframe(styled, hide_index=True)


def api_check(uni, base, res):
    """Diagnostics: shows whether each data source returned what the scan needs."""
    with st.expander("API check", expanded=True):
        n = len(res)
        oi_ok, vol_ok = int(res["OI chg %"].notna().sum()), int(res["Vol x"].notna().sum())
        st.write(f"Universe: {len(uni)} stocks, expiry {uni['expiry'].iloc[0]}")
        st.write(f"Quotes returned: {n} of {len(uni)}")
        st.write(f"OI change available: {oi_ok} of {n}")
        st.write(f"Volume ratio available: {vol_ok} of {n}")
        pick = uni[uni["symbol"] == "HAL"]
        pick = (pick if len(pick) else uni).iloc[0]
        st.write(f"Raw quote for {pick['symbol']} (future, then equity):")
        st.json(quotes({"NSE_FNO": [pick["fut_id"]], "NSE_EQ": [pick["eq_id"]]}))
        st.write("Baseline row:")
        st.dataframe(base[base["symbol"] == pick["symbol"]], hide_index=True)


if refresh:
    _scan.clear()
    _atm.clear()
    _atm_all.clear()
    _picks.clear()
    _timing_cached.clear()

try:
    day = str(now_ist().date())
    uni = _universe(day)
    base = _baseline(uni)
    res, scanned_at, compared_with = _scan(day, uni, base)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Stocks scanned", len(res))
    c2.metric("Long build-up", int((res["Signal"] == "Long build-up").sum()))
    c3.metric("Short build-up", int((res["Signal"] == "Short build-up").sum()))
    c4.metric("Scanned (IST)", scanned_at)
    if session_fraction() == 1.0:
        st.caption("Market closed: showing the last session's closing data.")

    opts = _options(day)
    with st.spinner("Checking the timing of the leading stocks..."):
        picks = _picks(scanned_at, pick_count, pick_side, max_spread, wall_near, pool_min,
                       res, opts)
    show_picks(picks)
    st.caption("Scanner: today's futures price, OI and volume (full marks at a 6% move, "
               "8% OI rise, 6x volume). Chg % = the future's move today; Vol x = cash volume "
               "against its 10-day normal for this time of day. "
               "Timing: four checks on the future's 5-minute candles (trend, VWAP, volume, "
               "RSI), plus a fifth before 09:45: a close beyond the first candle's range. "
               "Go = all pass, Almost = one fails; Missing lists what failed. "
               "Candle: the last completed 5-minute candle the checks used. "
               "Streak: 5-minute candles in a row on which the stock has held Go, and since when; "
               "green marks a Go held for two or more candles on a tradable option. "
               "Tradable: the at-the-money option has enough volume and a tight spread. "
               "Order: Go first, then Almost, then Wait, each by scanner score. "
               "IV is estimated from the premium.")

    show_log()

    if detail:
        st.caption(f"Prev and Move compare with the scan at {compared_with} IST." if compared_with
                   else "Prev and Move fill in once a scan at least 4 minutes old exists.")
        strong = res[res["Score"] >= min_score]
        ce = strong[strong["Side"] == "CE"].head(top_n)
        pe = strong[strong["Side"] == "PE"].head(top_n)
        with st.spinner(f"Fetching option chains for {len(ce) + len(pe)} stocks (3 s each)..."):
            ce_o, pe_o = with_options(ce), with_options(pe)

        show("CE candidates (bullish momentum)", ce_o)
        show("PE candidates (bearish momentum)", pe_o)

        with st.expander("All stocks: ATM options"):
            fixed = strike_mode.startswith("Fixed")
            st.caption(("Strike nearest the day's opening price, the same contract all day. "
                        if fixed else "Strike nearest the current price. ")
                       + "Confirms = Yes when the scanner's side is up today and the other side "
                         "is down. Lots = contracts traded.")
            show_all_options(_atm_all(scanned_at, fixed, res, opts))

        with st.expander("All F&O stocks"):
            hide = ["eq_id", "fut_id", "lot", "expiry", "eq_ltp", "eq_open",
                    "Prev side rank", "Side move"]
            st.dataframe(res.drop(columns=hide), hide_index=True)

        if debug:
            api_check(uni, base, res)
except DhanError as e:
    st.error(f"Dhan API error: {e}")
    st.caption("HTTP 401 or DH-901 usually means the access token has expired: "
               "paste a new one in the sidebar.")

if auto:
    # A browser-side timer triggers the refresh. The script itself never sleeps, so the page
    # stays responsive and Ctrl+C stops the server at once.
    st.session_state["_finished_at"] = time.time()
    # With the market closed the data does not change, so refresh only every 15 minutes.
    # That is still enough to keep the token auto-renew alive.
    interval = int(every) if session_fraction() < 1.0 else max(int(every), 900)

    @st.fragment(run_every=interval)
    def _auto_refresh():
        # This also runs once right after each full run; only a real timer tick may rerun.
        if time.time() - st.session_state["_finished_at"] >= interval - 2:
            st.rerun()

    _auto_refresh()
