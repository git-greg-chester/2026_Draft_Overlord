"""Snake math, roster needs, tier cliffs, and survival buckets.

The board itself is the ranking; nothing here re-ranks players. This only
answers "given who's gone and what I already have, what should I notice?"
"""

from espn import SLOT_ELIGIBLE, starters_from_slots

# Survival thresholds, in picks, relative to my next pick.
# Tunable: widen SAFE if the room reaches more than ESPN ADP suggests.
SAFE_MARGIN = 10      # ADP at least this far after my pick -> should still be there
GONE_MARGIN = -5      # ADP this far before my pick -> expect him gone
CLIFF_THRESHOLD = 2   # <= this many left in a tier -> warn

# Need weighting, expressed as ranks-worth of penalty added to my_rank.
# A player who can still fill an empty starting slot is untouched. One who
# only adds depth is pushed down hard but never removed -- a backup QB in
# round 12 is still a real pick.
PENALTY_FLEX = 12       # fills only FLEX / WR-TE, not a dedicated slot
PENALTY_DEPTH = 45      # pure bench depth at a position already covered
PENALTY_SURPLUS = 25    # per extra body beyond the first backup

# Scarcity. The value of a tier rises as it empties, because the real cost of
# waiting is the drop to the next tier weighted by the chance nothing survives.
ADP_SPREAD = 9.0        # picks of noise around a player's ADP (logistic scale)
SCARCITY_DAMPING = 0.6  # trust in the estimate; 1.0 would take it at face value
SCARCITY_CAP = 35       # never let scarcity alone leap a player more than this
DEFAULT_CLIFF = 30      # assumed drop when a position has no next tier left

# Positional runs. P(tier gone) multiplies independent probabilities, which is
# optimistic exactly when it matters: drafts go in bursts. The league's own
# history says so -- "7 RBs in round 2 in 2025". When a position is coming off
# the board faster than the remaining pool implies, stretch its horizon.
RUN_WINDOW = 12         # picks of history that count as "recent"
RUN_MIN_PICKS = 6       # below this there's no signal, only noise
RUN_POOL = 40           # upcoming players that define the natural supply rate
RUN_MAX_RATIO = 2.5     # cap: never assume more than 2.5x the normal rate
# Scarce positions have a tiny baseline share, so a single pick would divide
# by almost nothing and invent a run. Both guards exist to stop that: a real
# run needs several picks, and the expected count never falls below one.
RUN_MIN_COUNT = 3       # picks of a position before it counts as a run
RUN_MIN_EXPECTED = 1.0  # floor on the denominator
RUN_MIN_RATIO = 1.25    # below this it's ordinary variance, not a run


def pick_number(round_num: int, slot: int, team_count: int) -> int:
    """1-indexed overall pick for a snake draft."""
    if round_num % 2 == 1:
        return (round_num - 1) * team_count + slot
    return round_num * team_count - slot + 1


def my_pick_numbers(slot: int, team_count: int, rounds: int) -> list[int]:
    return [pick_number(r, slot, team_count) for r in range(1, rounds + 1)]


def next_pick(slot: int, team_count: int, rounds: int, picks_made: int) -> int | None:
    """Next pick strictly after the picks already made."""
    for n in my_pick_numbers(slot, team_count, rounds):
        if n > picks_made:
            return n
    return None


def slot_from_draft_order(draft_order: list[int], team_id: int) -> int | None:
    """1-indexed position in the first round."""
    if team_id in draft_order:
        return draft_order.index(team_id) + 1
    return None


def effective_adp(player: dict) -> float | None:
    """ESPN's live ADP predicts an ESPN room better than a stale industry average."""
    for key in ("espn_live_adp", "industry_adp"):
        v = player.get(key)
        if v:
            return float(v)
    return None


def survival(player: dict, my_next: int | None) -> str:
    if my_next is None:
        return "unknown"
    adp = effective_adp(player)
    if adp is None:
        return "unknown"
    margin = adp - my_next
    if margin >= SAFE_MARGIN:
        return "safe"
    if margin <= GONE_MARGIN:
        return "gone"
    return "coinflip"


