
import os
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / "config" / ".env", override=True)

CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "").strip()
ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "").strip()

URL = "https://api.dhan.co/v2/charts/intraday"

CONTRACTS = {
    "HAL_CE_ITM_4750": "113693",
    "HAL_CE_ATM_4800": "95757",
}

to_date = datetime.now()
from_date = to_date - timedelta(days=15)

headers = {
    "access-token": ACCESS_TOKEN,
    "client-id": CLIENT_ID,
    "Content-Type": "application/json",
}

for name, security_id in CONTRACTS.items():

    print("\n" + "=" * 65)
    print("CONTRACT:", name)
    print("=" * 65)

    payload = {
        "securityId": security_id,
        "exchangeSegment": "NSE_FNO",
        "instrument": "OPTSTK",
        "interval": "5",
        "oi": False,
        "fromDate": from_date.strftime("%Y-%m-%d %H:%M:%S"),
        "toDate": to_date.strftime("%Y-%m-%d %H:%M:%S"),
    }

    response = requests.post(
        URL,
        headers=headers,
        json=payload,
        timeout=30,
    )

    print("HTTP STATUS:", response.status_code)

    if response.status_code != 200:
        print(response.text[:1000])
        continue

    data = response.json()

    df = pd.DataFrame({
        "timestamp": pd.to_datetime(
            data["timestamp"], unit="s", utc=True
        ).tz_convert("Asia/Kolkata"),
        "volume": data["volume"],
    })

    df = df.sort_values("timestamp")

    print("TOTAL CANDLES:", len(df))
    print("DUPLICATE TIMESTAMPS:", df["timestamp"].duplicated().sum())
    print("ZERO VOLUME CANDLES:", (df["volume"] == 0).sum())

    df["date"] = df["timestamp"].dt.date

    for date, day_df in df.groupby("date"):

        day_df = day_df.sort_values("timestamp").copy()

        if len(day_df) < 50:
            print(f"\nLOW-CANDLE DAY: {date}")

            print("CANDLE COUNT:", len(day_df))
            print("FIRST:", day_df["timestamp"].min())
            print("LAST:", day_df["timestamp"].max())

            print("\nAVAILABLE TIMESTAMPS:")
            print(
                day_df["timestamp"].dt.strftime("%H:%M").to_list()
            )

            print("\nGAPS GREATER THAN 5 MINUTES:")

            differences = day_df["timestamp"].diff()
            gaps = day_df.loc[
                differences > pd.Timedelta(minutes=5),
                "timestamp"
            ]

            if gaps.empty:
                print("No gaps greater than 5 minutes.")
            else:
                for index in gaps.index:
                    previous = day_df.loc[index - 1, "timestamp"]
                    current = day_df.loc[index, "timestamp"]

                    print(
                        previous.strftime("%H:%M"),
                        "to",
                        current.strftime("%H:%M"),
                        "difference:",
                        current - previous
                    )
