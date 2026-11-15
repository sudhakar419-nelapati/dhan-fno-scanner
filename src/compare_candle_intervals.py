
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

dates = ["2026-11-11", "2026-11-12"]

for name, security_id in contracts.items():

    for interval in ["1", "5"]:

        print("\n" + "=" * 65)
        print(f"{name} | INTERVAL: {interval} MINUTE")
        print("=" * 65)

        payload = {
            "securityId": security_id,
            "exchangeSegment": "NSE_FNO",
            "instrument": "OPTSTK",
            "interval": interval,
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

        print("HTTP:", response.status_code)

        if response.status_code != 200:
            print(response.text[:1000])
            continue

        data = response.json()

        df = pd.DataFrame({
            "timestamp": pd.to_datetime(
                data["timestamp"],
                unit="s",
                utc=True
            ).tz_convert("Asia/Kolkata"),
            "volume": data["volume"],
        })

        for date in dates:

            day = df[
                df["timestamp"].dt.strftime("%Y-%m-%d") == date
                ].copy()

            print(f"\nDATE: {date}")
            print("TOTAL CANDLES:", len(day))
            print("ZERO VOLUME:", (day["volume"] == 0).sum())
            print("NON-ZERO VOLUME:", (day["volume"] > 0).sum())

            if not day.empty:
                print("FIRST:", day["timestamp"].min())
                print("LAST:", day["timestamp"].max())

                print("FIRST 10 TIMESTAMPS:")
                print(
                    day["timestamp"].head(10).dt.strftime(
                        "%H:%M:%S"
                    ).to_list()
                )

                print("LAST 5 TIMESTAMPS:")
                print(
                    day["timestamp"].tail(5).dt.strftime(
                        "%H:%M:%S"
                    ).to_list()
                )
