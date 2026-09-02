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
