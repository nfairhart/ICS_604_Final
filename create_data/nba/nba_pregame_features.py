"""
Build a model-ready pregame feature snapshot for each NBA game.

For each game in the Kalshi dataset, this script computes rolling
trailing statistics (last N games) for both teams as of game night,
then z-score normalizes within each season so 2024-25 and 2025-26
data can be combined without season-level drift.

Output:
    nba_pregame_features.csv   — one row per game, features + outcome
"""

import time
from pathlib import Path

import pandas as pd
import numpy as np
from nba_api.stats.endpoints import teamgamelogs, leaguegamefinder
from nba_api.stats.static import teams as nba_teams_static

HERE = Path(__file__).parent
ROOT = HERE.parent.parent

# ── config ────────────────────────────────────────────────────────────────────
WINDOW = 10          # rolling game window for trailing stats
SEASONS = ["2024-25", "2025-26"]
SLEEP_S = 0.7        # polite delay between API calls (rate limit)

# Kalshi 3-letter codes → NBA team abbreviations used by the API
KALSHI_TO_NBA = {
    "ATL": "ATL", "BOS": "BOS", "BKN": "BKN", "CHA": "CHA", "CHI": "CHI",
    "CLE": "CLE", "DAL": "DAL", "DEN": "DEN", "DET": "DET", "GSW": "GSW",
    "HOU": "HOU", "IND": "IND", "LAC": "LAC", "LAL": "LAL", "MEM": "MEM",
    "MIA": "MIA", "MIL": "MIL", "MIN": "MIN", "NOP": "NOP", "NYK": "NYK",
    "OKC": "OKC", "ORL": "ORL", "PHI": "PHI", "PHX": "PHX", "POR": "POR",
    "SAC": "SAC", "SAS": "SAS", "TOR": "TOR", "UTA": "UTA", "WAS": "WAS",
}

# nba_api team abbreviation → numeric team_id
def build_abbr_to_id() -> dict:
    return {t["abbreviation"]: t["id"] for t in nba_teams_static.get_teams()}


# ── fetch game logs for every team, both seasons ──────────────────────────────
def fetch_all_game_logs(seasons: list[str], abbr_to_id: dict) -> pd.DataFrame:
    frames = []
    for season in seasons:
        print(f"\nFetching {season} game logs...")
        for abbr, tid in abbr_to_id.items():
            try:
                gl = teamgamelogs.TeamGameLogs(
                    team_id_nullable=tid,
                    season_nullable=season,
                    season_type_nullable="Regular Season",
                ).get_data_frames()[0]
                gl["TEAM_ABBR"] = abbr
                gl["SEASON"] = season
                frames.append(gl)
                time.sleep(SLEEP_S)
            except Exception as e:
                print(f"  WARNING: {abbr} {season} failed: {e}")
    df = pd.concat(frames, ignore_index=True)
    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"])
    df = df.sort_values(["TEAM_ABBR", "SEASON", "GAME_DATE"]).reset_index(drop=True)
    return df


# ── compute per-team rolling features ─────────────────────────────────────────
ROLL_COLS = {
    # column in game log      → feature suffix
    "W":             "win_pct",      # will be mean → win rate
    "PTS":           "pts",
    "OPP_PTS":       "opp_pts",      # derived below as PTS - PLUS_MINUS
    "PLUS_MINUS":    "pm",
    "FG_PCT":        "fg_pct",
    "FG3_PCT":       "fg3_pct",
    "FT_PCT":        "ft_pct",
    "REB":           "reb",
    "AST":           "ast",
    "TOV":           "tov",
    "STL":           "stl",
    "BLK":           "blk",
}


def rolling_features(df: pd.DataFrame, window: int) -> pd.DataFrame:
    """
    For each team+season, compute rolling mean of the prior `window` games
    (shift(1) so the current game is excluded — true pregame snapshot).
    """
    out_frames = []
    for (abbr, season), grp in df.groupby(["TEAM_ABBR", "SEASON"], sort=False):
        grp = grp.sort_values("GAME_DATE").copy()

        # Binary win flag
        grp["W_BIN"] = (grp["WL"] == "W").astype(float)
        # Opponent points not returned directly — derive it
        grp["OPP_PTS"] = grp["PTS"] - grp["PLUS_MINUS"]

        feat_cols = {}
        for src, name in ROLL_COLS.items():
            col = "W_BIN" if src == "W" else src
            if col not in grp.columns:
                continue
            feat_cols[f"roll_{name}"] = (
                grp[col]
                .shift(1)                          # exclude tonight's game
                .rolling(window, min_periods=1)    # use whatever prior games exist
                .mean()
            )

        # Rest days between games
        feat_cols["rest_days"] = grp["GAME_DATE"].diff().dt.days.fillna(3).clip(0, 7)

        feat_df = pd.DataFrame(feat_cols, index=grp.index)
        feat_df["GAME_ID"]    = grp["GAME_ID"]
        feat_df["GAME_DATE"]  = grp["GAME_DATE"]
        feat_df["TEAM_ABBR"]  = abbr
        feat_df["SEASON"]     = season
        feat_df["HOME"]       = grp["MATCHUP"].str.contains("vs.").astype(int)
        feat_df["WL"]         = grp["WL"]
        out_frames.append(feat_df)

    return pd.concat(out_frames, ignore_index=True)


