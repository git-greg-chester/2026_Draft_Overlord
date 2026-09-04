"""Server-side pick ordering, which the recent-picks feed depends on."""

import server


def setup_function():
    server.S = server.State()
    server.S.board = [
        {"espn_id": 1, "name": "Alpha", "pos": "RB", "team": "DET", "my_rank": 1},
        {"espn_id": 2, "name": "Bravo", "pos": "WR", "team": "CIN", "my_rank": 2},
        {"espn_id": 3, "name": "Delta", "pos": "TE", "team": "LV", "my_rank": 3},
    ]
    server.S.by_id = {b["espn_id"]: b for b in server.S.board}
    server.S.by_norm = {server.normalize(b["name"]): b for b in server.S.board}


def test_detects_oldest_first_list():
    """New picks appended at the tail -> list reads oldest to newest."""
    S = server.S
    assert server.oldest_first([1], ["Alpha"]) == [1]
    out = server.oldest_first([1, 2], ["Alpha", "Bravo"])
    assert S.scrape_newest_first is False
    assert out == [1, 2]


def test_detects_newest_first_list():
    """New picks prepended at the head -> list reads newest to oldest."""
    S = server.S
    server.oldest_first([1], ["Alpha"])
    out = server.oldest_first([2, 1], ["Bravo", "Alpha"])
    assert S.scrape_newest_first is True
    assert out == [1, 2], "returned in draft order regardless of render order"


def test_direction_is_sticky_once_learned():
    S = server.S
    server.oldest_first([1], ["Alpha"])
    server.oldest_first([2, 1], ["Bravo", "Alpha"])
    assert S.scrape_newest_first is True
    # A later ambiguous sample must not flip it back.
    server.oldest_first([3, 2, 1], ["Delta", "Bravo", "Alpha"])
    assert S.scrape_newest_first is True


def test_recent_is_newest_first_with_pick_numbers():
    S = server.S
    S.api_order = [1, 2, 3]
    S.api = {1: 5, 2: 5, 3: 7}
    S.state.teams = {5: "Them", 7: "Us"}
    r = S.recent()
    assert [x["pick"] for x in r] == [3, 2, 1], "newest first for the feed"
    assert r[0]["name"] == "Delta" and r[0]["by"] == "Us"
    assert r[-1]["name"] == "Alpha"


def test_recent_marks_my_picks():
    S = server.S
    S.cfg = server.Config(team_id=7)
    S.api_order = [1, 2]
    S.api = {1: 5, 2: 7}
    r = S.recent()
    assert r[0]["mine"] is True and r[1]["mine"] is False


def test_recent_falls_back_to_scraped_order():
    S = server.S
    S.scrape_order = [2, 1]
    S.scraped = {1: 0, 2: 0}
    assert [x["name"] for x in S.recent()] == ["Alpha", "Bravo"]


def test_recent_ignores_players_off_the_board():
    """Kickers and deep sleepers have no board row; they must not crash it."""
    S = server.S
    S.api_order = [1, 999, 2]
    S.api = {1: 5, 999: 5, 2: 5}
    names = [x["name"] for x in S.recent()]
    assert names == ["Bravo", "Alpha"]


def test_scrape_dedupes_a_name_seen_twice_in_one_poll():
    """ESPN's draft room renders each pick in two places at once (a "recently
    drafted" ticker plus the full pick log), both matching our selector, so
    one scrape can carry the same name twice. found{} absorbed that silently
    (it's keyed by espn_id) but scrape_order didn't -- every pick doubled up
    in the recent-picks feed. A player is only ever drafted once."""
    S = server.S
    r = server.scrape(server.ScrapeIn(names=["Alpha", "Alpha", "Bravo", "Bravo"]))
    assert r["matched"] == 2
    assert S.scrape_order == [1, 2]
    assert [x["name"] for x in S.recent()] == ["Bravo", "Alpha"]


def test_scrape_dedup_is_case_and_space_insensitive():
    """Dedup runs on the normalized name, same as the board match itself."""
    S = server.S
    r = server.scrape(server.ScrapeIn(names=["alpha", "Alpha ", "Bravo"]))
    assert r["matched"] == 2
    assert S.scrape_order == [1, 2]


# --- crash persistence ----------------------------------------------------

def test_state_survives_a_restart(tmp_path):
    """The real failure mode: losing which picks were mine."""
    p = tmp_path / "s.json"
    S = server.S
    S.cfg = server.Config(league_id=435266, team_id=1)
    S.manual = {1: 1, 2: 0}          # one mine, one someone else's
    S.scraped = {3: 0}
    S.history = [1, 2]
    S.my_slot = 4
    S.save(p)

    fresh = server.State()
    fresh.cfg = server.Config(league_id=435266, team_id=1)
    msg = fresh.restore(p)
    assert "resumed 3 picks" in msg
    assert fresh.manual == {1: 1, 2: 0}
    assert fresh.scraped == {3: 0}
    assert fresh.history == [1, 2]
    assert fresh.my_slot == 4


def test_will_not_resume_another_leagues_state(tmp_path):
    p = tmp_path / "s.json"
    S = server.S
    S.cfg = server.Config(league_id=111)
    S.manual = {1: 1}
    S.save(p)

    fresh = server.State()
    fresh.cfg = server.Config(league_id=222)
    assert "ignored state from league 111" in fresh.restore(p)
    assert fresh.manual == {}


def test_will_not_resume_stale_state(tmp_path):
    import json as _json
    p = tmp_path / "s.json"
    server.S.manual = {1: 1}
    server.S.save(p)
    d = _json.loads(p.read_text())
    d["saved_at"] -= server.STATE_MAX_AGE + 60
    p.write_text(_json.dumps(d))

    fresh = server.State()
    assert "old" in fresh.restore(p)
    assert fresh.manual == {}


def test_corrupt_state_file_is_survivable(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json")
    fresh = server.State()
    assert fresh.restore(p) == "unreadable state file, ignored"
    assert fresh.manual == {}


def test_missing_state_file_is_silent(tmp_path):
    assert server.State().restore(tmp_path / "nope.json") is None


def test_scrape_direction_survives_restart(tmp_path):
    """Otherwise the recent-picks feed would render backwards after a restart."""
    p = tmp_path / "s.json"
    S = server.S
    S.scrape_newest_first = True
    S.scrape_order = [3, 2, 1]
    S.save(p)
    fresh = server.State()
    fresh.restore(p)
    assert fresh.scrape_newest_first is True
    assert fresh.scrape_order == [3, 2, 1]
