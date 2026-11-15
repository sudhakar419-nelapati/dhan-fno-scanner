
import pandas as pd
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

FILE = PROJECT_ROOT / "data" / "hal_atm_indicators.csv"

df = pd.read_csv(FILE)

print("\nAVAILABLE COLUMNS:")
print(df.columns.tolist())

print("\nTOTAL RECORDS:", len(df))

for option in ["CE", "PE"]:

    option_df = df[
        df["option_type"] == option
        ].copy()

    option_df = option_df.sort_values(
        "timestamp"
    )

    print("\n" + "=" * 80)
    print(f"HAL ATM {option} - LAST 5 CANDLES")
    print("=" * 80)

    columns = [
        "timestamp",
        "close",
        "EMA_9",
        "EMA_21",
        "EMA_50",
        "RSI_14",
        "VWAP",
        "REL_VOLUME",
        "PRICE_CHANGE_%",
        "MOMENTUM_SCORE",
        "MOMENTUM_STATUS"
    ]

    print(
        option_df[columns]
        .tail(5)
        .to_string(index=False)
    )

print("\nDATA VERIFICATION COMPLETED")