def roster_needs(my_players: list[dict], slot_counts: dict[str, int]) -> dict:
    """Greedily fill starting slots with what I have; report what's still open.

    Fills the most restrictive slots first so a flex-eligible player doesn't get
    consumed by FLEX while a dedicated slot sits empty.
    """
    starters = starters_from_slots(slot_counts)
    remaining = sorted(my_players, key=lambda p: p.get("my_rank", 9999))
    open_slots: dict[str, int] = {}

    order = sorted(starters, key=lambda s: len(SLOT_ELIGIBLE.get(s, {s})))
    for slot in order:
        need = starters[slot]
        eligible = SLOT_ELIGIBLE.get(slot, {slot})
        for _ in range(need):
            hit = next((p for p in remaining if p["pos"] in eligible), None)
            if hit:
                remaining.remove(hit)
            else:
                open_slots[slot] = open_slots.get(slot, 0) + 1

    counts: dict[str, int] = {}
    for p in my_players:
        counts[p["pos"]] = counts.get(p["pos"], 0) + 1

    # Positions that could still fill an open slot.
    needed_pos: set[str] = set()
    for slot in open_slots:
        needed_pos |= SLOT_ELIGIBLE.get(slot, {slot})

    return {
        "counts": counts,
        "open_slots": open_slots,
        "needed_positions": sorted(needed_pos),
        "bench_players": len(remaining),
    }


def need_penalty(pos: str, needs: dict, slot_counts: dict[str, int]) -> tuple[int, str]:
    """How far to push a position down once my starters there are covered.

    Returns (penalty in ranks, short reason for the UI). Never removes a
    player -- depth still has value, it just shouldn't outrank a starter.
    """
    open_slots = needs.get("open_slots") or {}
    counts = needs.get("counts") or {}

    # Still fills a dedicated starting slot -> full value.
    for slot, n in open_slots.items():
        if n and SLOT_ELIGIBLE.get(slot, {slot}) == {pos}:
            return 0, "starter"

    # Fills an open flex-ish slot -> mild discount, since a better position
    # might want that slot instead.
    for slot, n in open_slots.items():
        if n and pos in SLOT_ELIGIBLE.get(slot, {slot}) and len(SLOT_ELIGIBLE.get(slot, {slot})) > 1:
            return PENALTY_FLEX, "flex"

    # Nothing left to start him in. Penalise, and more for each extra body.
    started = sum(n for s, n in starters_from_slots(slot_counts).items()
                  if pos in SLOT_ELIGIBLE.get(s, {s}))
    surplus = max(0, counts.get(pos, 0) - max(1, started))
    return PENALTY_DEPTH + surplus * PENALTY_SURPLUS, "depth"


def p_taken_by(adp: float | None, pick_no: int) -> float:
    """Chance a player is gone by `pick_no`, given his ADP.

    ADP is a mean, not a certainty, so this is a logistic around it rather
    than a step. A player whose ADP equals the pick is a coin flip.
    """
    if adp is None or pick_no is None:
        return 0.0
    import math
    return 1.0 / (1.0 + math.exp(-(pick_no - adp) / ADP_SPREAD))


def horizon_pick(my_slot: int | None, team_count: int, rounds: int,
                 picks_made: int) -> int | None:
    """The pick by which I must have acted: my turn *after* the next one.

    The decision at any turn is "take him now, or hope he lasts until I pick
    again". Back-to-back turns (slot 1 gets 20 and 21) therefore carry almost
    no scarcity urgency, which is correct.
    """
    if not my_slot:
        return None
    mine = [n for n in my_pick_numbers(my_slot, team_count, rounds) if n > picks_made]
    if len(mine) >= 2:
        return mine[1]
    return mine[0] if mine else None


