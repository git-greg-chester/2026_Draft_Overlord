"""Parse tests against ESPN's real payload shape.

This is the code that runs live on draft night, so it gets exercised against
a fixture rather than trusted.
"""

import pytest

from espn import Config, DraftState, EspnClient, EspnError, starters_from_slots

PAYLOAD = {
    "settings": {
        "rosterSettings": {
            # QB1 RB2 WR2 WR/TE1 TE1 FLEX1 DST1 BE7 = 16
            "lineupSlotCounts": {
                "0": 1, "2": 2, "4": 2, "5": 1, "6": 1, "16": 1,
                "17": 0, "20": 7, "21": 0, "23": 1,
            }
        },
        "draftSettings": {"pickOrder": [4, 7, 2, 9, 1, 5, 10, 3, 8, 6]},
    },
    "teams": [
        {"id": 1, "location": "Team", "nickname": "One"},
        {"id": 2, "name": "Boys Rule"},
    ],
    "draftDetail": {
        "drafted": False,
        "inProgress": True,
        "picks": [
            {"overallPickNumber": 2, "roundId": 1, "teamId": 7, "playerId": 4430807},
            {"overallPickNumber": 1, "roundId": 1, "teamId": 4, "playerId": 4429795},
            {"overallPickNumber": 3, "roundId": 1, "teamId": 2, "playerId": 4362628,
             "keeper": True},
        ],
    },
}


def test_picks_sorted_by_overall():
    st = EspnClient.parse(PAYLOAD)
    assert [p["overall"] for p in st.picks] == [1, 2, 3]
    assert st.picks[0]["espn_id"] == 4429795
    assert st.picks[2]["keeper"] is True


def test_slot_counts_named_and_zero_slots_dropped():
    st = EspnClient.parse(PAYLOAD)
    assert st.slot_counts == {"QB": 1, "RB": 2, "WR": 2, "WR/TE": 1,
                              "TE": 1, "FLEX": 1, "DST": 1, "BE": 7}
    assert "K" not in st.slot_counts          # league has no kicker
    assert st.roster_size == 16


def test_starters_exclude_bench_and_ir():
    st = EspnClient.parse(PAYLOAD)
    starters = starters_from_slots(st.slot_counts)
    assert sum(starters.values()) == 9
    assert "BE" not in starters and "IR" not in starters


def test_team_names_fall_back_to_location_nickname():
    st = EspnClient.parse(PAYLOAD)
    assert st.teams[1] == "Team One"
    assert st.teams[2] == "Boys Rule"


def test_draft_order_and_flags():
    st = EspnClient.parse(PAYLOAD)
    assert st.draft_order == [4, 7, 2, 9, 1, 5, 10, 3, 8, 6]
    assert st.in_progress is True and st.complete is False


def test_empty_payload_does_not_explode():
    st = EspnClient.parse({})
    assert st.picks == [] and st.roster_size == 16


def test_unfilled_pick_slots_are_not_drafted_players():
    """ESPN pre-creates all 160 pick slots with playerId -1 before the draft.

    Treating those as real picks would show an empty board on draft night.
    """
    skeleton = {
        "teams": [{"id": i, "name": f"T{i}"} for i in range(1, 11)],
        "draftDetail": {
            "drafted": False, "inProgress": False,
            "picks": [
                {"overallPickNumber": n, "roundId": (n - 1) // 10 + 1,
                 "teamId": (n - 1) % 10 + 1, "playerId": -1}
                for n in range(1, 161)
            ],
        },
    }
    st = EspnClient.parse(skeleton)
    assert st.picks == []          # nothing actually drafted
    assert st.pick_slots == 160    # but the skeleton size is known
    assert st.draft_rounds == 16   # 160 / 10 teams


def test_partially_started_draft():
    payload = {
        "teams": [{"id": i, "name": f"T{i}"} for i in range(1, 11)],
        "draftDetail": {
            "inProgress": True,
            "picks": [
                {"overallPickNumber": 1, "roundId": 1, "teamId": 1, "playerId": 4429795},
                {"overallPickNumber": 2, "roundId": 1, "teamId": 2, "playerId": 4430807},
            ] + [
                {"overallPickNumber": n, "roundId": (n - 1) // 10 + 1,
                 "teamId": (n - 1) % 10 + 1, "playerId": -1}
                for n in range(3, 161)
            ],
        },
    }
    st = EspnClient.parse(payload)
    assert [p["espn_id"] for p in st.picks] == [4429795, 4430807]
    assert st.draft_rounds == 16


def test_draft_rounds_ignores_ir_slots():
    """With no pick skeleton to count, fall back to slots minus IR.

    The real league is roster_size 19 (16 drafted + 3 IR) and drafts 16 rounds.
    """
    payload = {
        "settings": {"rosterSettings": {"lineupSlotCounts": {
            "0": 1, "2": 2, "4": 2, "5": 1, "6": 1, "16": 1,
            "20": 7, "21": 3, "23": 1,        # slot 21 = IR
        }}},
        "teams": [{"id": i, "name": f"T{i}"} for i in range(1, 11)],
    }
    st = EspnClient.parse(payload)
    assert st.roster_size == 19       # ESPN counts IR here
    assert st.slot_counts["IR"] == 3
    assert st.pick_slots == 0         # no draft skeleton present
    assert st.draft_rounds == 16      # 19 - 3 IR


def test_slot_from_draft_order():
    from draft import slot_from_draft_order
    order = PAYLOAD["settings"]["draftSettings"]["pickOrder"]
    assert slot_from_draft_order(order, 4) == 1    # first
    assert slot_from_draft_order(order, 6) == 10   # last
    assert slot_from_draft_order(order, 999) is None


def test_swid_gets_braces():
    c = EspnClient(Config(espn_s2="abc", swid="AAAA-BBBB"))
    assert c.s.cookies["SWID"] == "{AAAA-BBBB}"
    c2 = EspnClient(Config(espn_s2="abc", swid="{AAAA-BBBB}"))
    assert c2.s.cookies["SWID"] == "{AAAA-BBBB}"


def test_missing_config_is_an_auth_error(tmp_path):
    with pytest.raises(EspnError) as e:
        Config.load(tmp_path / "nope.json")
    assert e.value.kind == "auth"
