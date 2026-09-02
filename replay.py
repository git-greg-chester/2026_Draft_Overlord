"""Rehearse a draft against the running board.

Two sources:
  --simulate    invent a plausible draft from ADP (no cookies needed)
  --from-league pull a finished season's real draft (needs cookies)

Either way the picks are fed to the server one at a time, so the whole
pipeline gets exercised the way it will be on draft night.
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

import requests

from draft import my_pick_numbers, pick_number
from espn import Config, EspnClient

HERE = Path(__file__).parent
SERVER = "http://127.0.0.1:8777"

# Fraction of picks spent on players who aren't on our 193-man board
# (kickers, deep sleepers). The app must not choke on these.
OFF_BOARD_RATE = 0.12


def simulate(board: list[dict], teams: int, rounds: int, seed: int, noise: float) -> list[dict]:
    """Draft in ADP order with gaussian jitter, snaking through the teams."""
    rng = random.Random(seed)
    pool = []
    for p in board:
        adp = p.get("espn_live_adp") or p.get("industry_adp") or (p["my_rank"] * 1.0)
        pool.append((adp + rng.gauss(0, noise), p))
    pool.sort(key=lambda x: x[0])
    queue = [p for _, p in pool]

    picks, fake_id = [], -900000
    for overall in range(1, teams * rounds + 1):
        rnd = (overall - 1) // teams + 1
        # which slot is on the clock at this overall pick
        slot = next(s for s in range(1, teams + 1) if pick_number(rnd, s, teams) == overall)
        if rng.random() < OFF_BOARD_RATE:
            fake_id -= 1
            picks.append({"overall": overall, "round": rnd, "team_id": slot,
                          "espn_id": fake_id, "off_board": True})
            continue
        if not queue:
            break
        p = queue.pop(0)
        picks.append({"overall": overall, "round": rnd, "team_id": slot,
                      "espn_id": p["espn_id"], "name": p["name"], "pos": p["pos"]})
    return picks


def from_league(season: int) -> list[dict]:
    cfg = Config.load()
    cfg.season = season
    state = EspnClient(cfg).fetch_draft(season)
    if not state.picks:
        raise SystemExit(f"No picks found for season {season}.")
    return state.picks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--from-league", type=int, metavar="SEASON")
    ap.add_argument("--slot", type=int, default=3, help="which draft slot is me")
    ap.add_argument("--teams", type=int, default=10)
    ap.add_argument("--rounds", type=int, default=16)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--noise", type=float, default=9.0, help="ADP jitter, in picks")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds between picks")
    ap.add_argument("--server", default=SERVER)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.from_league:
        picks = from_league(args.from_league)
        teams, rounds = args.teams, args.rounds
    else:
        board = json.loads((HERE / "board.json").read_text())
        teams, rounds = args.teams, args.rounds
        picks = simulate(board, teams, rounds, args.seed, args.noise)

    mine = set(my_pick_numbers(args.slot, teams, rounds))
    print(f"replaying {len(picks)} picks · slot {args.slot} · your picks: "
          f"{sorted(mine)[:6]}...\n")

    # Without this the survival buckets have no pick to measure against.
    requests.post(f"{args.server}/api/slot", json={"slot": args.slot}, timeout=10).raise_for_status()

    fed: list[dict] = []
    for p in picks:
        fed.append(p)
        r = requests.post(f"{args.server}/api/_replay",
                          json={"picks": fed, "my_team_id": args.slot}, timeout=10)
        r.raise_for_status()

        # Report the board exactly at the moments that matter: my picks.
        if p["overall"] in mine or p["overall"] + 1 in mine:
            st = requests.get(f"{args.server}/api/state", timeout=10).json()
            if p["overall"] + 1 in mine and not args.quiet:
                show(st, p["overall"] + 1)
        if args.delay:
            time.sleep(args.delay)

    st = requests.get(f"{args.server}/api/state", timeout=10).json()
    print("\n=== final ===")
    print(f"drafted {st['drafted_count']} · available {len(st['available'])}")
    roster = sorted(st["mine"], key=lambda x: x["my_rank"])
    print(f"your roster ({len(roster)}):")
    for p in roster:
        print(f"   {p['pos']:<4} {p['name']:<24} rank {p['my_rank']:<4} tier {p['tier']}")
    print(f"open starting slots: {st['needs']['open_slots'] or 'none'}")
    return 0


def show(st: dict, at_pick: int):
    print(f"--- on the clock: pick #{at_pick} ---")
    cl = [c for c in st["cliffs"] if c["cliff"]]
    if cl:
        print("   cliffs: " + ", ".join(
            f"{c['pos']} T{c['tier']} {c['remaining']} left" for c in cl))
    need = st["needs"]["open_slots"]
    print(f"   need: {need or 'nothing'}")
    for p in st["available"][:4]:
        print(f"   #{p['my_rank']:<4}{p['name']:<24}{p['pos']:<4}"
              f"{p['survival']:<9}adp {p['espn_live_adp'] or '-'}")


if __name__ == "__main__":
    sys.exit(main())
