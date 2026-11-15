
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

FILE = ROOT / "data" / "hal_atm_indicators.csv"

df = pd.read_csv(FILE)

df["timestamp"] = pd.to_datetime(df["timestamp"])

df["volume"] = pd.to_numeric(
    df["volume"], errors="coerce"
)

df["AVG_VOLUME_20"] = pd.to_numeric(
    df["AVG_VOLUME_20"], errors="coerce"
)

# Check only HAL PE ITM contract
df = df[df["security_id"] == 113696].copy()

df = df.sort_values("timestamp")

print("\nVOLUME DIAGNOSTIC")
print("=" * 70)

print("Total rows:", len(df))

print("Missing volume:", df["volume"].isna().sum())

print("Zero volume:", (df["volume"] == 0).sum())

print("Missing average volume:",
      df["AVG_VOLUME_20"].isna().sum())

# Examine each trading session
df["trading_date"] = df["timestamp"].dt.date

print("\nSESSION-WISE ANALYSIS")

for trading_date, group in df.groupby("trading_date"):

    group = group.sort_values("timestamp")

    valid_avg = group["AVG_VOLUME_20"].first_valid_index()

    print("\nDate:", trading_date)

    print("Total candles:", len(group))

    print("Zero volume:", (group["volume"] == 0).sum())

    print("Missing volume:", group["volume"].isna().sum())

    print("Missing AVG_VOLUME_20:",
          group["AVG_VOLUME_20"].isna().sum())

    if valid_avg is not None:

        print(
            "First valid AVG_VOLUME_20:",
            group.loc[valid_avg, "timestamp"]
        )

    else:

        print("No valid average volume")

print("\nFIRST VALID AVG VOLUME RECORDS")

print(
    df.loc[
        df["AVG_VOLUME_20"].notna(),
        [
            "timestamp",
            "volume",
            "AVG_VOLUME_20",
            "REL_VOLUME"
        ]
    ].head(10).to_string(index=False)
)

print("\nDIAGNOSTIC COMPLETED")
