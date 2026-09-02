# Draft Overlord — working notes

Local draft board for a 10-team full-PPR ESPN snake league. Draft Sep 4 2026 8pm EDT.

## Commands

```bash
python3 -m pytest -q                  # 18 python tests
npm test                              # jsdom render test of static/index.html
python3 -m uvicorn server:app --port 8777
python3 replay.py --simulate --slot 3 # rehearse against a running server
python3 ingest.py --overall rankings/*Overall.xlsx --positional rankings/*Positional.xlsx --refresh
```

## Non-obvious constraints

- **Secrets live outside the repo** at `~/.config/draft-overlord/config.json`.
  Never add a config path inside the working tree. `DRAFT_OVERLORD_CONFIG`
  overrides it for tests only.
- **The board must work with zero ESPN connectivity.** Polling only automates
  crossing players off. Any change that makes the UI depend on a live
  connection is a regression — that fallback is the whole safety story.
- **`unmatched.csv` must be empty** after `ingest.py`. An unmatched player never
  gets crossed off, which is worse than having no tool.
- **No kicker in this league**, and there's a `WR/TE` slot (ESPN lineup slot 5)
  plus a `FLEX` (slot 23). Don't assume a standard lineup.
- Picks for players outside the 193-man board (deep sleepers) are expected and
  must not break pick numbering — they count toward `drafted_count` only.
- Headless Chrome hangs on this machine; UI testing goes through jsdom.
  macOS has no `timeout` command.

## Layout

`ingest.py` rankings→ESPN ids · `espn.py` API client · `draft.py` snake/tier/needs
· `server.py` FastAPI+SSE · `static/index.html` board · `replay.py` rehearsal
