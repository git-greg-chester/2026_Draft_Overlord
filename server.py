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
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import draft
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
        self.history: list[int] = []          # manual picks, for undo
        self.version = 0
        self.conn = {"status": "offline", "detail": "polling not started", "at": None}
        self.state: DraftState = default_state()
        self.cfg: Config | None = None
        self.my_slot: int | None = None

    def bump(self):
        self.version += 1

    @property
    def my_team_id(self) -> int:
        return self.cfg.team_id if (self.cfg and self.cfg.team_id) else MY_TEAM_MANUAL

    def drafted(self) -> dict[int, int]:
        # API is authoritative; manual fills the gaps.
        return {**self.manual, **self.api}

    def view(self) -> dict:
        d = self.drafted()
        team_id = self.my_team_id
        v = draft.build_view(self.board, d, team_id, self.state, self.my_slot)
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
            client = EspnClient(S.cfg)
            state = client.fetch_draft()
            with S.lock:
                api = {
                    p["espn_id"]: p["team_id"]
                    for p in state.picks
                    if p.get("espn_id") and p.get("team_id")
                }
                changed = api != S.api
                S.api = api
                S.state = state
                if S.my_slot is None and S.cfg and S.cfg.team_id:
                    S.my_slot = (S.cfg.draft_slot
                                 or draft.slot_from_draft_order(state.draft_order, S.cfg.team_id))
                new_conn = {"status": "live",
                            "detail": f"{len(api)} picks", "at": time.time()}
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
    with S.lock:
        S.manual.clear()
        S.history.clear()
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
