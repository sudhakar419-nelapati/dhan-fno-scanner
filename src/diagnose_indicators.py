
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent

FILE = PROJECT_ROOT / "data" / "hal_atm_indicators.csv"

df = pd.read_csv(FILE)

print("=" * 75)
print("HAL INDICATOR DIAGNOSTICS")
print("=" * 75)

# Convert numeric columns
numeric_columns = [
    "close",
    "volume",
    "RSI_14",
    "VWAP",
    "AVG_VOLUME_20",
    "REL_VOLUME",
    "EMA_9",
    "EMA_21",
    "EMA_50"
]

for col in numeric_columns:
    df[col] = pd.to_numeric(
        df[col],
        errors="coerce"
    )

df["timestamp"] = pd.to_datetime(
    df["timestamp"],
    errors="coerce"
)

# Overall summary
print("\nTOTAL ROWS:", len(df))

print("\nMISSING VALUES BY INDICATOR")

print(
    df.isnull().sum().to_string()
)

# Per-contract analysis
print("\n" + "=" * 75)
print("CONTRACT-WISE MISSING VALUE ANALYSIS")
print("=" * 75)

columns_to_check = [
    "RSI_14",
    "VWAP",
    "AVG_VOLUME_20",
    "REL_VOLUME"
]

for security_id, group in df.groupby("security_id"):

    group = group.sort_values("timestamp")

    print("\nSECURITY ID:", security_id)

    print("OPTION TYPE:", group["option_type"].iloc[0])

    print("TOTAL CANDLES:", len(group))

    print("FIRST TIMESTAMP:", group["timestamp"].iloc[0])

    print("LAST TIMESTAMP:", group["timestamp"].iloc[-1])

    for col in columns_to_check:

        missing = group[col].isna().sum()

        print(
            f"{col:20}: {missing} missing"
        )

    print(
        "ZERO VOLUME CANDLES:",
        (group["volume"] == 0).sum()
    )

    print(
        "NEGATIVE VOLUME CANDLES:",
        (group["volume"] < 0).sum()
    )

    print("\nFIRST 5 ROWS:")

    print(
        group[
            [
                "timestamp",
                "close",
                "volume",
                "RSI_14",
                "VWAP",
                "AVG_VOLUME_20",
                "REL_VOLUME"
            ]
        ].head(5).to_string(index=False)
    )

print("\n" + "=" * 75)
print("DIAGNOSTICS COMPLETED")
print("=" * 75)
