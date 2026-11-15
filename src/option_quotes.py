
from pathlib import Path

import pandas as pd

from market_data import get_ltp_batch


PROJECT_ROOT = Path(__file__).resolve().parent.parent

SELECTION_FILE = (
        PROJECT_ROOT / "data" / "hal_atm_selection.csv"
)


def fetch_option_quotes():

    print("=" * 55)
    print("HAL ATM CALL / PUT PRICE MONITOR")
    print("=" * 55)

    contracts = pd.read_csv(SELECTION_FILE)

    security_ids = (
        contracts["SECURITY_ID"]
        .astype(int)
        .tolist()
    )

    # ONE API REQUEST FOR BOTH OPTIONS

    result = get_ltp_batch(
        exchange_segment="NSE_FNO",
        security_ids=security_ids
    )

    market_data = result.get("data", {}).get(
        "NSE_FNO", {}
    )

    print("\nOPTION PRICE DETAILS")

    for _, contract in contracts.iterrows():

        option_type = contract["OPTION_TYPE"]

        security_id = str(int(contract["SECURITY_ID"]))

        symbol = contract["DISPLAY_NAME"]

        quote = market_data.get(security_id)

        print("\n", option_type)
        print("Symbol:", symbol)
        print("Security ID:", security_id)

        if quote:

            price = quote.get("last_price")

            print("Option LTP: ₹", price)

        else:

            print("Quote unavailable for this contract")

    print("\nOPTION QUOTE FETCH COMPLETED")


if __name__ == "__main__":

    fetch_option_quotes()
