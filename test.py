import requests
import pandas as pd
import json

url = "https://raw.githubusercontent.com/datasets/nasdaq-listings/master/data/nasdaq-listed-symbols.csv"

df = pd.read_csv(url)

tickers = df["Symbol"].dropna().tolist()

with open("tickers.json", "w") as f:
    json.dump(tickers, f, indent=2)

print("Total:", len(tickers))