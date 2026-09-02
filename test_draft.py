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


def test_qb_deweighted_after_one_but_never_removed():
    """The explicit ask: taking a QB should push QBs down hard, not hide them."""
    from draft import apply_need_weights, need_penalty

    avail = [{"name": "qb2", "pos": "QB", "my_rank": 20},
             {"name": "rb5", "pos": "RB", "my_rank": 50}]

    before = roster_needs([], SLOTS)
    apply_need_weights(avail, before, SLOTS)
    assert avail[0]["need_tag"] == "starter" and avail[0]["adj_rank"] == 20

    after = roster_needs([P("myqb", "QB", 1)], SLOTS)
    apply_need_weights(avail, after, SLOTS)
    qb = avail[0]
    assert qb["need_tag"] == "depth"
    assert qb["adj_rank"] > 50, "second QB should now sort behind the RB"
    assert qb in avail, "de-weighted, never removed"


def test_rb_keeps_value_via_flex_after_starters_filled():
    """RB still fills FLEX, so it must not be penalised like a second QB."""
    from draft import apply_need_weights

    mine = [P("rb1", "RB", 1), P("rb2", "RB", 2)]
    needs = roster_needs(mine, SLOTS)
    avail = [{"name": "rb3", "pos": "RB", "my_rank": 30},
             {"name": "qb2", "pos": "QB", "my_rank": 30}]
    apply_need_weights(avail, needs, SLOTS)
    rb, qb = avail
    assert rb["need_tag"] == "flex"
    assert qb["need_tag"] == "starter"      # QB slot still empty here
    assert rb["adj_rank"] < qb["adj_rank"] + 100


def test_penalty_grows_with_surplus():
    from draft import need_penalty
    one = need_penalty("QB", roster_needs([P("a", "QB", 1)], SLOTS), SLOTS)[0]
    two = need_penalty("QB", roster_needs(
        [P("a", "QB", 1), P("b", "QB", 2)], SLOTS), SLOTS)[0]
    assert two > one, "stacking a third QB should hurt more than the second"


def test_weighting_is_stable_ordering():
    """Equal adjusted ranks fall back to board rank, so order never jitters."""
    from draft import apply_need_weights
    avail = [{"name": "b", "pos": "WR", "my_rank": 12},
             {"name": "a", "pos": "WR", "my_rank": 11}]
    needs = roster_needs([], SLOTS)
    apply_need_weights(avail, needs, SLOTS)
    ordered = sorted(avail, key=lambda p: (p["adj_rank"], p["my_rank"]))
    assert [p["name"] for p in ordered] == ["a", "b"]


# --- scarcity -------------------------------------------------------------

def TP(name, pos, rank, tier, adp):
    return {"name": name, "pos": pos, "my_rank": rank,
            "pos_tier": tier, "espn_live_adp": adp}


def test_p_taken_is_a_coin_flip_at_adp():
    from draft import p_taken_by
    assert abs(p_taken_by(50, 50) - 0.5) < 1e-9
    assert p_taken_by(50, 80) > 0.95      # long past his ADP
    assert p_taken_by(50, 20) < 0.05      # well before it
    assert p_taken_by(None, 50) == 0.0


def test_horizon_is_the_pick_after_next():
    from draft import horizon_pick
    # slot 1 of 10 picks 1, 20, 21, 40...
    assert horizon_pick(1, 10, 16, picks_made=0) == 20   # on clock at 1, next is 20
    assert horizon_pick(1, 10, 16, picks_made=19) == 21  # on clock at 20, next is 21
    assert horizon_pick(1, 10, 16, picks_made=20) == 40  # on clock at 21, next is 40
    assert horizon_pick(None, 10, 16, 0) is None


def test_back_to_back_picks_kill_urgency():
    """Slot 1 holds 20 and 21. Almost nothing can happen between them."""
    from draft import tier_scarcity, horizon_pick
    tier = [TP(f"rb{i}", "RB", 30 + i, 3, 34.0) for i in range(2)]
    nxt = [TP(f"rb{i}", "RB", 60 + i, 4, 70.0) for i in range(3)]

    at20 = horizon_pick(1, 10, 16, picks_made=19)   # -> 21, one pick away
    at21 = horizon_pick(1, 10, 16, picks_made=20)   # -> 40, nineteen away
    near = tier_scarcity(tier + nxt, at20)[("RB", 3)]
    far = tier_scarcity(tier + nxt, at21)[("RB", 3)]
    assert near["bonus"] < far["bonus"]
    assert near["bonus"] < 1.0, "back-to-back turns should carry ~no urgency"