def run_ratios(recent_positions: list[str], available: list[dict]) -> dict[str, float]:
    """How much faster than normal each position is coming off the board.

    Baseline is the position's share of the next `RUN_POOL` players by board
    rank -- i.e. what a neutral draft would consume. A ratio of 2.0 means the
    room is taking that position twice as fast as supply implies.
    """
    if len(recent_positions) < RUN_MIN_PICKS:
        return {}
    window = recent_positions[-RUN_WINDOW:]
    pool = sorted(available, key=lambda p: p.get("my_rank", 9999))[:RUN_POOL]
    if not pool:
        return {}

    out: dict[str, float] = {}
    for pos in set(window):
        count = window.count(pos)
        if count < RUN_MIN_COUNT:
            continue        # one or two picks is noise, not a run
        share = sum(1 for p in pool if p["pos"] == pos) / len(pool)
        expected = max(share * len(window), RUN_MIN_EXPECTED)
        ratio = count / expected
        if ratio < RUN_MIN_RATIO:
            continue        # ordinary variance around the expected rate
        out[pos] = min(RUN_MAX_RATIO, ratio)
    return out


def tier_scarcity(available: list[dict], horizon: int | None,
                  picks_made: int = 0, runs: dict[str, float] | None = None) -> dict:
    """Per (position, tier): how likely it empties, and what the drop costs.

    Returns {(pos, tier): {"p_gone", "cliff", "bonus", "remaining"}}.
    """
    if horizon is None:
        return {}
    runs = runs or {}

    by_pos: dict[str, list[dict]] = {}
    for p in available:
        by_pos.setdefault(p["pos"], []).append(p)

    out: dict[tuple, dict] = {}
    for pos, players in by_pos.items():
        players.sort(key=lambda p: p.get("my_rank", 9999))
        tiers: dict[int, list[dict]] = {}
        for p in players:
            t = p.get("pos_tier")
            if t is not None:
                tiers.setdefault(t, []).append(p)

        # A run stretches this position's horizon: if RBs are going twice as
        # fast as supply implies, the next 20 picks eat 40 picks' worth of RBs.
        ratio = runs.get(pos, 1.0)
        eff_horizon = picks_made + (horizon - picks_made) * ratio

        ordered = sorted(tiers)
        for i, t in enumerate(ordered):
            members = tiers[t]
            # Every member gone => the tier is gone. Independence is a
            # simplification; the run ratio above is the correction for it.
            p_gone = 1.0
            for m in members:
                p_gone *= p_taken_by(effective_adp(m), eff_horizon)

            best_now = members[0].get("my_rank", 9999)
            nxt = tiers[ordered[i + 1]][0].get("my_rank") if i + 1 < len(ordered) else None
            cliff = (nxt - best_now) if nxt is not None else DEFAULT_CLIFF
            cliff = max(0, cliff)

            bonus = min(SCARCITY_CAP, p_gone * cliff * SCARCITY_DAMPING)
            out[(pos, t)] = {"p_gone": p_gone, "cliff": cliff, "bonus": bonus,
                             "remaining": len(members), "run": round(ratio, 2)}
    return out


def apply_need_weights(available: list[dict], needs: dict,
                       slot_counts: dict[str, int],
                       scarcity: dict | None = None) -> None:
    """Annotate each available player with a need-adjusted rank, in place."""
    cache: dict[str, tuple[int, str]] = {}
    scarcity = scarcity or {}
    for p in available:
        pos = p["pos"]
        if pos not in cache:
            cache[pos] = need_penalty(pos, needs, slot_counts)
        penalty, reason = cache[pos]
        sc = scarcity.get((pos, p.get("pos_tier")), {})
        bonus = sc.get("bonus", 0.0)
        p["need_penalty"] = penalty
        p["need_tag"] = reason
        p["scarcity"] = round(bonus, 1)
        p["p_tier_gone"] = round(sc.get("p_gone", 0.0), 3)
        # Both terms are in ranks, so they simply add.
        p["adj_rank"] = round(p.get("my_rank", 9999) + penalty - bonus, 1)


