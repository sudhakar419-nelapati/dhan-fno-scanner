
from pathlib import Path
from datetime import date

import pandas as pd

from market_data import get_ltp


# ==================================================
# 1. PROJECT CONFIGURATION
# ==================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"

INSTRUMENT_FILE = DATA_DIR / "hal_nse_options.csv"

OUTPUT_FILE = DATA_DIR / "hal_atm_selection.csv"

HAL_SECURITY_ID = 2303

TIMEZONE = "Asia/Kolkata"


# ==================================================
# 2. LOAD HAL OPTION CONTRACTS
# ==================================================

def load_hal_contracts():

    print("\nLoading HAL option contracts...")

    if not INSTRUMENT_FILE.exists():

        raise FileNotFoundError(
            f"Instrument file not found: {INSTRUMENT_FILE}"
        )

    df = pd.read_csv(INSTRUMENT_FILE)

    required_columns = [
        "SM_EXPIRY_DATE",
        "STRIKE_PRICE",
        "SECURITY_ID",
        "OPTION_TYPE",
        "DISPLAY_NAME"
    ]

    missing = [
        column for column in required_columns
        if column not in df.columns
    ]

    if missing:

        raise ValueError(
            f"Missing instrument columns: {missing}"
        )

    df["SM_EXPIRY_DATE"] = pd.to_datetime(
        df["SM_EXPIRY_DATE"],
        errors="coerce"
    ).dt.normalize()

    df["STRIKE_PRICE"] = pd.to_numeric(
        df["STRIKE_PRICE"],
        errors="coerce"
    )

    df["SECURITY_ID"] = pd.to_numeric(
        df["SECURITY_ID"],
        errors="coerce"
    )

    df["OPTION_TYPE"] = (
        df["OPTION_TYPE"]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    df = df.dropna(
        subset=[
            "SM_EXPIRY_DATE",
            "STRIKE_PRICE",
            "SECURITY_ID"
        ]
    )

    df = df[
        df["OPTION_TYPE"].isin(["CE", "PE"])
    ].copy()

    df["SECURITY_ID"] = (
        df["SECURITY_ID"].astype(int)
    )

    df = df.drop_duplicates(
        subset=[
            "SM_EXPIRY_DATE",
            "STRIKE_PRICE",
            "OPTION_TYPE"
        ]
    )

    print("HAL contracts loaded:", len(df))

    return df


# ==================================================
# 3. GET UNDERLYING HAL PRICE
# ==================================================

def get_hal_price():

    print("\nFetching HAL underlying price...")

    price = get_ltp(
        exchange_segment="NSE_EQ",
        security_id=HAL_SECURITY_ID
    )

    if price is None:

        raise ValueError(
            "Unable to retrieve HAL underlying price."
        )

    price = float(price)

    if price <= 0:

        raise ValueError(
            f"Invalid HAL underlying price: {price}"
        )

    print("HAL LTP:", price)

    return price


# ==================================================
# 4. FIND NEAREST VALID EXPIRY
# ==================================================

def get_nearest_expiry(df):

    today = pd.Timestamp.now(
        tz=TIMEZONE
    ).date()

    valid_expiries = sorted(
        expiry
        for expiry in df["SM_EXPIRY_DATE"].unique()
        if expiry.date() >= today
    )

    if not valid_expiries:

        raise ValueError(
            "No future HAL option expiry found."
        )

    expiry = valid_expiries[0]

    print("Selected expiry:", expiry.date())

    return expiry


# ==================================================
# 5. FIND COMMON CE AND PE STRIKES
# ==================================================

def get_common_strikes(expiry_df):

    ce_strikes = set(
        expiry_df.loc[
            expiry_df["OPTION_TYPE"] == "CE",
            "STRIKE_PRICE"
        ].unique()
    )

    pe_strikes = set(
        expiry_df.loc[
            expiry_df["OPTION_TYPE"] == "PE",
            "STRIKE_PRICE"
        ].unique()
    )

    common_strikes = sorted(
        ce_strikes.intersection(pe_strikes)
    )

    if len(common_strikes) < 3:

        raise ValueError(
            "At least three common CE/PE strikes "
            "are required for six-contract selection."
        )

    return common_strikes


# ==================================================
# 6. FIND ATM, ITM AND OTM STRIKES
# ==================================================

def find_six_strikes(expiry_df, underlying_price):

    common_strikes = get_common_strikes(
        expiry_df
    )

    # Find the nearest available ATM strike.
    atm_strike = min(
        common_strikes,
        key=lambda strike: abs(
            strike - underlying_price
        )
    )

    atm_index = common_strikes.index(
        atm_strike
    )

    # Find one strike below and one above ATM.
    if atm_index == 0:

        raise ValueError(
            "No lower strike available for ITM CALL "
            "and OTM PUT."
        )

    if atm_index == len(common_strikes) - 1:

        raise ValueError(
            "No higher strike available for OTM CALL "
            "and ITM PUT."
        )

    lower_strike = common_strikes[
        atm_index - 1
        ]

    higher_strike = common_strikes[
        atm_index + 1
        ]

    print("\nSTRIKE SELECTION")
    print("-" * 45)

    print("Underlying Price:", underlying_price)

    print("ITM/Lower Strike:", lower_strike)

    print("ATM Strike:", atm_strike)

    print("OTM/Higher Strike:", higher_strike)

    return {
        "ATM": atm_strike,
        "LOWER": lower_strike,
        "HIGHER": higher_strike
    }


# ==================================================
# 7. GET EXACT OPTION CONTRACT
# ==================================================

def get_contract(expiry_df, strike, option_type):

    contract = expiry_df[
        (expiry_df["STRIKE_PRICE"] == strike) &
        (expiry_df["OPTION_TYPE"] == option_type)
        ]

    if contract.empty:

        raise ValueError(
            f"Contract not found: {strike} {option_type}"
        )

    return contract.iloc[0]


# ==================================================
# 8. BUILD SIX OPTION CONTRACTS
# ==================================================

def build_six_contracts(
        expiry_df,
        strikes,
        underlying_price
):

    selected_contracts = []

    contract_definitions = [

        # CALL contracts
        ("CE", "ITM", strikes["LOWER"]),

        ("CE", "ATM", strikes["ATM"]),

        ("CE", "OTM", strikes["HIGHER"]),

        # PUT contracts
        ("PE", "ITM", strikes["HIGHER"]),

        ("PE", "ATM", strikes["ATM"]),

        ("PE", "OTM", strikes["LOWER"])
    ]

    for option_type, moneyness, strike in contract_definitions:

        contract = get_contract(
            expiry_df,
            strike,
            option_type
        )

        contract_data = contract.to_dict()

        contract_data["MONEYNESS"] = moneyness

        contract_data["UNDERLYING_LTP"] = underlying_price

        contract_data["SELECTED_STRIKE"] = strike

        selected_contracts.append(
            contract_data
        )

    return pd.DataFrame(
        selected_contracts
    )


# ==================================================
# 9. DISPLAY SIX SELECTED CONTRACTS
# ==================================================

def display_contracts(
        selected,
        underlying_price,
        expiry
):

    print("\n")
    print("=" * 75)
    print("HAL SIX CONTRACT ATM OPTIONS MONITOR")
    print("=" * 75)

    print("Underlying Price:", underlying_price)

    print("Expiry:", expiry.date())

    print("\n")

    display_columns = [
        "OPTION_TYPE",
        "MONEYNESS",
        "STRIKE_PRICE",
        "SECURITY_ID",
        "DISPLAY_NAME"
    ]

    print(
        selected[display_columns].to_string(
            index=False
        )
    )

    print("\n")
    print("TOTAL CONTRACTS:", len(selected))


# ==================================================
# 10. SAVE SIX CONTRACTS
# ==================================================

def save_selection(selected):

    selected.to_csv(
        OUTPUT_FILE,
        index=False
    )

    print("\nSelection saved:", OUTPUT_FILE)

    print(
        "Saved contracts:",
        len(selected)
    )


# ==================================================
# 11. MAIN EXECUTION
# ==================================================

def main():

    print("\n")
    print("=" * 75)
    print("DHAN SIX-CONTRACT OPTION SELECTOR")
    print("=" * 75)

    # Step 1: Load instruments
    df = load_hal_contracts()

    # Step 2: Get underlying HAL price
    underlying_price = get_hal_price()

    # Step 3: Find nearest expiry
    expiry = get_nearest_expiry(df)

    expiry_df = df[
        df["SM_EXPIRY_DATE"] == expiry
        ].copy()

    # Step 4: Find common strikes
    strikes = find_six_strikes(
        expiry_df,
        underlying_price
    )

    # Step 5: Select six contracts
    selected = build_six_contracts(
        expiry_df,
        strikes,
        underlying_price
    )

    # Step 6: Display selected contracts
    display_contracts(
        selected,
        underlying_price,
        expiry
    )

    # Step 7: Save selection
    save_selection(selected)

    print("\n")
    print("=" * 75)
    print("SIX-CONTRACT SELECTION COMPLETED SUCCESSFULLY")
    print("=" * 75)


if __name__ == "__main__":

    main()
