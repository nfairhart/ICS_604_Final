"""
Retrieve GDELT 2.0 GKG article mentions and sentiment for NBA teams
in the 48-hour window preceding each game in nba_wavg_analysis.csv.

The script runs one BigQuery query covering the full game date range,
caches the raw result locally, then aggregates per-game metrics in Python.

Setup (one-time):
    pip install google-cloud-bigquery pandas pyarrow db-dtypes

GCP authentication:
    1. Go to https://console.cloud.google.com and create or select a project
    2. Enable the BigQuery API for that project
    3. IAM & Admin → Service Accounts → create one, grant it "BigQuery Job User"
       and "BigQuery Data Viewer" roles
    4. Create a JSON key for the service account and download it
    5. export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
       (add this line to ~/.zshrc to make it permanent)
    6. Set GCP_PROJECT below to your project ID (visible in GCP console header)

Cost estimate:
    GDELT GKG is a public dataset. One year of filtered data ≈ 5–30 GB scanned.
    BigQuery free tier = 1 TB/month, so this query is effectively free.

Output files:
    gdelt_raw_cache.csv         raw BigQuery results (cached to skip re-queries)
    gdelt_nba_coverage.csv      per-(game, team): article_count, avg_tone
    gdelt_nba_games.csv         per-game: both teams + disparity metrics
"""

import os
import sys
from datetime import datetime, timedelta

import pandas as pd
from google.cloud import bigquery

# ---------------------------------------------------------------------------
# Configuration — edit these
# ---------------------------------------------------------------------------

GCP_PROJECT  = "ics604"   # ← replace with your GCP project ID
GAME_CSV     = "nba_wavg_analysis.csv"
MARKETS_CSV  = "kxnbagame_markets_all.csv"
CACHE_FILE   = "gdelt_raw_cache.csv"
COVERAGE_CSV = "gdelt_nba_coverage.csv"
GAMES_CSV    = "gdelt_nba_games.csv"

# ---------------------------------------------------------------------------
# NBA team lookup: Kalshi 3-letter code → full team name (as it appears in GDELT)
# ---------------------------------------------------------------------------

TEAM_NAMES = {
    "ATL": "Atlanta Hawks",
    "BOS": "Boston Celtics",
    "BKN": "Brooklyn Nets",
    "CHA": "Charlotte Hornets",
    "CHI": "Chicago Bulls",
    "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks",
    "DEN": "Denver Nuggets",
    "DET": "Detroit Pistons",
    "GSW": "Golden State Warriors",
    "HOU": "Houston Rockets",
    "IND": "Indiana Pacers",
    "LAC": "Los Angeles Clippers",
    "LAL": "Los Angeles Lakers",
    "MEM": "Memphis Grizzlies",
    "MIA": "Miami Heat",
    "MIL": "Milwaukee Bucks",
    "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans",
    "NYK": "New York Knicks",
    "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic",
    "PHI": "Philadelphia 76ers",
    "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers",
    "SAC": "Sacramento Kings",
    "SAS": "San Antonio Spurs",
    "TOR": "Toronto Raptors",
    "UTA": "Utah Jazz",
    "WAS": "Washington Wizards",
}

MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# ---------------------------------------------------------------------------
# Ticker parsing
# ---------------------------------------------------------------------------

def parse_ticker(ticker: str) -> tuple[datetime, str, str]:
    """
    Parse a KXNBAGAME ticker into (game_date, team1_abbr, team2_abbr).
    Format: KXNBAGAME-YYMMMDDTEAM1TEAM2  e.g. KXNBAGAME-25APR15ATLORL
    """
    suffix = ticker.split("-", 1)[1]          # 25APR15ATLORL
    year   = 2000 + int(suffix[:2])
    month  = MONTH_MAP[suffix[2:5].upper()]
    day    = int(suffix[5:7])
    teams  = suffix[7:]
    team1  = teams[:3].upper()
    team2  = teams[3:6].upper()
    return datetime(year, month, day), team1, team2


# ---------------------------------------------------------------------------
# BigQuery query
# ---------------------------------------------------------------------------