def tier_cliffs(available: list[dict]) -> list[dict]:
    """For each position, how many players remain in the best available tier."""
    out = []
    by_pos: dict[str, list[dict]] = {}
    for p in available:
        by_pos.setdefault(p["pos"], []).append(p)

    for pos, players in by_pos.items():
        players.sort(key=lambda p: p.get("my_rank", 9999))
        top = players[0]
        tier = top.get("pos_tier")
        if tier is None:
            continue
        left = [p for p in players if p.get("pos_tier") == tier]
        out.append(
            {
                "pos": pos,
                "tier": tier,
                "tier_name": top.get("pos_tier_name"),
                "remaining": len(left),
                "cliff": len(left) <= CLIFF_THRESHOLD,
                "best": top["name"],
            }
        )
    out.sort(key=lambda x: (not x["cliff"], x["remaining"]))
    return out


def value_targets(available: list[dict], my_next: int | None, limit: int = 8) -> list[dict]:
    """Players the room should let fall past my pick (positive DELTA), still here."""
    picks = [p for p in available if (p.get("delta") or 0) > 0]
    picks.sort(key=lambda p: (-(p.get("delta") or 0), p.get("my_rank", 9999)))
    return picks[:limit]


def build_view(board: list[dict], drafted: dict[int, int], my_team_id: int,
               state, my_slot: int | None,
               recent_positions: list[str] | None = None) -> dict:
    """Assemble everything the UI needs in one payload.

    drafted maps espn_id -> team_id.
    """
    available, mine, gone = [], [], []
    for p in board:
        owner = drafted.get(p["espn_id"])
        if owner is None:
            available.append(p)
        else:
            gone.append({**p, "drafted_by": owner})
            if owner == my_team_id:
                mine.append(p)

    available.sort(key=lambda p: p.get("my_rank", 9999))
    mine.sort(key=lambda p: p.get("my_rank", 9999))

    picks_made = len(drafted)
    rounds = state.draft_rounds if state else 16
    nxt = next_pick(my_slot, state.team_count, rounds, picks_made) if my_slot else None

    for p in available:
        p["survival"] = survival(p, nxt)

    slot_counts = state.slot_counts if state else {}
    needs = roster_needs(mine, slot_counts)
    horizon = horizon_pick(my_slot, state.team_count if state else 10,
                           rounds, picks_made)
    runs = run_ratios(recent_positions or [], available)
    scarcity = tier_scarcity(available, horizon, picks_made, runs)
    apply_need_weights(available, needs, slot_counts, scarcity)
    return {
        # Each player carries adj_rank; the client sorts, so the payload
        # doesn't ship the same 193 players twice on every pick.
        "available": available,
        "mine": mine,
        "drafted_count": picks_made,
        "next_pick": nxt,
        "picks_until": (nxt - picks_made - 1) if nxt else None,
        "on_the_clock": picks_made + 1,
        "needs": needs,
        "cliffs": tier_cliffs(available),
        "targets": value_targets(available, nxt),
        "teams": state.teams if state else {},
        # Slim rows so search can answer "has he gone yet?" without a lookup.
        "taken": [{"espn_id": p["espn_id"], "name": p["name"], "pos": p["pos"],
                   "team": p["team"], "my_rank": p.get("my_rank"),
                   "by": p.get("drafted_by")} for p in gone],
        "rounds": rounds,
        "horizon": horizon,
        "runs": [{"pos": p, "ratio": round(r, 2),
                  "recent": (recent_positions or [])[-RUN_WINDOW:].count(p),
                  "window": min(RUN_WINDOW, len(recent_positions or []))}
                 # Show exactly what is being applied -- a hidden adjustment
                 # is worse than a noisy chip.
                 for p, r in sorted(runs.items(), key=lambda kv: -kv[1])],
        "scarcity": [
            {"pos": pos, "tier": t, **v}
            for (pos, t), v in sorted(scarcity.items(), key=lambda kv: -kv[1]["bonus"])
            if v["bonus"] >= 1
        ][:6],
    }
