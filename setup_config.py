"""Interactive credential setup. Run this in your own terminal.

Cookie values are read without echoing and written straight to
~/.config/draft-overlord/config.json with 0600 permissions. Nothing is
printed back, so the secrets never land in a scrollback or a transcript.
"""

import json
import re
import stat
import sys
from getpass import getpass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from espn import CONFIG_PATH, Config, EspnClient, EspnError


def from_url(url: str) -> tuple[int | None, int | None]:
    """Pull leagueId/teamId out of a pasted ESPN fantasy URL."""
    q = parse_qs(urlparse(url).query)
    league = q.get("leagueId", [None])[0]
    team = q.get("teamId", [None])[0]
    if league is None:  # newer path form: /leagues/123456
        m = re.search(r"/leagues/(\d+)", url)
        league = m.group(1) if m else None
    return (int(league) if league else None, int(team) if team else None)


def main() -> int:
    print("ESPN draft board setup\n" + "=" * 24)
    print("\n1. Open your ESPN team page while logged in and paste the URL.")
    print("   It looks like:")
    print("   https://fantasy.espn.com/football/team?leagueId=123456&teamId=3&seasonId=2026\n")

    league_id = team_id = None
    url = input("   URL (or press enter to type ids by hand): ").strip()
    if url:
        league_id, team_id = from_url(url)
        if league_id:
            print(f"   -> leagueId {league_id}" + (f", teamId {team_id}" if team_id else ""))
        else:
            print("   couldn't find a leagueId in that; falling back to manual entry")

    if not league_id:
        league_id = int(input("   leagueId: ").strip())
    if not team_id:
        raw = input("   teamId (your team; enter to skip): ").strip()
        team_id = int(raw) if raw else 0

    season = input("   season [2026]: ").strip() or "2026"

    print("\n2. Cookies. In the same browser, with fantasy.espn.com open:")
    print("     Chrome/Brave: Cmd+Option+I -> Application tab")
    print("       -> Storage -> Cookies -> https://fantasy.espn.com")
    print("     Safari: enable Develop menu, then Develop -> Show Web Inspector")
    print("       -> Storage -> Cookies")
    print("   Find these two rows and copy their Value cells.\n")
    print("   espn_s2  long, ~300 chars, full of %2F and %3D escapes")
    print("   SWID     short, looks like {AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}\n")
    print("   Input is hidden as you paste.\n")

    espn_s2 = getpass("   espn_s2: ").strip()
    swid = getpass("   SWID: ").strip()

    if not espn_s2 or not swid:
        print("\nBoth cookies are required.")
        return 1
    if len(espn_s2) < 50:
        print(f"\nThat espn_s2 is only {len(espn_s2)} chars, which is too short. "
              "Make sure you copied the whole Value cell.")
        return 1
    if not swid.startswith("{"):
        swid = "{" + swid.strip("{}") + "}"

    cfg = {
        "season": int(season),
        "league_id": league_id,
        "team_id": team_id,
        "draft_slot": None,
        "espn_s2": espn_s2,
        "swid": swid,
    }

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    CONFIG_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    print(f"\nWrote {CONFIG_PATH} (0600)")

    print("\n3. Verifying against ESPN...")
    try:
        state = EspnClient(Config.load()).fetch_draft()
    except EspnError as e:
        print(f"   FAILED [{e.kind}]: {e}")
        if e.kind == "auth":
            print("   -> cookies are wrong or expired. Re-copy both, and make sure")
            print("      you're copying from fantasy.espn.com (not espn.com).")
        elif e.kind == "notfound":
            print("   -> leagueId or season is wrong for this account.")
        return 1

    print(f"   OK: {len(state.teams)} teams, {len(state.picks)} picks so far")
    for tid, name in sorted(state.teams.items()):
        mark = "  <- you" if tid == team_id else ""
        print(f"      {tid}: {name}{mark}")
    if team_id and team_id not in state.teams:
        print(f"\n   WARNING: teamId {team_id} isn't in this league. "
              "Pick yours from the list above and update config.json.")
    if state.slot_counts:
        print(f"   lineup: {state.slot_counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
