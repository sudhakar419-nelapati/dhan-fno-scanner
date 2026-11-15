
from pathlib import Path

import pandas as pd
import requests


# ==========================================
# 1. PROJECT CONFIGURATION
# ==========================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

DATA_DIR.mkdir(parents=True, exist_ok=True)

INSTRUMENT_URL = (
    "https://images.dhan.co/api-data/"
    "api-scrip-master-detailed.csv"
)

INSTRUMENT_FILE = DATA_DIR / "dhan_instruments.csv"
HAL_OPTIONS_FILE = DATA_DIR / "hal_nse_options.csv"


# ==========================================
# 2. DOWNLOAD DHAN INSTRUMENT MASTER
# ==========================================

def download_instruments():

    print("=" * 60)
    print("DHAN INSTRUMENT MASTER DOWNLOAD")
    print("=" * 60)

    try:
        response = requests.get(
            INSTRUMENT_URL,
            timeout=120
        )

        response.raise_for_status()

        INSTRUMENT_FILE.write_bytes(response.content)

        print("Instrument master downloaded successfully!")

        print("File saved:", INSTRUMENT_FILE)

    except requests.RequestException as error:

        print("DOWNLOAD FAILED:", error)

        return None

    return INSTRUMENT_FILE


# ==========================================
# 3. LOAD INSTRUMENT MASTER
# ==========================================

def load_instruments(file_path):

    print("\nLoading instrument master...")

    try:

        df = pd.read_csv(
            file_path,
            low_memory=False
        )

        df.columns = df.columns.str.strip().str.upper()

        print("Total instruments:", len(df))

        print("Available columns:")

        print(df.columns.tolist())

        return df

    except Exception as error:

        print("FILE LOADING ERROR:", error)

        return None


# ==========================================
# 4. FILTER NSE STOCK OPTIONS
# ==========================================

def filter_nse_stock_options(df):

    print("\nFiltering NSE stock options...")

    required_columns = [
        "EXCH_ID",
        "SEGMENT",
        "INSTRUMENT",
        "UNDERLYING_SYMBOL",
        "SM_EXPIRY_DATE",
        "STRIKE_PRICE",
        "OPTION_TYPE"
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing_columns:

        print("Missing columns:", missing_columns)

        return None

    # Normalize text columns

    for column in [
        "EXCH_ID",
        "SEGMENT",
        "INSTRUMENT",
        "UNDERLYING_SYMBOL",
        "OPTION_TYPE"
    ]:

        df[column] = (
            df[column]
            .astype("string")
            .str.strip()
            .str.upper()
        )

    # NSE stock options only

    nse_options = df[
        (df["EXCH_ID"] == "NSE") &
        (df["SEGMENT"] == "D") &
        (df["INSTRUMENT"] == "OPTSTK") &
        (df["OPTION_TYPE"].isin(["CE", "PE"]))
        ].copy()

    print(
        "Total NSE stock option contracts:",
        len(nse_options)
    )

    return nse_options


# ==========================================
# 5. FILTER HAL OPTIONS
# ==========================================

def filter_hal_options(nse_options):

    print("\nFiltering HAL options...")

    if nse_options is None:
        return None

    hal = nse_options[
        nse_options["UNDERLYING_SYMBOL"] == "HAL"
        ].copy()

    if hal.empty:

        print("No NSE HAL option contracts found.")

        return hal

    # Convert expiry and strike into usable formats

    hal["SM_EXPIRY_DATE"] = pd.to_datetime(
        hal["SM_EXPIRY_DATE"],
        errors="coerce"
    )

    hal["STRIKE_PRICE"] = pd.to_numeric(
        hal["STRIKE_PRICE"],
        errors="coerce"
    )

    hal = hal.dropna(
        subset=["SM_EXPIRY_DATE", "STRIKE_PRICE"]
    )

    # Sort contracts

    hal = hal.sort_values(
        by=[
            "SM_EXPIRY_DATE",
            "STRIKE_PRICE",
            "OPTION_TYPE"
        ]
    )

    # Select useful output columns

    output_columns = [
        "EXCH_ID",
        "SEGMENT",
        "INSTRUMENT",
        "SECURITY_ID",
        "UNDERLYING_SECURITY_ID",
        "UNDERLYING_SYMBOL",
        "SYMBOL_NAME",
        "DISPLAY_NAME",
        "SM_EXPIRY_DATE",
        "STRIKE_PRICE",
        "OPTION_TYPE"
    ]

    available_columns = [
        column
        for column in output_columns
        if column in hal.columns
    ]

    print("\nHAL NSE OPTION CONTRACTS")

    print("-" * 100)

    print(
        hal[available_columns]
        .head(30)
        .to_string(index=False)
    )

    print("\nTotal HAL NSE contracts:", len(hal))

    # Show available expiries

    print("\nAvailable HAL expiries:")

    print(
        hal["SM_EXPIRY_DATE"]
        .drop_duplicates()
        .sort_values()
        .dt.strftime("%Y-%m-%d")
        .to_list()
    )

    # Show available strikes

    print("\nAvailable HAL strikes:")

    print(
        sorted(hal["STRIKE_PRICE"].unique())
    )

    # Save HAL contracts

    hal.to_csv(
        HAL_OPTIONS_FILE,
        index=False
    )

    print("\nHAL contracts saved successfully!")

    print("Output file:", HAL_OPTIONS_FILE)

    return hal


# ==========================================
# 6. MAIN EXECUTION
# ==========================================

def main():

    print("\n")
    print("=" * 60)
    print("DHAN ATM OPTIONS MONITOR")
    print("NSE F&O INSTRUMENT SCANNER")
    print("=" * 60)

    # Download instrument master

    file_path = download_instruments()

    if file_path is None:
        return

    # Load instrument master

    df = load_instruments(file_path)

    if df is None:
        return

    # Filter NSE stock options

    nse_options = filter_nse_stock_options(df)

    if nse_options is None:
        return

    # Filter HAL contracts

    hal = filter_hal_options(nse_options)

    if hal is None or hal.empty:

        print("\nHAL NSE contracts unavailable.")

        return

    print("\n")
    print("=" * 60)
    print("INSTRUMENT SCANNER COMPLETED SUCCESSFULLY")
    print("=" * 60)


if __name__ == "__main__":

    main()
