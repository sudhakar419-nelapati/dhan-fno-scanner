
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

FILE = ROOT / "data" / "hal_atm_candles.csv"

df = pd.read_csv(FILE)

df["timestamp"] = pd.to_datetime(
    df["timestamp"],
    errors="coerce"
)

df["DATE"] = df["timestamp"].dt.date
df["TIME"] = df["timestamp"].dt.strftime("%H:%M")
df["WEEKDAY"] = df["timestamp"].dt.day_name()

print("\nWEEKEND RECORDS")
print("=" * 65)

weekend = df[
    df["timestamp"].dt.dayofweek >= 5
    ]

print(
    weekend[
        ["security_id", "timestamp", "close", "volume"]
    ].head(20).to_string(index=False)
)

print("\nCANDLE TIMES AFTER 15:25")
print("=" * 65)

late = df[
    df["TIME"] > "15:25"
    ]

print(
    late[
        ["security_id", "timestamp", "close", "volume"]
    ].head(30).to_string(index=False)
)

print("\nDAILY CANDLE DISTRIBUTION")
print("=" * 65)

print(
    df.groupby(
        ["DATE", "WEEKDAY", "security_id"]
    ).size().to_string()
)

print("\nTIMESTAMP VERIFICATION COMPLETED")
