"""Match the draft board rankings to ESPN player IDs.

Run once before draft day. Writes board.json (the app's data source) and
unmatched.csv (must be empty before the draft -- an unmatched player is a
player that never gets crossed off).
"""

import argparse
import csv
import json
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd
import requests
from rapidfuzz import fuzz, process

UNIVERSE_URL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
    "/segments/0/leaguedefaults/3?view=kona_player_info"
)
PROTEAM_URL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
    "?view=proTeamSchedules_wl"
)
FILTER = '{"players":{"limit":1500,"sortDraftRanks":{"sortPriority":100,"sortAsc":true,"value":"PPR"}}}'
UA = "Mozilla/5.0"

POS_BY_ID = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}
ID_BY_POS = {v: k for k, v in POS_BY_ID.items()}

# Board abbreviation -> ESPN abbreviation, where they disagree.
TEAM_ALIASES = {"JAC": "JAX", "WAS": "WSH"}

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize(name: str) -> str:
    """Casefold, strip accents/punctuation/suffixes so 'Ja'Marr' == 'JaMarr'."""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9\s]", "", s)
    tokens = [t for t in s.split() if t not in SUFFIXES]
    return " ".join(tokens)


def fetch_universe(season: int, cache: Path) -> list[dict]:
    """ESPN's full player pool. Public -- no cookies needed."""
    if cache.exists():
        return json.loads(cache.read_text())["players"]
    r = requests.get(
        UNIVERSE_URL.format(season=season),
        headers={"User-Agent": UA, "x-fantasy-filter": FILTER},
        timeout=30,
    )
    r.raise_for_status()
    cache.write_text(json.dumps(r.json()))
    return r.json()["players"]