def build_query(start_gdelt: str, end_gdelt: str) -> str:
    """
    Build SQL for GDELT GKG filtered to articles mentioning any NBA team.
    start_gdelt / end_gdelt are YYYYMMDDHHMMSS strings (GDELT DATE format).

    V2Tone breakdown: avg_tone = pos - neg (net sentiment),
    polarity = pos + neg (total emotional activation, always >= 0).
    """
    team_conditions = "\n      OR ".join(
        f"LOWER(AllNames) LIKE '%{name.lower()}%'"
        for name in TEAM_NAMES.values()
    )

    return f"""
SELECT
  SUBSTR(CAST(DATE AS STRING), 1, 8)                           AS date_str,
  SAFE_CAST(SPLIT(V2Tone, ',')[SAFE_OFFSET(0)] AS FLOAT64)    AS avg_tone,
  SAFE_CAST(SPLIT(V2Tone, ',')[SAFE_OFFSET(1)] AS FLOAT64)    AS positive_score,
  SAFE_CAST(SPLIT(V2Tone, ',')[SAFE_OFFSET(2)] AS FLOAT64)    AS negative_score,
  SAFE_CAST(SPLIT(V2Tone, ',')[SAFE_OFFSET(3)] AS FLOAT64)    AS polarity,
  LOWER(AllNames)                                               AS all_names_lower
FROM (
  SELECT *,
    ROW_NUMBER() OVER (PARTITION BY DocumentIdentifier ORDER BY DATE) AS rn
  FROM `gdelt-bq.gdeltv2.gkg`
  WHERE DATE >= {start_gdelt}
    AND DATE <  {end_gdelt}
    AND AllNames IS NOT NULL
    AND V2Tone   IS NOT NULL
    AND V2Tone   != ''
    AND (
        {team_conditions}
    )
)
WHERE rn = 1
"""


def run_bigquery(query: str, project: str) -> pd.DataFrame:
    client = bigquery.Client(project=project)
    print("  Running BigQuery query (this may take 1–5 minutes)...")
    job    = client.query(query)
    df     = job.to_dataframe()
    print(f"  Query complete. Rows returned: {len(df):,}")
    return df


# ---------------------------------------------------------------------------
# Team mention detection
# ---------------------------------------------------------------------------

