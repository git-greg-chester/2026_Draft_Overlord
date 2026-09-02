"""Checks for the pieces that are easy to get subtly wrong."""

from draft import my_pick_numbers, next_pick, pick_number, roster_needs, survival, tier_cliffs

# Our league: QB/RB/RB/WR/WR/WR-TE/TE/FLEX/D-ST + 7 bench = 16
SLOTS = {"QB": 1, "RB": 2, "WR": 2, "WR/TE": 1, "TE": 1, "FLEX": 1, "DST": 1, "BE": 7}
T = 10


def P(name, pos, rank):
    return {"name": name, "pos": pos, "my_rank": rank}


def test_snake_turns():
    assert pick_number(1, 3, T) == 3
    assert pick_number(2, 3, T) == 18      # snake back
    assert pick_number(3, 3, T) == 23
    assert pick_number(1, 1, T) == 1
    assert pick_number(2, 1, T) == 20      # slot 1 gets last pick of round 2
    assert pick_number(2, 10, T) == 11     # slot 10 gets back-to-back 10 & 11


def test_every_pick_used_once():
    seen = [n for s in range(1, T + 1) for n in my_pick_numbers(s, T, 16)]
    assert sorted(seen) == list(range(1, T * 16 + 1))


def test_next_pick_skips_past():
    # slot 3: picks 3, 18, 23, 38...
    assert next_pick(3, T, 16, picks_made=0) == 3
    assert next_pick(3, T, 16, picks_made=3) == 18
    assert next_pick(3, T, 16, picks_made=17) == 18
    assert next_pick(3, T, 16, picks_made=18) == 23
    assert next_pick(3, T, 16, picks_made=160) is None


def test_restrictive_slots_fill_first():
    # One TE only. He must land in TE, not be eaten by FLEX or WR/TE.
    mine = [P("rb1", "RB", 1), P("rb2", "RB", 2), P("wr1", "WR", 3),
            P("wr2", "WR", 4), P("te1", "TE", 5)]
    n = roster_needs(mine, SLOTS)
    assert "TE" not in n["open_slots"], n["open_slots"]
    # WR/TE and FLEX still open, QB and DST still open
    assert n["open_slots"].get("QB") == 1
    assert n["open_slots"].get("DST") == 1
    assert "QB" in n["needed_positions"]


def test_full_starting_lineup_leaves_nothing_open():
    mine = [P("qb", "QB", 1), P("rb1", "RB", 2), P("rb2", "RB", 3),
            P("wr1", "WR", 4), P("wr2", "WR", 5), P("wr3", "WR", 6),
            P("te", "TE", 7), P("flex", "RB", 8), P("dst", "DST", 9)]
    n = roster_needs(mine, SLOTS)
    assert n["open_slots"] == {}, n["open_slots"]
    assert n["bench_players"] == 0


def test_extras_go_to_bench():
    mine = [P(f"wr{i}", "WR", i) for i in range(1, 8)]
    n = roster_needs(mine, SLOTS)
    # WR, WR, WR/TE, FLEX absorb 4; rest benched
    assert n["bench_players"] == 3, n


def test_survival_buckets():
    nxt = 25
    assert survival({"espn_live_adp": 40}, nxt) == "safe"
    assert survival({"espn_live_adp": 15}, nxt) == "gone"
    assert survival({"espn_live_adp": 27}, nxt) == "coinflip"
    assert survival({"espn_live_adp": None}, nxt) == "unknown"
    assert survival({"espn_live_adp": 40}, None) == "unknown"


def test_live_adp_beats_stale_industry():
    p = {"espn_live_adp": 40, "industry_adp": 12}
    assert survival(p, 25) == "safe"


def test_cliff_detection():
    avail = [
        {"name": "a", "pos": "TE", "my_rank": 1, "pos_tier": 1, "pos_tier_name": "elite"},
        {"name": "b", "pos": "TE", "my_rank": 2, "pos_tier": 1, "pos_tier_name": "elite"},
        {"name": "c", "pos": "RB", "my_rank": 3, "pos_tier": 2, "pos_tier_name": "solid"},
    ] + [
        {"name": f"r{i}", "pos": "RB", "my_rank": 10 + i, "pos_tier": 2, "pos_tier_name": "solid"}
        for i in range(5)
    ]
    cliffs = {c["pos"]: c for c in tier_cliffs(avail)}
    assert cliffs["TE"]["remaining"] == 2 and cliffs["TE"]["cliff"] is True
    assert cliffs["RB"]["remaining"] == 6 and cliffs["RB"]["cliff"] is False
    # cliffs sort first
    assert tier_cliffs(avail)[0]["pos"] == "TE"


def test_transports_merge_without_clobbering():
    """Sunday runs polling and the draft-room scraper at once.

    An empty or lagging API must never erase scraped picks, and a working
    API must win where the two disagree about who took a player.
    """
    import server

    S = server.State()
    S.board = [{"espn_id": i, "name": f"p{i}", "pos": "RB", "my_rank": i} for i in (1, 2, 3)]

    # Scraper is ahead, API has nothing yet -> scraped survives.
    S.scraped = {1: 0, 2: 0}
    S.api = {}
    assert S.drafted() == {1: 0, 2: 0}

    # API catches up and knows the actual team -> API wins on overlap.
    S.api = {1: 7}
    assert S.drafted() == {1: 7, 2: 0}

    # Manual cross-off fills a gap neither transport has.
    S.manual = {3: 0}
    assert S.drafted() == {3: 0, 1: 7, 2: 0}

    # API briefly returning nothing must not wipe the board mid-draft.
    S.api = {}
    assert S.drafted() == {3: 0, 1: 0, 2: 0}
