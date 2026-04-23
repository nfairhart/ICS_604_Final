"""
Fetch 2024-25 NBA team Net Ratings (NRtg) from Basketball-Reference.

Basketball-Reference hides some tables in HTML comments; this script
uncomments them before parsing with lxml.

Output:
    nba_net_ratings_2025.csv    columns: team (Kalshi 3-letter code), net_rating
"""

import io
import requests
import pandas as pd
from bs4 import BeautifulSoup

URL = "https://www.basketball-reference.com/leagues/NBA_2025.html"

# Full team name → Kalshi 3-letter ticker code (matches gdelt_nba_fetch.py TEAM_NAMES)
NAME_TO_KALSHI = {
    "Atlanta Hawks": "ATL",
    "Boston Celtics": "BOS",
    "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI",
    "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL",
    "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW",
    "Houston Rockets": "HOU",
    "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC",
    "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA",
    "Washington Wizards": "WAS",
}


def fetch_advanced_team() -> pd.DataFrame:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; research-scraper/1.0)"}
    resp = requests.get(URL, headers=headers, timeout=30)
    resp.raise_for_status()

    # BBRef wraps some tables in HTML comments to defer rendering
    html = resp.text.replace("<!--", "").replace("-->", "")
    soup = BeautifulSoup(html, "html.parser")

    table = soup.find("table", {"id": "advanced-team"})
    if table is None:
        raise RuntimeError("advanced-team table not found — page structure may have changed")

    df = pd.read_html(io.StringIO(str(table)), header=[0, 1])[0]
    return df


def extract_net_ratings(df: pd.DataFrame) -> pd.DataFrame:
    # Flatten multi-level columns: keep the inner (level 1) name where informative
    def flatten(col):
        top, bot = str(col[0]), str(col[1])
        if "Unnamed" in top:
            return bot
        return f"{top}_{bot}" if bot not in top else top

    df.columns = [flatten(c) for c in df.columns]

    # Drop separator rows Basketball-Reference inserts as sub-headers
    df = df[df["Team"].notna() & (df["Team"] != "Team")].copy()

    # Strip playoff asterisk and whitespace
    df["team_clean"] = df["Team"].str.replace(r"\*$", "", regex=True).str.strip()
    df["team"] = df["team_clean"].map(NAME_TO_KALSHI)
    df["net_rating"] = pd.to_numeric(df["NRtg"], errors="coerce")

    unmatched = df[df["team"].isna()]["team_clean"].tolist()
    if unmatched:
        print(f"WARNING: could not map these team names to Kalshi codes: {unmatched}")

    return df[["team", "net_rating"]].dropna().reset_index(drop=True)


def main():
    print("Fetching Basketball-Reference advanced team stats for 2024-25 season...")
    raw = fetch_advanced_team()
    ratings = extract_net_ratings(raw)
    print(f"Extracted net ratings for {len(ratings)} teams\n")
    print(ratings.to_string(index=False))

    out = "nba_net_ratings_2025.csv"
    ratings.to_csv(out, index=False)
    print(f"\nSaved → {out}")


if __name__ == "__main__":
    main()