# ── join home + away features into one matchup row ────────────────────────────
def build_matchup_rows(roll: pd.DataFrame, kalshi_games: pd.DataFrame) -> pd.DataFrame:
    """
    For each Kalshi game (team1 vs team2), pull the rolling features
    for both teams on that date and stack them into a single wide row.
    """
    feat_cols = [c for c in roll.columns if c.startswith("roll_") or c == "rest_days"]

    records = []
    for _, kg in kalshi_games.iterrows():
        t1 = KALSHI_TO_NBA.get(kg["team1"])
        t2 = KALSHI_TO_NBA.get(kg["team2"])
        gdate = pd.to_datetime(kg["game_date"])

        # Find the game log row for each team on this date
        r1 = roll[(roll["TEAM_ABBR"] == t1) & (roll["GAME_DATE"] == gdate)]
        r2 = roll[(roll["TEAM_ABBR"] == t2) & (roll["GAME_DATE"] == gdate)]

        if r1.empty or r2.empty:
            continue

        r1, r2 = r1.iloc[0], r2.iloc[0]

        row = {
            "event_ticker":  kg["event_ticker"],
            "game_date":     gdate,
            "team1":         kg["team1"],
            "team2":         kg["team2"],
            "winner":        kg["winner"],
            "team1_home":    int(r1["HOME"]),
            "season":        r1["SEASON"],
            "winner_implied_prob": kg.get("winner_implied_prob", np.nan),
        }

        # Team1 features
        for c in feat_cols:
            row[f"t1_{c}"] = r1[c]
        # Team2 features
        for c in feat_cols:
            row[f"t2_{c}"] = r2[c]
        # Differentials (team1 − team2) — often the most predictive features
        for c in feat_cols:
            row[f"diff_{c}"] = r1[c] - r2[c]

        # Binary outcome: did team1 win?
        row["team1_won"] = int(kg["winner"] == kg["team1"])

        records.append(row)

    return pd.DataFrame(records)


# ── z-score normalization within each season ──────────────────────────────────
def normalize_within_season(df: pd.DataFrame) -> pd.DataFrame:
    num_cols = [c for c in df.columns if c.startswith(("t1_", "t2_", "diff_"))]
    out = df.copy()
    for season, grp in df.groupby("season"):
        idx = grp.index
        for c in num_cols:
            mu, sigma = grp[c].mean(), grp[c].std()
            if sigma > 0:
                out.loc[idx, c] = (grp[c] - mu) / sigma
    return out


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    kalshi = pd.read_csv(ROOT / "data" / "gdelt_nba_games.csv")

    abbr_to_id = build_abbr_to_id()
    print(f"Found {len(abbr_to_id)} NBA teams in static data.")

    logs = fetch_all_game_logs(SEASONS, abbr_to_id)
    print(f"\nTotal game log rows fetched: {len(logs)}")
    logs.to_csv(HERE / "nba_game_logs_raw.csv", index=False)

    roll = rolling_features(logs, WINDOW)
    print(f"Rolling feature rows: {len(roll)}")

    matchups = build_matchup_rows(roll, kalshi)
    print(f"Matchup rows joined: {len(matchups)}")

    matchups_norm = normalize_within_season(matchups)
    matchups_norm.to_csv(ROOT / "data" / "nba_pregame_features.csv", index=False)
    print(f"\nSaved nba_pregame_features.csv ({len(matchups_norm)} rows, "
          f"{len(matchups_norm.columns)} columns)")

    # Quick summary
    feat_cols = [c for c in matchups_norm.columns if c.startswith(("t1_", "t2_", "diff_"))]
    print(f"\nFeature columns ({len(feat_cols)}):")
    for c in feat_cols:
        print(f"  {c}")

    print(f"\nOutcome balance:")
    print(matchups_norm["team1_won"].value_counts())
    print(f"\nMissing values per feature:")
    print(matchups_norm[feat_cols].isna().sum().to_string())


if __name__ == "__main__":
    main()