def fetch_proteams(season: int) -> dict[int, str]:
    r = requests.get(PROTEAM_URL.format(season=season), headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    d = r.json()
    d = d[0] if isinstance(d, list) else d
    teams = d.get("settings", {}).get("proTeams") or d.get("proTeams") or []
    return {t["id"]: t.get("abbrev", "").upper() for t in teams}


def build_index(players: list[dict], proteams: dict[int, str]) -> list[dict]:
    out = []
    for entry in players:
        p = entry["player"]
        pos = POS_BY_ID.get(p.get("defaultPositionId"))
        if pos is None:
            continue
        own = p.get("ownership") or {}
        ranks = (p.get("draftRanksByRankType") or {}).get("PPR") or {}
        out.append(
            {
                "espn_id": p["id"],
                "name": p["fullName"],
                "norm": normalize(p["fullName"]),
                "pos": pos,
                "team": proteams.get(p.get("proTeamId"), ""),
                "adp": own.get("averageDraftPosition"),
                "espn_rank": ranks.get("rank"),
                "injury": p.get("injuryStatus"),
            }
        )
    return out


def load_board(overall: Path, positional: Path) -> pd.DataFrame:
    """Overall board joined with the per-position tiers."""
    df = pd.read_excel(overall, sheet_name="Draft Board")
    df = df[df["PLAYER"].notna()].copy()

    # Positional sheets carry POS RANK / TIER / TIER NAME keyed to MY OVR RANK.
    pos_rows = []
    xl = pd.ExcelFile(positional)
    for sheet in xl.sheet_names:
        if sheet == "Method & Legend":
            continue
        s = xl.parse(sheet)
        s = s[s["PLAYER"].notna()]
        for _, r in s.iterrows():
            pos_rows.append(
                {
                    "MY OVR RANK": r["MY OVR RANK"],
                    "POS RANK": r["POS RANK"],
                    "POS TIER": r["TIER"],
                    "POS TIER NAME": r["TIER NAME"],
                }
            )
    pos_df = pd.DataFrame(pos_rows)
    merged = df.merge(pos_df, left_on="MY RANK", right_on="MY OVR RANK", how="left")
    if len(merged) != len(df):
        raise SystemExit(f"join changed row count {len(df)} -> {len(merged)}; MY OVR RANK not unique")
    return merged


def match(row, index, by_pos) -> tuple[dict | None, str]:
    """Exact on (name, pos); then fuzzy within same pos, preferring same team."""
    norm = normalize(row["PLAYER"])
    pos = str(row["POS"]).upper()
    team = TEAM_ALIASES.get(str(row["TEAM"]).upper(), str(row["TEAM"]).upper())

    cands = by_pos.get(pos, [])
    exact = [c for c in cands if c["norm"] == norm]
    if len(exact) == 1:
        return exact[0], "exact"
    if len(exact) > 1:
        same_team = [c for c in exact if c["team"] == team]
        if len(same_team) == 1:
            return same_team[0], "exact+team"
        return None, f"ambiguous: {len(exact)} share this name/pos"

    if not cands:
        return None, "no candidates at position"
    choices = [c["norm"] for c in cands]
    hit = process.extractOne(norm, choices, scorer=fuzz.WRatio, score_cutoff=86)
    if hit is None:
        return None, "no fuzzy match above cutoff"
    cand = cands[choices.index(hit[0])]
    if cand["team"] != team:
        return cand, f"fuzzy {hit[1]:.0f} TEAM MISMATCH board={team} espn={cand['team']}"
    return cand, f"fuzzy {hit[1]:.0f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--overall", required=True, type=Path)
    ap.add_argument("--positional", required=True, type=Path)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--out", type=Path, default=Path("board.json"))
    ap.add_argument("--unmatched", type=Path, default=Path("unmatched.csv"))
    ap.add_argument("--cache", type=Path, default=Path("universe.json"))
    args = ap.parse_args()

    players = fetch_universe(args.season, args.cache)
    proteams = fetch_proteams(args.season)
    index = build_index(players, proteams)
    by_pos: dict[str, list[dict]] = {}
    for c in index:
        by_pos.setdefault(c["pos"], []).append(c)

    board, unmatched, suspicious = [], [], []
    for _, row in load_board(args.overall, args.positional).iterrows():
        cand, how = match(row, index, by_pos)
        if cand is None:
            unmatched.append({"my_rank": row["MY RANK"], "player": row["PLAYER"],
                              "pos": row["POS"], "team": row["TEAM"], "reason": how})
            continue
        if how.startswith("fuzzy"):
            suspicious.append((row["MY RANK"], row["PLAYER"], cand["name"], how))
        board.append(
            {
                "espn_id": cand["espn_id"],
                "name": row["PLAYER"],
                "espn_name": cand["name"],
                "pos": str(row["POS"]).upper(),
                "team": str(row["TEAM"]).upper(),
                "bye": None if pd.isna(row["BYE"]) else int(row["BYE"]),
                "my_rank": int(row["MY RANK"]),
                "tier": None if pd.isna(row["TIER"]) else int(row["TIER"]),
                "tier_name": None if pd.isna(row["TIER NAME"]) else row["TIER NAME"],
                "pos_rank": None if pd.isna(row.get("POS RANK")) else int(row["POS RANK"]),
                "pos_tier": None if pd.isna(row.get("POS TIER")) else int(row["POS TIER"]),
                "pos_tier_name": None if pd.isna(row.get("POS TIER NAME")) else row["POS TIER NAME"],
                "espn_ovr": None if pd.isna(row["ESPN OVR"]) else int(row["ESPN OVR"]),
                "industry_adp": None if pd.isna(row["INDUSTRY ADP"]) else float(row["INDUSTRY ADP"]),
                "delta": None if pd.isna(row["DELTA (ESPN-MINE)"]) else int(row["DELTA (ESPN-MINE)"]),
                "espn_live_adp": cand["adp"],
                "flags": None if pd.isna(row["FLAGS"]) else str(row["FLAGS"]),
                "match": how,
            }
        )

    args.out.write_text(json.dumps(board, indent=1))
    with args.unmatched.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["my_rank", "player", "pos", "team", "reason"])
        w.writeheader()
        w.writerows(unmatched)

    dupes = len(board) - len({b["espn_id"] for b in board})
    print(f"matched   {len(board)}")
    print(f"unmatched {len(unmatched)}  -> {args.unmatched}")
    print(f"duplicate espn_ids: {dupes}")
    if suspicious:
        print(f"\nfuzzy matches to eyeball ({len(suspicious)}):")
        for r, mine, espn, how in suspicious:
            print(f"  #{r:<4} {mine:<26} -> {espn:<26} [{how}]")
    for u in unmatched:
        print(f"  UNMATCHED #{u['my_rank']} {u['player']} ({u['pos']}/{u['team']}): {u['reason']}")
    return 1 if (unmatched or dupes) else 0


if __name__ == "__main__":
    sys.exit(main())
