
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

# Locate project root and load credentials
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / "config" / ".env"

load_dotenv(ENV_FILE)

ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN")


def test_connection():

    if not ACCESS_TOKEN:
        print("ERROR: Dhan access token not found!")
        return

    url = "https://api.dhan.co/v2/profile"

    headers = {
        "access-token": ACCESS_TOKEN
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=15
        )

        print("HTTP STATUS:", response.status_code)

        if response.status_code == 200:
            profile = response.json()

            print("DHAN API CONNECTION SUCCESSFUL!")
            print("Client ID:", profile.get("dhanClientId"))
            print("Token Validity:", profile.get("tokenValidity"))

        else:
            print("API ERROR:", response.text)

    except requests.RequestException as error:
        print("CONNECTION ERROR:", error)


if __name__ == "__main__":
    test_connection()
