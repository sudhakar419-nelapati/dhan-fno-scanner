
import os
import requests
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "config" / ".env", override=True)

URL = "https://api.dhan.co/v2/charts/intraday"

headers = {
    "access-token": os.getenv("DHAN_ACCESS_TOKEN", "").strip(),
    "client-id": os.getenv("DHAN_CLIENT_ID", "").strip(),
    "Content-Type": "application/json",
}

contracts = {
    "ITM_4750": "113693",
    "ATM_4800": "95757",
}

for name, security_id in contracts.items():

    print("\n" + "=" * 60)
    print(name)
    print("=" * 60)

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
        URL, headers=headers, json=payload, timeout=30
    )

    print("HTTP:", response.status_code)

    if response.status_code != 200:
        print(response.text[:1000])
        continue

    data = response.json()

    df = pd.DataFrame({
        "timestamp": pd.to_datetime(
            data["timestamp"], unit="s", utc=True
        ).tz_convert("Asia/Kolkata"),
        "open": data["open"],
        "high": data["high"],
        "low": data["low"],
        "close": data["close"],
        "volume": data["volume"],
    })

    for date in ["2026-11-11", "2026-11-12"]:

        day = df[
            df["timestamp"].dt.strftime("%Y-%m-%d") == date
            ].copy()

        day["time"] = day["timestamp"].dt.strftime("%H:%M:%S")

        minute = day["timestamp"].dt.minute
        second = day["timestamp"].dt.second

        unusual = day[
            (minute % 5 != 0) | (second != 0)
            ]

        print("\nDATE:", date)
        print("FIRST 5 RECORDS:")
        print(day.head(5).to_string(index=False))

        print("\nLAST 5 RECORDS:")
        print(day.tail(5).to_string(index=False))

        print("\nTIMESTAMPS NOT ON 5-MINUTE BOUNDARY:")
        print(unusual.to_string(index=False))

        print("\nVOLUME TOTAL:", day["volume"].sum())
