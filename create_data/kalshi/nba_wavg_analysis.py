"""
Compute volume-weighted average YES price per market in the window
3.5 hours to 2.5 hours before game end (close_time).

Streams the parquet in small batches so memory stays low.

Output: nba_wavg_analysis.csv
"""

import csv
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import pyarrow.parquet as pq
import pandas as pd

HERE = Path(__file__).parent

MARKETS_CSV    = HERE / "kxnbagame_markets_all.csv"
TRADES_PARQUET = HERE / "kxnbagame_trades_all.parquet"
OUTPUT_CSV     = HERE / "nba_wavg_analysis.csv"
BATCH_SIZE     = 50_000  # rows per iteration

WINDOW_START = timedelta(hours=3.5)
WINDOW_END   = timedelta(hours=2.5)


def load_close_times(markets_csv: str) -> dict:
    """Return {ticker: close_time as UTC-aware datetime}."""
    df = pd.read_csv(markets_csv, usecols=["ticker", "close_time"])
    df["close_time"] = pd.to_datetime(df["close_time"], format="ISO8601", utc=True)
    return dict(zip(df["ticker"], df["close_time"]))


def main():
    print("Loading market close times ...")
    close_times = load_close_times(MARKETS_CSV)
    print(f"  {len(close_times)} markets loaded\n")

    # Accumulators: {market_ticker: [weighted_price_sum, volume_sum, trade_count]}
    acc = defaultdict(lambda: [0.0, 0.0, 0])

    pf = pq.ParquetFile(TRADES_PARQUET)
    total_rows = 0
    kept_rows  = 0

    print(f"Streaming {TRADES_PARQUET} in batches of {BATCH_SIZE:,} rows ...")

    for batch in pf.iter_batches(
        batch_size=BATCH_SIZE,
        columns=["market_ticker", "created_time", "count_fp", "yes_price_dollars"],
    ):
        df = batch.to_pandas()
        total_rows += len(df)

        df["created_time"] = pd.to_datetime(df["created_time"], utc=True, errors="coerce")
        df = df.dropna(subset=["created_time", "count_fp", "yes_price_dollars"])

        for ticker, group in df.groupby("market_ticker", sort=False):
            close = close_times.get(ticker)
            if close is None:
                continue
            lo = close - WINDOW_START
            hi = close - WINDOW_END
            mask = (group["created_time"] >= lo) & (group["created_time"] <= hi)
            window = group[mask]
            if window.empty:
                continue
            acc[ticker][0] += (window["yes_price_dollars"] * window["count_fp"]).sum()
            acc[ticker][1] += window["count_fp"].sum()
            acc[ticker][2] += len(window)
            kept_rows += len(window)

        print(f"  processed {total_rows:>10,} rows  |  kept {kept_rows:,} in-window", end="\r")

    print(f"\n\nDone streaming. Writing {OUTPUT_CSV} ...")

    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["market_ticker", "wavg_yes_price", "total_volume_fp", "trade_count"])
        for ticker, (wsum, vol, cnt) in sorted(acc.items()):
            wavg = wsum / vol if vol > 0 else None
            writer.writerow([ticker, wavg, vol, cnt])

    print(f"Wrote {len(acc)} rows to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
