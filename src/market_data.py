
import os
from pathlib import Path

import requests
from dotenv import load_dotenv


# ==========================================
# PROJECT CONFIGURATION
# ==========================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / "config" / ".env")

CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN")

BASE_URL = "https://api.dhan.co/v2"


# ==========================================
# BATCH LTP FUNCTION
# ==========================================

def get_ltp_batch(exchange_segment, security_ids):

    if not CLIENT_ID or not ACCESS_TOKEN:
        raise ValueError("Dhan credentials missing!")

    url = f"{BASE_URL}/marketfeed/ltp"

    headers = {
        "access-token": ACCESS_TOKEN,
        "client-id": CLIENT_ID,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    payload = {
        exchange_segment: [int(x) for x in security_ids]
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=15
    )

    print("HTTP STATUS:", response.status_code)

    if not response.ok:
        print("DHAN ERROR:", response.text)
        response.raise_for_status()

    return response.json()


# ==========================================
# SINGLE INSTRUMENT LTP FUNCTION
# ==========================================

def get_ltp(exchange_segment, security_id):

    result = get_ltp_batch(
        exchange_segment,
        [security_id]
    )

    return (
        result.get("data", {})
        .get(exchange_segment, {})
        .get(str(security_id), {})
        .get("last_price")
    )


# ==========================================
# TEST
# ==========================================

if __name__ == "__main__":

    price = get_ltp("NSE_EQ", 2303)

    print("HAL UNDERLYING LTP:", price)
