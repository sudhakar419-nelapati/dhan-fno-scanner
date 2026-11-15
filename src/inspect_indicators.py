
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent

file = PROJECT_ROOT / "data" / "hal_atm_indicators.csv"

df = pd.read_csv(file)

print("\nCOLUMN NAMES")
print(df.columns.tolist())

print("\nTOTAL ROWS")
print(len(df))

print("\nCONTRACT COUNTS")
print(df.groupby("security_id").size())

print("\nLATEST RECORDS BY CONTRACT")

for security_id, group in df.groupby("security_id"):

    print("\nSECURITY ID:", security_id)

    print(group.tail(3).to_string(index=False))

print("\nMISSING VALUES")
print(df.isnull().sum())
