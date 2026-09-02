"""Local draft board server.

Works with no ESPN connection at all -- the board and click-to-cross-off are
fully usable offline. Polling, when configured, just automates the crossing off.
"""

import asyncio
import json
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import draft
from ingest import normalize
from espn import Config, DraftState, EspnClient, EspnError

HERE = Path(__file__).parent
POLL_SECONDS = 2.5
UNKNOWN_TEAM = 0   # manual cross-off where we don't know who took him
MY_TEAM_MANUAL = -1  # stand-in for "my team" before ESPN settings are known

# From the league's own legend: 10-team, full PPR, no kicker,
# QB / RB / RB / WR / WR / WR-TE / TE / FLEX / D-ST, 16 roster (7 bench).
# Used so the board is fully useful with no ESPN connection at all; the live
# poll overwrites all of this the moment it succeeds.
DEFAULT_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "WR/TE": 1, "TE": 1,
                 "FLEX": 1, "DST": 1, "BE": 7}
DEFAULT_TEAMS = 10
DEFAULT_ROSTER = 16

app = FastAPI()

# The ESPN draft room posts scraped picks here from its own page context.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://fantasy.espn.com"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def private_network_access(request, call_next):
    """Chrome blocks HTTPS pages from reaching 127.0.0.1 unless the preflight
    explicitly opts in. Without this the draft-room bridge silently fails."""
    if request.method == "OPTIONS":
        from starlette.responses import Response
        origin = request.headers.get("origin", "*")
        return Response(status_code=200, headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Allow-Private-Network": "true",
            "Access-Control-Max-Age": "600",
        })
    resp = await call_next(request)
    resp.headers["Access-Control-Allow-Private-Network"] = "true"
    return resp


def default_state() -> DraftState:
    st = DraftState()
    st.slot_counts = dict(DEFAULT_SLOTS)
    st.team_count = DEFAULT_TEAMS
    st.roster_size = DEFAULT_ROSTER
    return st


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.board: list[dict] = []
        self.manual: dict[int, int] = {}     # espn_id -> team_id
        self.api: dict[int, int] = {}
        self.scraped: dict[int, int] = {}    # pushed from the ESPN draft room
        self.by_norm: dict[str, dict] = {}   # normalized name -> board row
        self.by_id: dict[int, dict] = {}     # espn_id -> board row
        self.api_order: list[int] = []       # espn_ids, draft order, from API
        self.scrape_order: list[int] = []    # espn_ids, draft order, scraped
        self.scrape_prev: list[str] = []     # last raw scrape, to infer direction
        self.scrape_newest_first: bool | None = None  # learned, then sticky
        self.history: list[int] = []          # manual picks, for undo
        self.version = 0
        self.conn = {"status": "offline", "detail": "polling not started", "at": None}
        self.state: DraftState = default_state()
        self.cfg: Config | None = None
        self.my_slot: int | None = None
        # Set by /api/_replay so a live poll can't overwrite rehearsal data.
        self.replay_mode = False

    def bump(self):
        self.version += 1

    @property
    def my_team_id(self) -> int:
        return self.cfg.team_id if (self.cfg and self.cfg.team_id) else MY_TEAM_MANUAL

    def drafted(self) -> dict[int, int]:
        # API is authoritative when it has anything; scraped beats manual.
        return {**self.manual, **self.scraped, **self.api}

    def recent(self, limit: int = 14) -> list[dict]:
        """Most recent picks first, so freshness is verifiable at a glance.

        The API is preferred because it carries real pick numbers and team
        ids; the scraper only knows order.
        """
        order = self.api_order or self.scrape_order or list(self.history)
        drafted = self.drafted()
        out = []
        total = len(order)
        for i, espn_id in enumerate(reversed(order[-limit:])):
            row = self.by_id.get(espn_id)
            if row is None:
                continue
            team_id = drafted.get(espn_id, UNKNOWN_TEAM)
            out.append({
                "pick": total - i,
                "name": row["name"],
                "pos": row["pos"],
                "team": row["team"],
                "mine": team_id == self.my_team_id,
                "by": self.state.teams.get(team_id) if team_id > 0 else None,
            })
        return out

    def view(self) -> dict:
        d = self.drafted()
        team_id = self.my_team_id
        v = draft.build_view(self.board, d, team_id, self.state, self.my_slot)
        v["recent"] = self.recent()
        v["connection"] = self.conn
        v["version"] = self.version
        v["league"] = {
            "teams": self.state.team_count,
            "roster_size": self.state.roster_size,
            "my_slot": self.my_slot,
            "my_team_id": team_id,
            "slots": self.state.slot_counts,
        }
        return v


