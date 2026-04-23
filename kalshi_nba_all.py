"""
Pull ALL settled KXNBAGAME markets (no date filter) and their trade data.
Trades are written to Parquet in batches to keep memory usage low.

Install deps:
    pip install requests pyarrow

Endpoints used (all public / no auth required):
  GET /markets                -- settled markets, recent
  GET /historical/markets     -- settled markets, older (past historical cutoff)
  GET /markets/trades         -- recent trades
  GET /historical/trades      -- trades past the historical cutoff
  GET /historical/cutoff      -- boundary between the two

Output:
  kxnbagame_markets_all.csv       (small, stays as CSV)
  kxnbagame_trades_all.parquet    (columnar, compressed — much smaller than CSV)
"""

import csv
import requests
import time
from datetime import datetime, timezone
from typing import Optional

import pyarrow as pa
import pyarrow.parquet as pq

BASE_URL       = "https://api.elections.kalshi.com/trade-api/v2"
MARKETS_CSV    = "kxnbagame_markets_all.csv"
TRADES_PARQUET = "kxnbagame_trades_all.parquet"
SERIES_TICKER  = "KXNBAGAME"
BATCH_SIZE     = 50   # flush to parquet every N markets to limit RAM usage


# ---------------------------------------------------------------------------
# PyArrow schema for trades
# ---------------------------------------------------------------------------