def teams_in_article(all_names_lower: str) -> list[str]:
    """Return list of team abbreviations whose full name appears in the article."""
    found = []
    for abbr, name in TEAM_NAMES.items():
        if name.lower() in all_names_lower:
            found.append(abbr)
    return found


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # 1. Load game list
    print("Loading game list ...")
    games_df = pd.read_csv(GAME_CSV)
    print(f"  {len(games_df)} games found\n")

    # Parse each game's date and teams
    parsed = []
    for _, row in games_df.iterrows():
        try:
            game_date, team1, team2 = parse_ticker(row["event_ticker"])
            parsed.append({
                "event_ticker":        row["event_ticker"],
                "winner":              row["winner"],
                "loser":               row["loser"],
                "winner_implied_prob": row["winner_implied_prob"],
                "loser_implied_prob":  row["loser_implied_prob"],
                "game_date":           game_date,
                "team1":               team1,
                "team2":               team2,
            })
        except Exception as e:
            print(f"  Warning: could not parse ticker '{row['event_ticker']}': {e}")

    games = pd.DataFrame(parsed)

    min_date = games["game_date"].min()
    max_date = games["game_date"].max()
    print(f"  Game date range: {min_date.date()} → {max_date.date()}\n")

    # 2. GDELT query covers (min_date - 2 days) to (max_date + 1 day)
    gdelt_start = (min_date - timedelta(days=2)).strftime("%Y%m%d") + "000000"
    gdelt_end   = (max_date + timedelta(days=1)).strftime("%Y%m%d") + "000000"

    # 3. Fetch from BigQuery (or load from cache)
    if os.path.exists(CACHE_FILE):
        print(f"Cache file '{CACHE_FILE}' found — skipping BigQuery query.")
        print("  Delete it to force a fresh fetch.\n")
        raw = pd.read_csv(CACHE_FILE, dtype={"date_str": str})
        before = len(raw)
        raw = raw.drop_duplicates()
        dropped = before - len(raw)
        if dropped:
            print(f"  Dropped {dropped:,} duplicate rows from cache.\n")
    else:
        if GCP_PROJECT == "your-gcp-project-id":
            sys.exit(
                "ERROR: Set GCP_PROJECT in the script to your actual Google Cloud project ID.\n"
                "       Then run again."
            )
        query = build_query(gdelt_start, gdelt_end)
        print(f"BigQuery query date range: {gdelt_start} → {gdelt_end}")
        raw = run_bigquery(query, GCP_PROJECT)
        raw.to_csv(CACHE_FILE, index=False)
        print(f"  Saved raw results to '{CACHE_FILE}'\n")

    # 4. Expand to one row per (article, team mention)
    print("Expanding team mentions per article ...")
    records = []
    for _, row in raw.iterrows():
        if pd.isna(row["all_names_lower"]) or pd.isna(row["avg_tone"]):
            continue
        teams_found = teams_in_article(row["all_names_lower"])
        for abbr in teams_found:
            records.append({
                "date_str":       str(row["date_str"])[:8],
                "team":           abbr,
                "avg_tone":       float(row["avg_tone"]),
                "positive_score": float(row["positive_score"]) if pd.notna(row["positive_score"]) else None,
                "negative_score": float(row["negative_score"]) if pd.notna(row["negative_score"]) else None,
                "polarity":       float(row["polarity"])       if pd.notna(row["polarity"])       else None,
            })

    mentions = pd.DataFrame(records)
    print(f"  {len(mentions):,} team-article pairs\n")

    # Aggregate: per (date_str, team) → counts and mean tone components
    daily = (
        mentions
        .groupby(["date_str", "team"])
        .agg(
            article_count =("avg_tone",       "count"),
            avg_tone      =("avg_tone",        "mean"),
            avg_positive  =("positive_score",  "mean"),
            avg_negative  =("negative_score",  "mean"),
            avg_polarity  =("polarity",        "mean"),
        )
        .reset_index()
    )

    # 5. Join GDELT coverage to each game using the 48-hour pre-game window
    # Window = [game_date - 2 days, game_date - 1 day] (the two calendar days before)
    print("Computing per-game coverage metrics ...")
    coverage_rows = []

    for _, game in games.iterrows():
        gdate     = game["game_date"]
        day_minus1 = (gdate - timedelta(days=1)).strftime("%Y%m%d")
        day_minus2 = (gdate - timedelta(days=2)).strftime("%Y%m%d")
        window_dates = {day_minus1, day_minus2}

        for team_abbr in [game["team1"], game["team2"]]:
            team_daily = daily[
                (daily["team"] == team_abbr) &
                (daily["date_str"].isin(window_dates))
            ]
            has_data = len(team_daily) > 0
            coverage_rows.append({
                "event_ticker":  game["event_ticker"],
                "game_date":     gdate.date(),
                "team":          team_abbr,
                "window_start":  day_minus2,
                "window_end":    day_minus1,
                "article_count": int(team_daily["article_count"].sum())  if has_data else 0,
                "avg_tone":      float(team_daily["avg_tone"].mean())    if has_data else None,
                "avg_positive":  float(team_daily["avg_positive"].mean()) if has_data else None,
                "avg_negative":  float(team_daily["avg_negative"].mean()) if has_data else None,
                "avg_polarity":  float(team_daily["avg_polarity"].mean()) if has_data else None,
            })

    coverage = pd.DataFrame(coverage_rows)
    coverage.to_csv(COVERAGE_CSV, index=False)
    print(f"  Saved per-team coverage to '{COVERAGE_CSV}'  ({len(coverage)} rows)\n")

    # 6. Build per-game disparity metrics
    print("Building per-game disparity metrics ...")
    game_rows = []

    for _, game in games.iterrows():
        ticker  = game["event_ticker"]
        team1   = game["team1"]
        team2   = game["team2"]
        winner  = game["winner"]
        loser   = game["loser"]

        t1 = coverage[(coverage["event_ticker"] == ticker) & (coverage["team"] == team1)]
        t2 = coverage[(coverage["event_ticker"] == ticker) & (coverage["team"] == team2)]

        def _val(df, col):
            return float(df[col].iloc[0]) if len(df) and pd.notna(df[col].iloc[0]) else None

        t1_count    = int(t1["article_count"].sum()) if len(t1) else 0
        t2_count    = int(t2["article_count"].sum()) if len(t2) else 0
        t1_tone     = _val(t1, "avg_tone")
        t2_tone     = _val(t2, "avg_tone")
        t1_positive = _val(t1, "avg_positive")
        t2_positive = _val(t2, "avg_positive")
        t1_negative = _val(t1, "avg_negative")
        t2_negative = _val(t2, "avg_negative")
        t1_polarity = _val(t1, "avg_polarity")
        t2_polarity = _val(t2, "avg_polarity")

        # Mention ratio: (t1 - t2) / total, range [-1, 1]; positive → t1 has more coverage
        total_count   = t1_count + t2_count
        mention_ratio = (t1_count - t2_count) / total_count if total_count > 0 else None

        # Tone differentials: positive → t1 covered more positively than t2
        tone_diff     = (t1_tone     - t2_tone)     if (t1_tone     is not None and t2_tone     is not None) else None
        polarity_diff = (t1_polarity - t2_polarity) if (t1_polarity is not None and t2_polarity is not None) else None

        # Media disparity score (proposal definition): avg of normalized mention ratio + tone diff
        # avg_tone diff is typically in [-20, 20]; normalize to [-1, 1] via /10
        if mention_ratio is not None and tone_diff is not None:
            media_disparity = (mention_ratio + tone_diff / 10.0) / 2.0
        else:
            media_disparity = None

        pricing_error_winner = game["winner_implied_prob"] - 1.0
        pricing_error_loser  = game["loser_implied_prob"]

        game_rows.append({
            "event_ticker":              ticker,
            "game_date":                 game["game_date"].date(),
            "team1":                     team1,
            "team2":                     team2,
            "winner":                    winner,
            "loser":                     loser,
            "winner_implied_prob":       game["winner_implied_prob"],
            "loser_implied_prob":        game["loser_implied_prob"],
            # Article counts
            "team1_articles":            t1_count,
            "team2_articles":            t2_count,
            # Tone metrics per team
            "team1_avg_tone":            t1_tone,
            "team2_avg_tone":            t2_tone,
            "team1_avg_positive":        t1_positive,
            "team2_avg_positive":        t2_positive,
            "team1_avg_negative":        t1_negative,
            "team2_avg_negative":        t2_negative,
            "team1_avg_polarity":        t1_polarity,
            "team2_avg_polarity":        t2_polarity,
            # Disparity features for regression
            "mention_ratio_t1":          mention_ratio,
            "tone_diff_t1_minus_t2":     tone_diff,
            "polarity_diff_t1_minus_t2": polarity_diff,
            "media_disparity_t1":        media_disparity,
            # Outcome
            "pricing_error_winner":      pricing_error_winner,
            "pricing_error_loser":       pricing_error_loser,
        })

    out_df = pd.DataFrame(game_rows)
    out_df.to_csv(GAMES_CSV, index=False)
    print(f"  Saved per-game disparity data to '{GAMES_CSV}'  ({len(out_df)} rows)\n")

    # 7. Quick sanity checks
    n_with_coverage = out_df[out_df["team1_articles"] + out_df["team2_articles"] > 0].shape[0]
    pct_coverage    = 100 * n_with_coverage / len(out_df) if len(out_df) else 0
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  Total games:             {len(out_df)}")
    print(f"  Games with coverage:     {n_with_coverage} ({pct_coverage:.1f}%)")
    print(f"  Median articles (team1): {out_df['team1_articles'].median():.0f}")
    print(f"  Median articles (team2): {out_df['team2_articles'].median():.0f}")
    if out_df["team1_avg_tone"].notna().any():
        print(f"  Mean avg_tone  team1: {out_df['team1_avg_tone'].mean():.2f}")
        print(f"  Mean avg_tone  team2: {out_df['team2_avg_tone'].mean():.2f}")
        print(f"  Mean polarity  team1: {out_df['team1_avg_polarity'].mean():.2f}")
        print(f"  Mean polarity  team2: {out_df['team2_avg_polarity'].mean():.2f}")
    print(f"\nOutputs: {COVERAGE_CSV}, {GAMES_CSV}")
    print("\nNext step: merge GAMES_CSV with Kalshi data in nick_analysis.ipynb")
    print("  Regression: pricing_error_winner ~ media_disparity_t1 + controls")


if __name__ == "__main__":
    main()
