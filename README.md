# 2026 Draft Overlord

Live draft board for **Boys Rule Girls Drule** — 10-team, full PPR, snake,
**no kicker**. Draft: **Sep 4 2026, 8:00pm EDT**.

Reads the draft from ESPN (exact player IDs) rather than reading the screen.
Runs locally in a browser tab beside the ESPN draft room.

- Lineup: `QB / RB / RB / WR / WR / WR-TE / TE / FLEX / D-ST` — 9 starters
- Roster 19 = **16 drafted rounds** + 3 IR · 7 bench · 160 total picks
- You are **team 1, draft slot 1**: picks 1, 20, 21, 40, 41, 60, 61, 80, 81,
  100, 101, 120, 121, 140, 141, 160 — the first overall pick, then back-to-back
  turns every round

---

# Draft night

```bash
cd ~/projects/2026_Draft_Overlord
python3 -m uvicorn server:app --port 8777
```

1. Open <http://127.0.0.1:8777>, tile it beside the ESPN draft room.
2. Paste snippet **A** from `draft_room_snippets.js` into the draft room's
   DevTools console (Cmd+Opt+J). `MY_TEAM` is prefilled with
   `G-Reg the 3rd Leg` — if the room renders your name differently, fix it,
   or the board can't tell your picks from anyone else's and every
   roster-aware feature quietly misbehaves.
3. Confirm the console logs `pushed N -> matched N` every couple of seconds.

**Run both transports.** Polling and the draft-room scraper feed the same board
and merge safely — an empty or lagging API can never erase scraped picks, and
the API wins where they disagree. Clicking rows by hand works on top of both.
Locked by `test_transports_merge_without_clobbering`.

### Controls

| Action | Effect |
|---|---|
| Click a row | Cross that player off (someone else took him) |
| Click `+` | Add him to **your** roster |
| `Undo` | Reverse the last manual action |
| `/` | Focus search · `Esc` clears |
| `Weighted` / `Board` | Toggle need+scarcity ordering vs. your raw board |
| slot dropdown | Override the auto-derived draft slot |

The **Recent picks** column on the right is your liveness check: if the age
clock in its header goes amber (>150s) or red (>400s) while the draft is
moving, a transport has died.

### If something breaks

- **Red connection dot** — the board still works. Cross players off by hand.
  That fallback is the whole safety story; nothing is lost.
- **Console says `bridge down: Failed to fetch`** — Chrome's Private Network
  Access. Set `chrome://flags/#block-insecure-private-network-requests` to
  **Disabled** and relaunch, or use the clipboard route (below).
- **Server crashed** — just restart it. State is on disk and resumes; a banner
  appears telling you to check your roster.
- **Nothing crossing off** — re-paste the snippet. The server restarting kills
  the old interval's target.

---

## One-time setup

**1. Install deps**

```bash
python3 -m pip install -r requirements.txt
```

**2. Build the board** (done; re-run if the rankings change)

```bash
python3 ingest.py \
  --overall rankings/2026_Draft_Board_Overall.xlsx \
  --positional rankings/2026_Draft_Board_Positional.xlsx --refresh
```

`--refresh` re-pulls ESPN's ranks and ADP, which move daily. **Run it the day
before the draft** — the legend flags Love, Jeanty, Jacobs and Conner as
volatile.

Writes `board.json`. **`unmatched.csv` must be empty** — an unmatched player
never gets crossed off, which is worse than no tool at all. Currently 193/193
match exactly.

**3. ESPN credentials**

```bash
python3 setup_config.py
```

Run it in your own terminal. It reads the cookies without echoing them, writes
`~/.config/draft-overlord/config.json` at `0600`, then verifies against ESPN and
prints your league's teams so you can confirm the right `team_id`. Paste your
team URL when prompted and it extracts `leagueId`/`teamId` for you.

Secrets live **outside** the repo on purpose — no `.gitignore` edit or careless
`git add -f` can leak a live session. `espn_s2` dies if you log out of ESPN or
change your password; re-run this if so.

---

## Rehearsing

```bash
python3 -m uvicorn server:app --port 8777 &
python3 replay.py --simulate --slot 1 --delay 1
```

Feeds a simulated draft in at one pick a second so you can watch the feed fill
and the ordering shift in the browser. It drafts on ADP alone, so its rosters
look silly (four QBs) — it exists to exercise the pipeline, not to draft well.

With cookies configured, replay a real finished draft:

```bash
python3 replay.py --from-league 2025 --slot 1
```

`/api/reset` returns the server to live polling afterwards.

Tests: `python3 -m pytest -q` (56) and `npm test` (jsdom UI render).

---

## Transports

### Draft-room bridge — proven

Scrapes the draft room DOM. Validated on a live mock: 160 picks, 100% name
match, and the pick list does not virtualise, so old picks stay readable.

Selector: `[class*="pick__message"] .playerinfo__playername`

Snippets are in **`draft_room_snippets.js`**:

- **A — direct**, needs the Chrome PNA flag disabled. No clipboard hijacking.
- **B — clipboard**, no browser settings required. Pair with
  `python3 clipboard_bridge.py`. The draft tab must stay focused.