S = State()


def oldest_first(ids: list[int], raw: list[str]) -> list[int]:
    """Return scraped picks in draft order.

    ESPN's pick list may render newest-first or oldest-first and we can't know
    which from a single sample. Watching where new entries appear settles it:
    if the previous scrape is a *suffix* of this one, new picks arrived at the
    head, so the list is newest-first. The answer is sticky once learned.
    """
    prev = S.scrape_prev
    if S.scrape_newest_first is None and prev and len(raw) > len(prev):
        if raw[-len(prev):] == prev:
            S.scrape_newest_first = True
        elif raw[:len(prev)] == prev:
            S.scrape_newest_first = False
    S.scrape_prev = list(raw)
    return list(reversed(ids)) if S.scrape_newest_first else ids


def load_board() -> list[dict]:
    p = HERE / "board.json"
    if not p.exists():
        raise SystemExit("board.json missing -- run ingest.py first.")
    return json.loads(p.read_text())


def poll_loop():
    """Background ESPN poll. Never fatal: failures degrade to manual mode."""
    backoff = POLL_SECONDS
    while True:
        try:
            if S.replay_mode:
                time.sleep(POLL_SECONDS)
                continue
            client = EspnClient(S.cfg)
            state = client.fetch_draft()
            with S.lock:
                # Re-check under the lock: a replay may have started while
                # this fetch was in flight, and a stale result must not
                # overwrite it.
                if S.replay_mode:
                    continue
                api = {
                    p["espn_id"]: p["team_id"]
                    for p in state.picks
                    if p.get("espn_id") and p.get("team_id")
                }
                changed = api != S.api
                S.api = api
                S.api_order = [p["espn_id"] for p in state.picks]
                S.state = state
                if S.my_slot is None and S.cfg and S.cfg.team_id:
                    S.my_slot = (S.cfg.draft_slot
                                 or draft.slot_from_draft_order(state.draft_order, S.cfg.team_id))
                # Don't report "0 picks" over a working draft-room bridge.
                detail = (f"draft room: {len(S.scraped)} picks"
                          if (not api and S.scraped) else f"{len(api)} picks")
                new_conn = {"status": "live", "detail": detail, "at": time.time()}
                if changed or S.conn["status"] != "live":
                    S.conn = new_conn
                    S.bump()
                else:
                    S.conn = new_conn
            backoff = POLL_SECONDS
        except EspnError as e:
            with S.lock:
                S.conn = {"status": e.kind, "detail": str(e), "at": time.time()}
                S.bump()
            backoff = min(backoff * 2, 30)
        except Exception as e:  # noqa: BLE001 - poll thread must never die
            with S.lock:
                S.conn = {"status": "error", "detail": repr(e), "at": time.time()}
                S.bump()
            backoff = min(backoff * 2, 30)
        time.sleep(backoff)


@app.on_event("startup")
def startup():
    S.board = load_board()
    S.by_norm = {normalize(b["name"]): b for b in S.board}
    S.by_id = {b["espn_id"]: b for b in S.board}
    try:
        S.cfg = Config.load()
        S.my_slot = S.cfg.draft_slot
        threading.Thread(target=poll_loop, daemon=True).start()
    except EspnError as e:
        S.conn = {"status": "manual", "detail": str(e), "at": time.time()}


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/state")
def get_state():
    with S.lock:
        return S.view()


class PickIn(BaseModel):
    espn_id: int
    mine: bool = False