TRADE_SCHEMA = pa.schema([
    pa.field("market_ticker",     pa.string()),
    pa.field("trade_id",          pa.string()),
    pa.field("created_time",      pa.string()),  # cast to timestamp in pandas if needed
    pa.field("taker_side",        pa.string()),
    pa.field("count_fp",          pa.float64()),
    pa.field("yes_price_dollars", pa.float64()),
    pa.field("no_price_dollars",  pa.float64()),
])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_dt(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def get_historical_cutoff_ts() -> int:
    r = requests.get(f"{BASE_URL}/historical/cutoff", timeout=30)
    r.raise_for_status()
    data = r.json()
    print(f"  Full cutoff response: {data}")
    raw = data.get("trades_created_ts") or data.get("trades_cutoff_ts") or None
    if not raw:
        return 0
    if isinstance(raw, str):
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    return int(raw)


def paginate(path: str, params: dict, key: str) -> list[dict]:
    """Cursor-based paginator — exhausts all pages and returns combined list."""
    results, cursor = [], ""
    params = dict(params)
    params.setdefault("limit", 1000)

    while True:
        if cursor:
            params["cursor"] = cursor
        r = requests.get(f"{BASE_URL}{path}", params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        results.extend(data.get(key, []))
        cursor = data.get("cursor", "")
        if not cursor:
            break
        time.sleep(0.05)

    return results


def fetch_trades(ticker: str, use_historical: bool) -> list[dict]:
    """Fetch all trades for one ticker, falling back to the other endpoint on 404."""
    primary  = "/historical/trades" if use_historical else "/markets/trades"
    fallback = "/markets/trades"    if use_historical else "/historical/trades"
    try:
        return paginate(primary, {"ticker": ticker}, "trades")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (400, 404):
            return paginate(fallback, {"ticker": ticker}, "trades")
        raise


def trades_to_arrow(trades: list[dict]) -> pa.Table:
    """Convert a list of trade dicts to a typed PyArrow table."""
    def str_col(key):
        return [str(t.get(key)) if t.get(key) is not None else None for t in trades]

    def float_col(key):
        out = []
        for t in trades:
            v = t.get(key)
            try:
                out.append(float(v) if v is not None else None)
            except (TypeError, ValueError):
                out.append(None)
        return out

    return pa.table({
        "market_ticker":     str_col("market_ticker"),
        "trade_id":          str_col("trade_id"),
        "created_time":      str_col("created_time"),
        "taker_side":        str_col("taker_side"),
        "count_fp":          float_col("count_fp"),
        "yes_price_dollars": float_col("yes_price_dollars"),
        "no_price_dollars":  float_col("no_price_dollars"),
    }, schema=TRADE_SCHEMA)


# ---------------------------------------------------------------------------
# Step 1: fetch all settled markets
# ---------------------------------------------------------------------------

def get_all_settled_markets() -> list[dict]:
    params = {"series_ticker": SERIES_TICKER, "status": "settled", "limit": 1000}

    live = paginate("/markets", params, "markets")
    print(f"  /markets            : {len(live)} markets")

    try:
        hist = paginate("/historical/markets", params, "markets")
        print(f"  /historical/markets : {len(hist)} markets")
    except requests.HTTPError as e:
        print(f"  /historical/markets : error ({e}), skipping")
        hist = []

    # Dedupe — live endpoint takes priority on overlap
    by_ticker = {m["ticker"]: m for m in hist}
    for m in live:
        by_ticker[m["ticker"]] = m

    markets = list(by_ticker.values())
    markets.sort(key=lambda m: m.get("close_time") or "")
    return markets


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # 1. Historical cutoff
    print("Fetching historical cutoff ...")
    cutoff_ts = get_historical_cutoff_ts()
    cutoff_dt = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc) if cutoff_ts else None
    print(f"  Cutoff: {cutoff_dt.isoformat() if cutoff_dt else 'N/A'}\n")

    # 2. All settled markets
    print(f"Fetching all settled {SERIES_TICKER} markets ...")
    markets = get_all_settled_markets()
    print(f"  Total unique markets : {len(markets)}\n")

    if not markets:
        print("No markets returned. Check that SERIES_TICKER is correct.")
        return

    # 3. Write markets to CSV (small enough to not matter)
    market_fields = [
        "ticker", "event_ticker", "title", "subtitle",
        "open_time", "close_time", "settlement_ts",
        "result", "settlement_value_dollars", "volume_fp",
        "last_price_dollars", "yes_bid_dollars", "no_bid_dollars",
    ]
    with open(MARKETS_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=market_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(markets)
    print(f"Wrote {MARKETS_CSV}  ({len(markets)} rows)\n")

    # 4. Fetch trades → Parquet (batched writes, low memory)
    print(f"Fetching trades for {len(markets)} markets → {TRADES_PARQUET}\n")

    pq_writer          = None
    total_trades       = 0
    errors             = []
    batch: list[dict]  = []

    def flush():
        nonlocal pq_writer
        if not batch:
            return
        table = trades_to_arrow(batch)
        if pq_writer is None:
            pq_writer = pq.ParquetWriter(
                TRADES_PARQUET, TRADE_SCHEMA, compression="snappy"
            )
        pq_writer.write_table(table)
        batch.clear()

    try:
        for i, m in enumerate(markets, 1):
            ticker        = m["ticker"]
            settled_dt    = parse_dt(m.get("settlement_ts"))
            settled_epoch = int(settled_dt.timestamp()) if settled_dt else 0
            use_hist      = bool(cutoff_ts and settled_epoch < cutoff_ts)

            try:
                trades = fetch_trades(ticker, use_historical=use_hist)
                for t in trades:
                    t["market_ticker"] = ticker
                batch.extend(trades)
                total_trades += len(trades)
                tag = "hist" if use_hist else "live"
                print(f"[{i:>4}/{len(markets)}] {ticker}: {len(trades):>6} trades ({tag})"
                      f"  [total: {total_trades:,}]")
            except requests.HTTPError as e:
                print(f"[{i:>4}/{len(markets)}] {ticker}: ERROR {e}")
                errors.append((ticker, str(e)))

            # Flush to disk every BATCH_SIZE markets
            if i % BATCH_SIZE == 0:
                flush()
                print(f"  → flushed batch to parquet  ({total_trades:,} trades so far)\n")

            time.sleep(0.1)

        flush()  # final flush for remainder

    finally:
        if pq_writer:
            pq_writer.close()

    # 5. Summary
    print(f"\n{'='*60}")
    print(f"Done.")
    print(f"  Markets processed : {len(markets)}")
    print(f"  Total trades      : {total_trades:,}")
    print(f"  Errors            : {len(errors)}")
    if errors:
        print("  Failed tickers:")
        for ticker, err in errors:
            print(f"    {ticker}: {err}")
    print(f"\nOutputs: {MARKETS_CSV}, {TRADES_PARQUET}")
    print(f"\nTo load in pandas:")
    print(f"  import pandas as pd")
    print(f"  trades = pd.read_parquet('{TRADES_PARQUET}')")
    print(f"  trades['created_time'] = pd.to_datetime(trades['created_time'])")


if __name__ == "__main__":
    main()