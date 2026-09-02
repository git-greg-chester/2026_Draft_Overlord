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
Then paste snippet **A** from `draft_room_snippets.js` into the draft room
console (set `MY_TEAM` first).

**Run both transports.** Polling and the draft-room scraper feed the same
board and merge safely — a lagging or empty API can never erase scraped
picks, and the API wins where the two disagree. Whichever works on the
night, the board is correct, and clicking rows by hand always works on top
of both. Covered by `test_transports_merge_without_clobbering`.

Draft slot is auto-derived from ESPN's pick order. Override it in the header
dropdown if the order changes.

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

## Do picks arrive live? (partly answered)

Replaying a *finished* draft proves we can read picks; it does not prove ESPN
writes them *during* a draft. `watch.py` measures that.

```bash
python3 watch.py --discover        # list leagues on the account
python3 watch.py --league <id>     # timestamp every pick as it lands
```

**Tested 2026-09-01 against a live ESPN mock draft:** the room reached 160
picks while `mDraftDetail`, `mRoster`, `mTeam`, `mMatchup` and
`mPendingTransactions` all returned zero. On completion the mock league
**404'd** — ESPN deletes mocks, they are never persisted.

So the mock proves nothing about a real draft. What is known:

- Real league drafts *are* written: 2023, 2024, 2025 all hold full pick data.
- Whether they are written live or only at completion is **still untested**.
- To settle it, create a throwaway *real* league (mocks won't do), schedule a
  draft a few minutes out, let it autodraft, and point `watch.py` at it.

Until then the draft-room bridge below is the transport we trust.

## Draft-room bridge (proven)

Reads picks straight out of the ESPN draft room DOM. Validated on a live
mock: 160 picks, 100% name match, no virtualization of the pick list.

Selector: `[class*="pick__message"] .playerinfo__playername`

Direct POST is blocked by Chrome's Private Network Access (HTTPS page ->
127.0.0.1), so either:

- set `chrome://flags/#block-insecure-private-network-requests` to
  **Disabled** and use the direct-fetch snippet, or
- run `python3 clipboard_bridge.py` and use the clipboard snippet, which no
  browser policy blocks. The draft tab must stay focused.

Both snippets live in the console; see git history for the exact text.
`copy()` does not work inside `setInterval` -- use
`navigator.clipboard.writeText`.

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
- **`starter` / `flex` / `depth`** — need weighting. Once a position is covered
  it gets pushed down but never removed (see `PENALTY_*` in `draft.py`).
- **`scarce +n`** — tier scarcity. See below.

## How scarcity works

The cost of waiting on a player is: *how likely his tier is gone when I pick
again* × *how far the drop is to the next tier*. Both are measurable, and the
product is in ranks, so it just subtracts from the adjusted rank.

- **P(tier gone)** — each member's chance of being taken by the horizon, from a
  logistic around his ADP (`ADP_SPREAD`), multiplied together.
- **Cliff** — ranks between the best player in this tier and the best in the
  next tier at that position.
- **Horizon** — the pick *after* your next one. The question is "take him now or
  get one at my next turn", so back-to-back picks (slot 1 holds 20 and 21)
  carry almost no urgency. That falls out of the model rather than being
  special-cased.

Tunable in `draft.py`: `SCARCITY_DAMPING`, `SCARCITY_CAP`, `ADP_SPREAD`.

**Positional runs.** `P(tier gone)` multiplies independent probabilities, which
is optimistic exactly when it matters, because drafts go in bursts — the league
ran 7 RBs in one round in 2025. When a position comes off the board faster than
the remaining pool implies, its horizon stretches by that ratio.

Two guards stop noise becoming signal: a run needs at least `RUN_MIN_COUNT`
picks (a single TE would otherwise divide by a tiny baseline and read as 2.5x),
and the expected count is floored at one. Whatever ratio is applied is shown in
the header, because a hidden adjustment is worse than a noisy chip.

## Crash recovery

State is written to `draft_state.json` on every change and restored on boot.
Losing it mid-draft is the app's worst failure mode: the scraper repopulates
*who* is drafted but not which picks were **mine**, so needs and weighting would
silently reset to an empty roster while looking completely normal.

Guards: the snapshot is ignored if it belongs to another league or is more than
18 hours old, and a corrupt file is skipped rather than fatal. On a successful
resume the board shows a banner — check your roster before trusting it.

## Search

`/` focuses the box, `Esc` clears it. Search spans drafted players too, so
"has he gone yet?" is answerable; taken players appear struck through with the
team that took them.

Header chips lead with scarcity (`TE T1 87% gone by #40`), then raw tier
counts, then open slots. The **Weighted / Board** toggle turns all of this off
and shows your board exactly as written.

## Files

| File | Role |
|---|---|
| `ingest.py` | Rankings → ESPN player IDs → `board.json` |
| `espn.py` | API client; classifies 401 / 404 / network |
| `draft.py` | Snake math, roster needs, tier cliffs, survival |
| `server.py` | FastAPI + SSE, manual pick/undo |
| `static/index.html` | The board |
| `replay.py` | Rehearsal harness |