- **C — re-discovery**, prints which DOM containers hold your players, in case
  ESPN changes its markup before Sunday.

Console `copy()` does **not** work inside `setInterval` — use
`navigator.clipboard.writeText`.

### API polling — unproven live

```bash
python3 watch.py --discover        # list leagues on the account
python3 watch.py --league <id>     # timestamp every pick as it lands
```

What's established:

- Real league drafts **are** persisted — 2023, 2024, 2025 all hold full pick
  data readable right now.
- ESPN **mock drafts are ephemeral**: a live mock reached 160 picks while every
  API view returned zero, then the league 404'd on completion. Mocks cannot
  answer this question; don't retry that experiment.
- A throwaway real league **also cannot**: ESPN refuses to start a draft while
  manager slots are unfilled.

So whether a real draft writes picks live or only at completion is still
unknown — and it no longer matters, because both transports run together and
merge safely.

---

## What the columns mean

- **Rank** — your board rank (overall, or positional inside a position tab).
- **safe / 50/50 / gone** — whether ESPN's *live* ADP says he survives to your
  next pick. Live ADP predicts an ESPN room better than a stale industry
  average. See `SAFE_MARGIN`, `GONE_MARGIN`.
- **+n** (green) — your `DELTA`: ESPN ranks him this many picks *later* than you
  do, so the room should let him fall. Value targets.
- **adp** — ESPN live ADP.
- **`starter` / `flex` / `depth`** — need weighting.
- **`scarce +n`** — tier scarcity.

Header chips lead with **runs**, then scarcity, then tier counts, then open
slots. The `Board` toggle turns all weighting off and shows your board exactly
as written.

## The model

Two adjustments to your board rank, both measured in ranks so they simply add.

**Need weighting** — once a position is covered it's pushed down, never removed:

| Situation | Penalty |
|---|---|
| Fills an empty starting slot | 0 |
| Only fills FLEX or WR-TE | `PENALTY_FLEX` 12 |
| Pure depth | `PENALTY_DEPTH` 45 |
| Each surplus body | `PENALTY_SURPLUS` 25 |

**Scarcity** — the cost of waiting is *how likely his tier is gone when I pick
again* × *how far the drop is to the next tier*:

- **P(tier gone)** — each member's chance of being taken by the horizon, from a
  logistic around his ADP (`ADP_SPREAD`), multiplied together.
- **Cliff** — ranks between the best in this tier and the best in the next.
- **Horizon** — the pick *after* your next one. The question is "take him now or
  get one next turn", so your back-to-back picks at 20/21 carry almost no
  urgency. That falls out of the model rather than being special-cased.

`PENALTY_DEPTH (45) > SCARCITY_CAP (35)` is deliberate and tested: scarcity may
promote a position you still need, but can never promote one you've covered.

**Positional runs** — `P(tier gone)` assumes independence, which is optimistic
exactly when it matters, because drafts go in bursts (the league ran 7 RBs in
one round in 2025). When a position leaves faster than the remaining pool
implies, its horizon stretches by that ratio.

Three guards stop noise becoming signal, all learned the hard way — the first
cut read a *single* TE pick as a 2.5× run, because scarce positions have a tiny
baseline:

- `RUN_MIN_COUNT` 3 — fewer picks is noise
- `RUN_MIN_EXPECTED` 1.0 — floors the denominator
- `RUN_MIN_RATIO` 1.25 — below this is ordinary variance

Whatever ratio is applied is shown in the header; a hidden adjustment is worse
than a noisy chip.

All constants are at the top of `draft.py`. If scarcity feels timid on the
night, raise `SCARCITY_DAMPING` (0.6) toward 1.0.

## Crash recovery

State is written to `draft_state.json` on every change and restored on boot.
Losing it mid-draft is the app's worst failure mode: the scraper repopulates
*who* is drafted but not which picks were **mine**, so needs and weighting would
silently reset to an empty roster while looking completely normal.

Guards: ignored if it belongs to another league or is >18h old; a corrupt file
is skipped rather than fatal; writes are atomic. Verified against a real
`SIGKILL`. On resume the board shows a banner — check your roster before
trusting it.

## Files

| File | Role |
|---|---|
| `ingest.py` | Rankings → ESPN player IDs → `board.json` |
| `espn.py` | API client; classifies 401 / 404 / network |
| `draft.py` | Snake math, needs, scarcity, runs — all tunables live here |
| `server.py` | FastAPI + SSE, manual pick/undo, scrape intake, persistence |
| `static/index.html` | The board |
| `setup_config.py` | Interactive credential setup + verification |
| `draft_room_snippets.js` | Console snippets A/B/C for the draft room |
| `clipboard_bridge.py` | Clipboard transport when PNA blocks direct POST |
| `watch.py` | Measures whether picks arrive live; `--discover` lists leagues |
| `replay.py` | Rehearsal harness (simulated or a real past season) |
| `test_draft.py` `test_espn.py` `test_server.py` | 56 tests |
| `test_ui.js` | jsdom render test — headless Chrome hangs on this machine |