@app.post("/api/pick")
def manual_pick(p: PickIn):
    with S.lock:
        if not any(b["espn_id"] == p.espn_id for b in S.board):
            raise HTTPException(404, "not on the board")
        S.manual[p.espn_id] = S.my_team_id if p.mine else UNKNOWN_TEAM
        S.history.append(p.espn_id)
        S.bump()
        return {"ok": True, "version": S.version}


@app.post("/api/undo")
def undo():
    with S.lock:
        if not S.history:
            raise HTTPException(400, "nothing to undo")
        espn_id = S.history.pop()
        S.manual.pop(espn_id, None)
        S.bump()
        return {"ok": True, "undone": espn_id, "version": S.version}


@app.post("/api/reset")
def reset():
    """Clear manual picks and drop out of replay mode back to live polling."""
    with S.lock:
        S.manual.clear()
        S.history.clear()
        S.replay_mode = False
        S.api = {}
        S.bump()
        return {"ok": True}


class SlotIn(BaseModel):
    slot: int | None = None


@app.post("/api/slot")
def set_slot(s: SlotIn):
    """ESPN often doesn't fix the draft order until draft night, and the
    survival math is meaningless without it."""
    with S.lock:
        if s.slot is not None and not (1 <= s.slot <= S.state.team_count):
            raise HTTPException(400, f"slot must be 1..{S.state.team_count}")
        S.my_slot = s.slot
        S.bump()
        return {"ok": True, "my_slot": S.my_slot}


@app.get("/api/names")
def names():
    """Board names, so a page-side scraper can find them in the DOM."""
    with S.lock:
        return {"names": [b["name"] for b in S.board]}


class ScrapeIn(BaseModel):
    """Player names lifted straight out of the ESPN draft room DOM."""
    names: list[str]
    mine: list[str] = []


@app.post("/api/scrape")
def scrape(s: ScrapeIn):
    """Fallback transport for when ESPN's read API doesn't carry live picks.

    Takes names rather than ids because the DOM only has names; they are
    matched against the board with the same normalization ingest.py used.
    """
    with S.lock:
        mine_norm = {normalize(n) for n in s.mine}
        found: dict[int, int] = {}
        unmatched: list[str] = []
        ordered: list[int] = []
        for raw in s.names:
            row = S.by_norm.get(normalize(raw))
            if row is None:
                unmatched.append(raw)
                continue
            found[row["espn_id"]] = (S.my_team_id if normalize(raw) in mine_norm
                                     else UNKNOWN_TEAM)
            ordered.append(row["espn_id"])

        S.scrape_order = oldest_first(ordered, s.names)
        if found != S.scraped:
            S.scraped = found
            S.conn = {"status": "live", "detail": f"draft room: {len(found)} picks",
                      "at": time.time()}
            S.bump()
        return {"ok": True, "matched": len(found), "unmatched": unmatched,
                "newest_first": S.scrape_newest_first}


class ReplayIn(BaseModel):
    """Test-only: stand in for what the poller would have produced."""
    picks: list[dict]
    my_team_id: int | None = None


@app.post("/api/_replay")
def replay(r: ReplayIn):
    with S.lock:
        S.replay_mode = True
        good = [p for p in r.picks
                if p.get("espn_id") and p.get("team_id") and p["espn_id"] > 0]
        S.api = {p["espn_id"]: p["team_id"] for p in good}
        S.api_order = [p["espn_id"] for p in good]
        if r.my_team_id is not None:
            if S.cfg is None:
                S.cfg = Config(team_id=r.my_team_id)
            else:
                S.cfg.team_id = r.my_team_id
        S.conn = {"status": "live", "detail": f"replay {len(S.api)} picks", "at": time.time()}
        S.bump()
        return {"ok": True, "picks": len(S.api)}


@app.get("/api/events")
async def events():
    async def gen():
        last = -1
        while True:
            with S.lock:
                v = S.version
                payload = S.view() if v != last else None
            if payload is not None:
                last = v
                yield f"data: {json.dumps(payload)}\n\n"
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
