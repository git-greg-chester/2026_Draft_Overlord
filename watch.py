"""Watch a draft and measure how fast ESPN's read API reflects picks.

The whole design assumes picks show up in mDraftDetail during the draft rather
than only at the end. That is an assumption until measured, and this measures
it: every new pick is printed with the wall-clock delay since it was noticed.

  python3 watch.py --discover          # find mock/new leagues on this account
  python3 watch.py --league 123456     # watch that league
  python3 watch.py                     # watch the league in config.json
"""

import argparse
import sys
import time
from datetime import datetime

import requests

from espn import Config, EspnClient, EspnError

FAN_URL = "https://fan.api.espn.com/apis/v2/fans/{swid}"


def discover(cfg: Config) -> list[tuple[int, int, str]]:
    """Every fantasy-football league this account can see, mocks included."""
    r = requests.get(
        FAN_URL.format(swid=cfg.swid),
        params={"featureFlags": "expandAthlete", "displayEvents": "true",
                "displayNow": "true", "showAirings": "false"},
        cookies={"espn_s2": cfg.espn_s2, "SWID": cfg.swid},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    r.raise_for_status()
    out = []
    for p in r.json().get("preferences") or []:
        e = (p.get("metaData") or {}).get("entry") or {}
        if e.get("gameId") != 1:            # 1 = fantasy football
            continue
        for g in e.get("groups") or []:
            out.append((int(e.get("seasonId", 0)), int(g["groupId"]),
                        g.get("groupName", "?")))
    return out


def watch(cfg: Config, league_id: int, season: int, interval: float, quiet: bool) -> int:
    cfg.league_id = league_id
    cfg.season = season
    client = EspnClient(cfg)

    print(f"watching league {league_id} season {season}, polling every {interval}s")
    print("ctrl-c to stop\n")

    seen: dict[int, dict] = {}
    started = time.time()
    last_new = None
    polls = 0
    errors = 0

    while True:
        t0 = time.time()
        try:
            st = client.fetch_draft()
        except EspnError as e:
            errors += 1
            print(f"  [{e.kind}] {e}")
            time.sleep(min(interval * 4, 20))
            continue
        latency = time.time() - t0
        polls += 1

        new = [p for p in st.picks if p["overall"] not in seen]
        for p in sorted(new, key=lambda x: x["overall"] or 0):
            seen[p["overall"]] = p
            now = datetime.now().strftime("%H:%M:%S")
            gap = f"{time.time() - last_new:5.1f}s since prev" if last_new else "first pick"
            print(f"  {now}  pick #{p['overall']:<4} r{p['round']:<3} "
                  f"team {p['team_id']:<4} player {p['espn_id']:<9} "
                  f"[{gap}, api {latency*1000:.0f}ms]")
            last_new = time.time()

        if not quiet and polls % 15 == 0 and not new:
            print(f"  ... {polls} polls, {len(seen)} picks, "
                  f"in_progress={st.in_progress} complete={st.complete} "
                  f"slots={st.pick_slots} ({time.time()-started:.0f}s elapsed)")

        if st.complete and seen:
            print(f"\ndraft complete: {len(seen)} picks seen over "
                  f"{time.time()-started:.0f}s, {errors} errors")
            return 0
        time.sleep(interval)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", type=int)
    ap.add_argument("--season", type=int)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    cfg = Config.load()
    if args.discover:
        rows = discover(cfg)
        print(f"{len(rows)} fantasy football league(s) on this account:\n")
        for season, lid, name in sorted(rows):
            mine = "  <- config" if lid == cfg.league_id else ""
            print(f"  season {season}  league {lid:<10} {name}{mine}")
        print("\nJoin a mock draft, then re-run this. A new leagueId means we can")
        print("watch it live with:  python3 watch.py --league <id>")
        return 0

    try:
        return watch(cfg, args.league or cfg.league_id, args.season or cfg.season,
                     args.interval, args.quiet)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())