def test_thin_tier_beats_deep_tier():
    from draft import tier_scarcity
    thin = [TP("te1", "TE", 20, 1, 25.0)]
    thin_next = [TP("te2", "TE", 60, 2, 65.0)]
    deep = [TP(f"wr{i}", "WR", 20 + i, 1, 25.0) for i in range(6)]
    deep_next = [TP("wrx", "WR", 60, 2, 65.0)]
    s = tier_scarcity(thin + thin_next + deep + deep_next, 45)
    assert s[("TE", 1)]["p_gone"] > s[("WR", 1)]["p_gone"]
    assert s[("TE", 1)]["bonus"] > s[("WR", 1)]["bonus"]


def test_bonus_scales_with_the_cliff():
    """Same survival odds, bigger drop to the next tier => more urgency."""
    from draft import tier_scarcity
    small = [TP("a", "RB", 10, 1, 12.0), TP("b", "RB", 14, 2, 40.0)]
    big = [TP("a", "WR", 10, 1, 12.0), TP("b", "WR", 60, 2, 40.0)]
    s = tier_scarcity(small + big, 40)
    assert s[("WR", 1)]["cliff"] > s[("RB", 1)]["cliff"]
    assert s[("WR", 1)]["bonus"] > s[("RB", 1)]["bonus"]


def test_scarcity_is_capped_and_never_negative():
    from draft import tier_scarcity, SCARCITY_CAP
    cliffy = [TP("a", "TE", 5, 1, 6.0), TP("b", "TE", 400, 2, 300.0)]
    s = tier_scarcity(cliffy, 200)[("TE", 1)]
    assert 0 <= s["bonus"] <= SCARCITY_CAP


def test_scarcity_can_outrank_a_flex_penalty_but_not_invert_reality():
    """A vanishing tier should be able to jump a mild flex discount."""
    from draft import apply_need_weights, tier_scarcity
    mine = [P("rb1", "RB", 1), P("rb2", "RB", 2)]
    needs = roster_needs(mine, SLOTS)
    avail = [TP("scarce_rb", "RB", 40, 3, 42.0),
             TP("next_rb", "RB", 90, 4, 95.0),
             TP("plain_te", "TE", 38, 2, 120.0),
             TP("te_next", "TE", 50, 3, 130.0)]
    sc = tier_scarcity(avail, 60)
    apply_need_weights(avail, needs, SLOTS, sc)
    rb = next(p for p in avail if p["name"] == "scarce_rb")
    te = next(p for p in avail if p["name"] == "plain_te")
    assert rb["scarcity"] > te["scarcity"]
    assert rb["adj_rank"] < rb["my_rank"] + rb["need_penalty"]


def test_no_horizon_means_no_scarcity():
    from draft import apply_need_weights
    avail = [TP("a", "RB", 10, 1, 12.0)]
    apply_need_weights(avail, roster_needs([], SLOTS), SLOTS, {})
    assert avail[0]["scarcity"] == 0
    assert avail[0]["adj_rank"] == 10


# --- positional runs ------------------------------------------------------

def test_no_run_signal_from_too_few_picks():
    from draft import run_ratios
    avail = [TP(f"rb{i}", "RB", i, 1, 20.0) for i in range(10)]
    assert run_ratios(["RB"] * 3, avail) == {}


def test_detects_a_position_run():
    """The league's own history: 7 RBs in a round."""
    from draft import run_ratios
    # Pool is a third RB, so a neutral draft takes ~4 RBs in 12 picks.
    avail = ([TP(f"rb{i}", "RB", i, 1, 20.0) for i in range(14)]
             + [TP(f"wr{i}", "WR", 50 + i, 1, 60.0) for i in range(26)])
    window = ["RB"] * 8 + ["WR"] * 4
    r = run_ratios(window, avail)
    assert r["RB"] > 1.5, r
    assert "WR" not in r, "WR is going slower than supply, not a run"


def test_run_ratio_is_capped():
    from draft import run_ratios, RUN_MAX_RATIO
    avail = ([TP("rb", "RB", 1, 1, 20.0)]
             + [TP(f"wr{i}", "WR", 10 + i, 1, 60.0) for i in range(39)])
    r = run_ratios(["RB"] * 12, avail)
    assert r["RB"] == RUN_MAX_RATIO


