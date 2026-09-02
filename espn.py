"""ESPN fantasy API client.

The draft views require your browser cookies; the player universe does not.
Errors are classified so the UI can say something actionable instead of just
"disconnected" -- 401 means re-copy your cookies, 404 means wrong league id.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import requests

BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
CONFIG_PATH = Path(
    os.environ.get("DRAFT_OVERLORD_CONFIG", Path.home() / ".config" / "draft-overlord" / "config.json")
)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# ESPN lineup slot ids -> readable. Our league: QB/RB/RB/WR/WR/WR-TE/TE/FLEX/D-ST
SLOT_NAMES = {
    0: "QB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE", 6: "TE",
    16: "DST", 17: "K", 20: "BE", 21: "IR", 23: "FLEX",
}
# Which board positions may fill a given starting slot.
SLOT_ELIGIBLE = {
    "QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"}, "DST": {"DST"}, "K": {"K"},
    "WR/TE": {"WR", "TE"}, "RB/WR": {"RB", "WR"}, "FLEX": {"RB", "WR", "TE"},
}


class EspnError(Exception):
    def __init__(self, kind: str, msg: str):
        self.kind = kind  # auth | notfound | network | http
        super().__init__(msg)


@dataclass
class Config:
    season: int = 2026
    league_id: int = 0
    team_id: int = 0
    draft_slot: int | None = None
    espn_s2: str = ""
    swid: str = ""

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        if not path.exists():
            raise EspnError(
                "auth",
                f"No config at {path}. Copy config.example.json there and add your "
                f"league id + espn_s2/SWID cookies, then chmod 600.",
            )
        d = json.loads(path.read_text())
        return cls(
            season=d.get("season", 2026),
            league_id=int(d["league_id"]),
            team_id=int(d.get("team_id", 0)),
            draft_slot=d.get("draft_slot"),
            espn_s2=d.get("espn_s2", ""),
            swid=d.get("swid", ""),
        )


@dataclass
class DraftState:
    picks: list[dict] = field(default_factory=list)   # real picks only; see parse()
    teams: dict[int, str] = field(default_factory=dict)
    slot_counts: dict[str, int] = field(default_factory=dict)
    roster_size: int = 16
    pick_slots: int = 0      # total pick slots ESPN created (incl. unfilled)
    team_count: int = 10
    draft_order: list[int] = field(default_factory=list)
    in_progress: bool = False
    complete: bool = False

    @property
    def draft_rounds(self) -> int:
        """Rounds actually drafted -- not roster_size, which counts IR slots
        that are never part of the draft."""
        if self.pick_slots and self.team_count:
            return self.pick_slots // self.team_count
        drafted_slots = sum(
            n for k, n in self.slot_counts.items() if k != "IR"
        )
        return drafted_slots or 16


class EspnClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        if cfg.espn_s2 and cfg.swid:
            swid = cfg.swid if cfg.swid.startswith("{") else "{" + cfg.swid + "}"
            self.s.cookies.update({"espn_s2": cfg.espn_s2, "SWID": swid})

    def _get(self, url: str, **kw) -> dict:
        try:
            r = self.s.get(url, timeout=15, **kw)
        except requests.RequestException as e:
            raise EspnError("network", str(e)) from e
        if r.status_code in (401, 403):
            raise EspnError("auth", "ESPN rejected the cookies. Re-copy espn_s2 and SWID.")
        if r.status_code == 404:
            raise EspnError("notfound", f"League {self.cfg.league_id} not found for season {self.cfg.season}.")
        if r.status_code != 200:
            raise EspnError("http", f"HTTP {r.status_code}")
        return r.json()

    def league_url(self, season: int | None = None) -> str:
        s = season or self.cfg.season
        return f"{BASE}/seasons/{s}/segments/0/leagues/{self.cfg.league_id}"

    def fetch_draft(self, season: int | None = None) -> DraftState:
        d = self._get(
            self.league_url(season),
            params=[("view", "mDraftDetail"), ("view", "mSettings"), ("view", "mTeam")],
        )
        return self.parse(d)

    @staticmethod
    def parse(d: dict) -> DraftState:
        st = DraftState()
        settings = d.get("settings") or {}
        roster = settings.get("rosterSettings") or {}
        counts = roster.get("lineupSlotCounts") or {}
        for slot_id, n in counts.items():
            if not n:
                continue
            name = SLOT_NAMES.get(int(slot_id), f"slot{slot_id}")
            st.slot_counts[name] = st.slot_counts.get(name, 0) + int(n)
        st.roster_size = sum(int(n) for n in counts.values()) or 16

        teams = d.get("teams") or []
        for t in teams:
            nm = t.get("name") or " ".join(
                x for x in [t.get("location"), t.get("nickname")] if x
            ).strip()
            st.teams[t["id"]] = nm or f"Team {t['id']}"
        st.team_count = len(teams) or 10

        draft = d.get("draftDetail") or {}
        st.in_progress = bool(draft.get("inProgress"))
        st.complete = bool(draft.get("drafted"))
        all_picks = draft.get("picks") or []
        for p in all_picks:
            pid = p.get("playerId")
            # ESPN pre-creates every pick slot with playerId -1 and fills them
            # in live. Anything <= 0 is an empty slot, not a drafted player.
            if pid is None or pid <= 0:
                continue
            st.picks.append(
                {
                    "overall": p.get("overallPickNumber"),
                    "round": p.get("roundId"),
                    "team_id": p.get("teamId"),
                    "espn_id": pid,
                    "keeper": bool(p.get("keeper")),
                }
            )
        st.picks.sort(key=lambda x: x["overall"] or 0)
        st.pick_slots = len(all_picks)

        order = (settings.get("draftSettings") or {}).get("pickOrder") or []
        st.draft_order = list(order)
        return st


def starters_from_slots(slot_counts: dict[str, int]) -> dict[str, int]:
    """Starting lineup only -- drop bench and IR."""
    return {k: v for k, v in slot_counts.items() if k not in ("BE", "IR")}
