import os
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / "config" / ".env"

load_dotenv(dotenv_path=ENV_FILE, override=True)

CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "").strip()
ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "").strip()

print("ENV FILE FOUND:", ENV_FILE.exists())
print("CLIENT ID LOADED:", bool(CLIENT_ID))
print("ACCESS TOKEN LOADED:", bool(ACCESS_TOKEN))

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

    print("\n" + "=" * 60)
    print(f"CONTRACT: {name}")
    print(f"SECURITY ID: {security_id}")
    print("=" * 60)

    payload = {
        "securityId": security_id,
        "exchangeSegment": "NSE_FNO",
        "instrument": "OPTSTK",
        "interval": "5",
        "oi": False,
        "fromDate": from_date.strftime("%Y-%m-%d %H:%M:%S"),
        "toDate": to_date.strftime("%Y-%m-%d %H:%M:%S"),
    }

    try:
        response = requests.post(
            URL,
            headers=headers,
            json=payload,
            timeout=30,
        )

        print("HTTP STATUS:", response.status_code)

        if response.status_code != 200:
            print("API ERROR:", response.text[:1000])
            continue

        data = response.json()

        if not data or "timestamp" not in data:
            print("No timestamp data returned.")
            print("Response:", str(data)[:1000])
            continue

        df = pd.DataFrame({
            "timestamp": pd.to_datetime(
                data["timestamp"],
                unit="s",
                utc=True
            ).tz_convert("Asia/Kolkata"),
            "open": data["open"],
            "high": data["high"],
            "low": data["low"],
            "close": data["close"],
            "volume": data["volume"],
        })

        df = df.sort_values("timestamp")

        print("TOTAL CANDLES:", len(df))
        print("FIRST CANDLE:", df["timestamp"].min())
        print("LAST CANDLE:", df["timestamp"].max())

        print("\nDAILY CANDLE COUNTS:")
        print(df.groupby(df["timestamp"].dt.date).size())

        print("\nDAILY VOLUME TOTALS:")
        print(df.groupby(df["timestamp"].dt.date)["volume"].sum())

    except Exception as error:
        print("ERROR:", error)
