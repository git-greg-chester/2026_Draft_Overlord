# 2026 Draft Overlord

Live draft board for **Boys Rule Girls Drule** — 10-team, full PPR, snake,
no kicker. Draft: **Sep 4 2026, 8:00pm EDT**.

Reads the draft from ESPN's fantasy API (exact player IDs) rather than reading
the screen. Runs locally in a browser tab beside the ESPN draft room.

Lineup: `QB / RB / RB / WR / WR / WR-TE / TE / FLEX / D-ST` · 16 roster, 7 bench.

---

## Draft-night runbook

```bash
cd ~/projects/2026_Draft_Overlord
python3 -m uvicorn server:app --port 8777
```

Open <http://127.0.0.1:8777> and tile it beside the ESPN draft room.
Set your **slot** in the header dropdown as soon as the draft order is posted —
the survival column is meaningless without it.

- **Click a row** → cross that player off (someone else took him).
- **Click `+`** → add him to *your* roster.
- **Undo** → reverse the last manual action.

If the connection dot is red, the board still works — cross players off by hand.
That is the whole point of the fallback; nothing is lost.

---

## One-time setup

**1. Install deps**

```bash
python3 -m pip install -r requirements.txt
```

**2. Build the board** (already done; re-run if the rankings change)

```bash
python3 ingest.py \
  --overall rankings/2026_Draft_Board_Overall.xlsx \
  --positional rankings/2026_Draft_Board_Positional.xlsx
```

Writes `board.json`. **`unmatched.csv` must be empty** — an unmatched player
never gets crossed off, which is worse than no tool at all. Currently 193/193
match exactly.

**3. Add ESPN credentials** (optional — enables auto cross-off)

```bash
mkdir -p ~/.config/draft-overlord
cp config.example.json ~/.config/draft-overlord/config.json
chmod 600 ~/.config/draft-overlord/config.json
```

Fill in:

| Field | Where to get it |
|---|---|
| `league_id` | ESPN URL: `.../leagues/{THIS}` |
| `team_id` | Your team's id in that league |
| `draft_slot` | Leave `null` — set it in the UI on draft night |
| `espn_s2` | DevTools → Application → Cookies → `fantasy.espn.com` |
| `swid` | Same place, keep the `{braces}` |

Secrets live **outside** the repo on purpose. Nothing here can commit them.

---

## Rehearsing

Simulate a full draft against a running server — no cookies needed:

```bash
python3 -m uvicorn server:app --port 8777 &
python3 replay.py --simulate --slot 3
```

Prints the board at each of your picks. Add `--delay 1` to watch it move in the
browser. The simulation drafts on ADP alone, so its rosters look silly (four
QBs) — it exists to push picks through the pipeline, not to draft well.

Once cookies are configured you can replay a real finished draft:

```bash
python3 replay.py --from-league 2025 --slot 1
```

## Proving picks arrive live

Replaying a *finished* draft proves we can read picks; it does not prove ESPN
writes them to the read API *during* a draft. `watch.py` measures that.

```bash
python3 watch.py --discover        # list leagues, including any mock you joined
python3 watch.py --league <id>     # timestamp every pick as it lands
```

Each new pick prints with the gap since the previous one, so a live draft
should show picks trickling in. If they all appear at once at the end, polling
is the wrong transport and the fallback is a browser extension.

Tests: `python3 -m pytest -q` (21) and `npm test` (jsdom UI render).

---

## What the columns mean

- **Rank** — your board's rank (overall, or positional inside a position tab).
- **safe / 50/50 / gone** — whether ESPN's *live* ADP says he survives to your
  next pick. Live ADP beats the stale industry average at predicting an ESPN
  room. Thresholds are in `draft.py` (`SAFE_MARGIN`, `GONE_MARGIN`).
- **+n** (green) — your `DELTA`: ESPN ranks him this many picks *later* than you
  do, so the room should let him fall. These are the value targets.
- **adp** — ESPN live ADP.
- Header chips show unfilled starting slots and **tier cliffs** (≤2 left in the
  best available tier at a position).

## Files

| File | Role |
|---|---|
| `ingest.py` | Rankings → ESPN player IDs → `board.json` |
| `espn.py` | API client; classifies 401 / 404 / network |
| `draft.py` | Snake math, roster needs, tier cliffs, survival |
| `server.py` | FastAPI + SSE, manual pick/undo |
| `static/index.html` | The board |
| `replay.py` | Rehearsal harness |
