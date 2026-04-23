"""
Test GDELT BigQuery connection using a single game.
Run this before gdelt_nba_fetch.py to verify credentials and query structure.

Usage:
    python gdelt_test.py
"""

from google.cloud import bigquery

GCP_PROJECT = "ics604"   # ← same value as in gdelt_nba_fetch.py

# Test game: Memphis vs Golden State, April 15 2025
# 48-hour window = April 13 and April 14
TEST_GAME       = "KXNBAGAME-25APR15MEMGSW"
TEST_TEAM1      = "Memphis Grizzlies"
TEST_TEAM2      = "Golden State Warriors"
GDELT_START     = "20250413000000"
GDELT_END       = "20250415000000"

QUERY = f"""
SELECT
  SUBSTR(CAST(DATE AS STRING), 1, 8)                         AS date_str,
  SAFE_CAST(SPLIT(V2Tone, ',')[SAFE_OFFSET(0)] AS FLOAT64)  AS avg_tone,
  SAFE_CAST(SPLIT(V2Tone, ',')[SAFE_OFFSET(1)] AS FLOAT64)  AS positive_score,
  SAFE_CAST(SPLIT(V2Tone, ',')[SAFE_OFFSET(2)] AS FLOAT64)  AS negative_score,
  SAFE_CAST(SPLIT(V2Tone, ',')[SAFE_OFFSET(3)] AS FLOAT64)  AS polarity,
  LOWER(AllNames)                                             AS all_names_lower
FROM `gdelt-bq.gdeltv2.gkg`
WHERE DATE >= {GDELT_START}
  AND DATE <  {GDELT_END}
  AND AllNames IS NOT NULL
  AND V2Tone   IS NOT NULL
  AND V2Tone   != ''
  AND (
    LOWER(AllNames) LIKE '%memphis grizzlies%'
    OR LOWER(AllNames) LIKE '%golden state warriors%'
  )
LIMIT 500
"""

def team_summary(label: str, rows):
    if rows.empty:
        print(f"{label} : 0 articles")
        return
    print(f"{label}")
    print(f"  Articles : {len(rows)}")
    print(f"  avg_tone       : {rows['avg_tone'].mean():+.2f}  "
          f"(pos - neg sentiment; range ~-10 to +10 typical)")
    print(f"  positive_score : {rows['positive_score'].mean():.2f}  "
          f"(% of text with positive language)")
    print(f"  negative_score : {rows['negative_score'].mean():.2f}  "
          f"(% of text with negative language)")
    print(f"  polarity       : {rows['polarity'].mean():.2f}  "
          f"(pos + neg; total emotional activation — higher = more charged coverage)")


def main():
    print(f"Test game : {TEST_GAME}")
    print(f"Teams     : {TEST_TEAM1} vs {TEST_TEAM2}")
    print(f"Window    : {GDELT_START} → {GDELT_END}")
    print(f"Project   : {GCP_PROJECT}\n")

    if GCP_PROJECT == "your-gcp-project-id":
        print("ERROR: Set GCP_PROJECT at the top of this file before running.")
        return

    client = bigquery.Client(project=GCP_PROJECT)
    print("Running query (LIMIT 500 rows — cheap test)...")
    job = client.query(QUERY)
    df  = job.to_dataframe()
    print(f"Rows returned: {len(df)}\n")

    if df.empty:
        print("No results — double-check credentials and project ID.")
        return

    grizzlies_rows = df[df["all_names_lower"].str.contains("memphis grizzlies", na=False)]
    warriors_rows  = df[df["all_names_lower"].str.contains("golden state warriors", na=False)]

    team_summary("Memphis Grizzlies", grizzlies_rows)
    print()
    team_summary("Golden State Warriors", warriors_rows)

    print("\n--- Sample rows (first 5) ---")
    print(df[["date_str", "avg_tone", "positive_score", "negative_score",
              "polarity"]].head(5).to_string(index=False))

    print("\nTest passed — credentials and query are working.")

if __name__ == "__main__":
    main()
