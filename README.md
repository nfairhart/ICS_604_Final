# NBA Prediction Market Bias

**ICS 604: Applied Data Science — Final Project**  
Does pre-game media coverage skew Kalshi market prices for NBA games?

---

## Project Overview

This project merges three data sources — Kalshi prediction market contracts, NBA rolling performance statistics, and GDELT media coverage data — to test whether pre-game media attention systematically biases NBA market prices. The full analysis is in `nick_analysis.ipynb`.

---

## Environment Setup

**Requirements:** Python 3.10+

```bash
# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

---

## Google Cloud / BigQuery Setup (one-time)

The GDELT script queries a public dataset hosted on Google BigQuery. You need a free GCP project to run it.

**1. Create a GCP project**
- Go to [https://console.cloud.google.com](https://console.cloud.google.com)
- Click the project dropdown → **New Project** → give it a name → **Create**
- Note your **Project ID** (shown in the console header — looks like `my-project-123456`)

**2. Enable the BigQuery API**
- In the GCP console, go to **APIs & Services → Library**
- Search for **BigQuery API** and click **Enable**

**3. Create a Service Account**
- Go to **IAM & Admin → Service Accounts → Create Service Account**
- Give it any name (e.g., `bigquery-reader`)
- Grant it two roles:
  - `BigQuery Job User`
  - `BigQuery Data Viewer`
- Click **Done**

**4. Download a JSON key**
- Click the service account you just created → **Keys** tab → **Add Key → Create new key → JSON**
- Save the downloaded file somewhere safe (e.g., `~/gcp-key.json`)

**5. Set your credentials**
```bash
export GOOGLE_APPLICATION_CREDENTIALS=~/gcp-key.json
# Add this line to ~/.zshrc to make it permanent
```

**6. Set your Project ID in the GDELT script**  
Open [create_data/gdelt/gdelt_nba_fetch.py](create_data/gdelt/gdelt_nba_fetch.py) and set:
```python
GCP_PROJECT = "your-project-id-here"
```

> **Cost:** GDELT is a public dataset. One year of filtered data scans ~5–30 GB. BigQuery's free tier covers 1 TB/month, so this query costs nothing.

---

## Running the Data Pipeline

Run the scripts in this order. Each step produces output files that the next step reads.

### Step 1 — Pull Kalshi market and trade data

```bash
python create_data/kalshi/kalshi_nba_all.py
```

**Output (in `create_data/kalshi/`):**
- `kxnbagame_markets_all.csv` — one row per settled market
- `kxnbagame_trades_all.parquet` — full trade history (~1.5 GB raw)

> No API key required. Kalshi's public endpoints are used.

---

### Step 2 — Compute volume-weighted average implied probabilities

```bash
python create_data/kalshi/nba_wavg_analysis.py
```

**Reads:** Step 1 outputs  
**Output (in `create_data/kalshi/`):**
- `nba_wavg_analysis.csv` — one row per game with weighted-average YES price in the 1-hour pre-game window

---

### Step 3 — Fetch GDELT media coverage from BigQuery

> Complete the Google Cloud setup above before running this step.

```bash
python create_data/gdelt/gdelt_nba_fetch.py
```

**Reads:** `nba_wavg_analysis.csv` (game dates)  
**Output (in `create_data/gdelt/`):**
- `gdelt_raw_cache.csv` — raw BigQuery results (cached so re-runs skip the query)
- `gdelt_nba_coverage.csv` — per (game, team): article count and average tone
- `gdelt_nba_games.csv` — per game: both teams' coverage + disparity metrics

---

### Step 4 — Pull NBA rolling pregame statistics

```bash
python create_data/nba/nba_pregame_features.py
```

**Reads:** `nba_wavg_analysis.csv` (game list)  
**Output (in `create_data/nba/`):**
- `nba_pregame_features.csv` — one row per game with rolling win%, point differential, FG%, rest days for both teams

> This script calls the NBA Stats API with a 0.7s delay between requests to avoid rate limiting. Expect it to run for several minutes.

---

### Step 5 — Run the analysis notebook

```bash
jupyter notebook nick_analysis.ipynb
```

The notebook merges all three datasets and runs the full analysis: calibration check, permutation test, logistic regression, and model vs. Kalshi comparison. Figures are saved to `analysis/`.

---

## Repository Structure

```
ICS_604_Final/
├── create_data/
│   ├── kalshi/
│   │   ├── kalshi_nba_all.py        # Step 1: pull raw Kalshi data
│   │   └── nba_wavg_analysis.py     # Step 2: compute weighted-avg prices
│   ├── gdelt/
│   │   └── gdelt_nba_fetch.py       # Step 3: query GDELT via BigQuery
│   └── nba/
│       └── nba_pregame_features.py  # Step 4: pull NBA rolling stats
├── analysis/                        # saved figures
├── nick_analysis.ipynb              # Step 5: full analysis
├── report.md / report.pdf           # final report
└── requirements.txt
```

---

## References

1. Kalshi REST API — https://trading-api.kalshi.com/trade-api/v2/openapi.json
2. NBA Stats API — https://www.nba.com/stats
3. GDELT via Google BigQuery — https://cloud.google.com/bigquery/public-data