def test_run_raises_p_gone_and_bonus():
    """The whole point: a run makes the independence assumption less wrong."""
    from draft import tier_scarcity
    tier = [TP(f"rb{i}", "RB", 30 + i, 3, 55.0) for i in range(3)]
    nxt = [TP("rb9", "RB", 80, 4, 95.0)]
    calm = tier_scarcity(tier + nxt, horizon=60, picks_made=40)[("RB", 3)]
    hot = tier_scarcity(tier + nxt, horizon=60, picks_made=40,
                        runs={"RB": 2.0})[("RB", 3)]
    assert hot["p_gone"] > calm["p_gone"]
    assert hot["bonus"] > calm["bonus"]
    assert hot["run"] == 2.0 and calm["run"] == 1.0


def test_run_cannot_pull_the_horizon_backwards():
    """A ratio of 1.0 must be identical to no run at all."""
    from draft import tier_scarcity
    tier = [TP("a", "TE", 20, 1, 30.0), TP("b", "TE", 60, 2, 70.0)]
    base = tier_scarcity(tier, horizon=50, picks_made=30)[("TE", 1)]
    same = tier_scarcity(tier, horizon=50, picks_made=30, runs={"TE": 1.0})[("TE", 1)]
    assert base["p_gone"] == same["p_gone"]


def test_a_single_pick_is_never_a_run():
    """Scarce positions have a tiny baseline; one pick must not read as 2.5x."""
    from draft import run_ratios
    avail = ([TP(f"rb{i}", "RB", i, 1, 20.0) for i in range(20)]
             + [TP(f"wr{i}", "WR", 30 + i, 1, 40.0) for i in range(19)]
             + [TP("te1", "TE", 70, 1, 80.0)])          # TE is 1/40 of the pool
    window = ["RB"] * 6 + ["WR"] * 5 + ["TE"]           # exactly one TE
    r = run_ratios(window, avail)
    assert "TE" not in r, f"one TE pick invented a run: {r}"


def test_three_picks_at_a_scarce_position_is_a_real_run():
    """The pick 73-92 TE run the league legend describes."""
    from draft import run_ratios
    avail = ([TP(f"rb{i}", "RB", i, 1, 20.0) for i in range(20)]
             + [TP(f"wr{i}", "WR", 30 + i, 1, 40.0) for i in range(19)]
             + [TP("te1", "TE", 70, 1, 80.0)])
    window = ["RB"] * 5 + ["WR"] * 4 + ["TE"] * 3
    r = run_ratios(window, avail)
    assert r.get("TE", 0) > 1.5, r


def test_common_position_needs_real_excess_not_just_presence():
    from draft import run_ratios
    # RB is a third of the pool, so 4 of 12 is exactly par -- not a run.
    avail = ([TP(f"rb{i}", "RB", i, 1, 20.0) for i in range(13)]
             + [TP(f"wr{i}", "WR", 30 + i, 1, 40.0) for i in range(27)])
    assert "RB" not in run_ratios(["RB"] * 4 + ["WR"] * 8, avail)
    assert run_ratios(["RB"] * 9 + ["WR"] * 3, avail).get("RB", 0) > 1.5


def test_depth_can_never_be_promoted_by_scarcity():
    """Structural guarantee: PENALTY_DEPTH exceeds SCARCITY_CAP.

    A position I've already covered must never outrank its own board slot,
    no matter how fast its tier is vanishing. Scarcity may promote a needed
    position -- that is the point -- but never a redundant one.
    """
    from draft import PENALTY_DEPTH, SCARCITY_CAP, apply_need_weights, tier_scarcity
    assert PENALTY_DEPTH > SCARCITY_CAP

    mine = [P("qb", "QB", 1, )] if False else [P("qb", "QB", 1)]
    needs = roster_needs(mine, SLOTS)
    avail = [TP("qb2", "QB", 40, 1, 41.0), TP("qb3", "QB", 95, 2, 200.0)]
    sc = tier_scarcity(avail, horizon=60, picks_made=40, runs={"QB": 2.5})
    apply_need_weights(avail, needs, SLOTS, sc)
    for p in avail:
        if p["need_tag"] == "depth":
            assert p["adj_rank"] > p["my_rank"], p
