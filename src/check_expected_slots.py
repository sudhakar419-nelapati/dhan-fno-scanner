
import os
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime
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

headers = {
    "access-token": ACCESS_TOKEN,
    "client-id": CLIENT_ID,
    "Content-Type": "application/json",
}

DATES = ["2026-11-11", "2026-11-12"]

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
        "fromDate": "2026-11-11 09:15:00",
        "toDate": "2026-11-12 15:40:00",
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

    for date in DATES:

        print("\nDATE:", date)

        day = df[
            df["timestamp"].dt.strftime("%Y-%m-%d") == date
            ].copy()

        day["time"] = day["timestamp"].dt.strftime("%H:%M")

        expected = pd.date_range(
            start=f"{date} 09:15",
            end=f"{date} 15:35",
            freq="5min"
        ).strftime("%H:%M").tolist()

        actual = day["time"].tolist()

        missing = sorted(set(expected) - set(actual))
        zero_volume = day[day["volume"] == 0]

        print("EXPECTED SLOTS:", len(expected))
        print("RETURNED CANDLES:", len(day))
        print("MISSING EXPECTED SLOTS:", len(missing))
        print("ZERO VOLUME CANDLES:", len(zero_volume))

        print("\nMISSING TIMES:")
        print(missing)

        print("\nZERO VOLUME TIMES:")
        print(
            zero_volume["time"].tolist()
        )

        print("\nNON-ZERO VOLUME CANDLES:", len(day[day["volume"] > 0]))
