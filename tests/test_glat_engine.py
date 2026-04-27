import random
import unittest
from pathlib import Path

from glat_engine import GLATEngine, InvalidActionError
from referee import get_legal_actions


class FakeAgent:
    def __init__(self) -> None:
        self.calls = 0

    def get_action(self, state, legal_actions):
        self.calls += 1
        for index, action in enumerate(legal_actions):
            if action["type"] != "end_turn":
                return index
        return len(legal_actions) - 1

    def get_turn_plan(self, state, legal_actions):
        self.calls += 1
        non_end_actions = [
            index for index, action in enumerate(legal_actions) if action["type"] != "end_turn"
        ]
        if not non_end_actions:
            return [len(legal_actions) - 1]
        return [non_end_actions[0], len(legal_actions) - 1]


class StalePlanAgent:
    def __init__(self) -> None:
        self.calls = 0

    def get_turn_plan(self, state, legal_actions):
        self.calls += 1
        first_play = next(
            (index for index, action in enumerate(legal_actions) if action["type"] == "play_card"),
            len(legal_actions) - 1,
        )
        return [first_play, first_play]


class SpyEngine(GLATEngine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.applied_action_validity = []

    def apply_action(self, state, action):
        self.applied_action_validity.append(self.is_valid_action(state, action))
        return super().apply_action(state, action)


def choose_by_card_id(card_id_to_choose):
    def _chooser(state, player_id, prompt, options, optional):
        for option in options:
            if option["card_id"] == card_id_to_choose:
                return option["instance_id"]
        return None if optional else "__default__"
    return _chooser


def choose_blocker(blocker_card_id):
    def _chooser(state, defender_id, attacker, target, blocker_options, counter_options):
        for option in blocker_options:
            if option["card_id"] == blocker_card_id:
                return {"blocker_id": option["instance_id"], "counter_ids": []}
        return {"mode": "default"}
    return _chooser


def always_trigger(state, player_id, card):
    return True


class GLATEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = FakeAgent()
        self.engine = SpyEngine(agent=self.agent)

    def test_initial_setup_deals_opening_hand_before_life(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        _, deck_ids = self.engine._deck_entries()
        expected_order = list(deck_ids)
        random.Random(7).shuffle(expected_order)
        player = state["players"]["P1"]

        self.assertEqual([card["card_id"] for card in player["hand"]], expected_order[:5])
        self.assertEqual([card["card_id"] for card in player["life_cards"]], expected_order[5:9])
        self.assertEqual(len(player["deck"]), 41)
        self.assertFalse(player["mulligan_used"])
        self.assertEqual(state["match_mode"], "digital_strict")
        self.assertEqual(state["command_console"], [])

    def test_initial_state_accepts_physical_reported_match_mode(self) -> None:
        state = self.engine.create_initial_state(seed=7, match_mode="physical_reported")

        self.assertEqual(state["match_mode"], "physical_reported")

    def test_lookup_card_data_reads_full_card_database(self) -> None:
        card = self.engine.lookup_card_data("OP12-021")

        self.assertIsNotNone(card)
        self.assertEqual(card["name"], "Ipponmatsu")

    def test_initial_state_rejects_unknown_match_mode(self) -> None:
        with self.assertRaises(ValueError):
            self.engine.create_initial_state(seed=7, match_mode="loosey_goosey")

    def test_initial_setup_can_apply_one_opening_mulligan_before_life(self) -> None:
        kept_state = self.engine.create_initial_state(seed=7)
        mulligan_state = self.engine.create_initial_state(seed=7, mulligans={"P1": True})
        kept_player = kept_state["players"]["P1"]
        mulligan_player = mulligan_state["players"]["P1"]

        self.assertTrue(mulligan_player["mulligan_used"])
        self.assertEqual(len(mulligan_player["hand"]), 5)
        self.assertEqual(len(mulligan_player["life_cards"]), mulligan_player["life"])
        self.assertEqual(len(mulligan_player["deck"]), 41)
        self.assertNotEqual(
            [card["instance_id"] for card in kept_player["hand"]],
            [card["instance_id"] for card in mulligan_player["hand"]],
        )

    def test_turn_progression_runs_ten_turns(self) -> None:
        state = self.engine.run_game(max_turns=10, seed=7)

        self.engine.validate_state(state)
        self.assertEqual(state["turn"], 11)
        self.assertEqual(state["phase"], "end")
        self.assertGreater(len(state["logs"]), 0)

    def test_save_and_load_state_round_trip(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        path = Path("glat_state_roundtrip.test.json")
        try:
            self.engine.save_state(state, str(path))
            loaded = self.engine.load_state(str(path))
            self.assertEqual(loaded["turn"], state["turn"])
            self.assertEqual(loaded["active_player"], state["active_player"])
            self.assertEqual(len(loaded["players"]["P1"]["hand"]), len(state["players"]["P1"]["hand"]))
            self.assertEqual(len(loaded["players"]["P2"]["life_cards"]), len(state["players"]["P2"]["life_cards"]))
        finally:
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass

    def test_initial_state_tracks_life_cards(self) -> None:
        state = self.engine.create_initial_state(seed=7)

        self.assertEqual(len(state["players"]["P1"]["life_cards"]), 4)
        self.assertEqual(len(state["players"]["P2"]["life_cards"]), 4)
        self.assertEqual(len(state["players"]["P1"]["deck"]), 41)
        self.assertEqual(len(state["players"]["P2"]["deck"]), 41)

    def test_illegal_action_is_rejected(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["phase"] = "main"

        first_hand_card = state["players"]["P1"]["hand"][0]
        state["players"]["P1"]["board"].append(first_hand_card)
        state["players"]["P1"]["hand"] = state["players"]["P1"]["hand"][1:]
        first_hand_card["played_turn"] = state["turn"]

        invalid_attack = {
            "type": "attack",
            "payload": {
                "attacker_id": first_hand_card["instance_id"],
                "target": "leader",
            },
        }

        self.assertFalse(self.engine.is_valid_action(state, invalid_attack))
        with self.assertRaises(InvalidActionError):
            self.engine.apply_action(state, invalid_attack)

    def test_combat_resolution_kos_rested_character(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 2
        state["active_player"] = "P1"
        state["phase"] = "main"

        attacker = self.engine.build_card_instance("P1", "OP12-119")
        attacker["played_turn"] = 1
        defender = self.engine.build_card_instance("P2", "OP12-086")
        defender["played_turn"] = 1
        defender["state"] = "rested"

        state["players"]["P1"]["board"] = [attacker]
        state["players"]["P2"]["board"] = [defender]
        state["players"]["P2"]["hand"] = []

        attack = {
            "type": "attack",
            "payload": {
                "attacker_id": attacker["instance_id"],
                "target": defender["instance_id"],
            },
        }

        result = self.engine.apply_action(state, attack)

        self.assertTrue(result["ko"])
        self.assertEqual(len(state["players"]["P2"]["board"]), 0)
        self.assertEqual(attacker["state"], "rested")

    def test_legal_action_generator_only_returns_valid_actions(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        self.engine.refresh_phase(state)
        self.engine.draw_phase(state)
        self.engine.don_phase(state)
        state["phase"] = "main"

        actions = get_legal_actions(state, self.engine)

        self.assertGreater(len(actions), 0)
        self.assertEqual(actions[-1]["type"], "end_turn")
        self.assertTrue(all(self.engine.is_valid_action(state, action) for action in actions))

    def test_event_card_can_be_played_to_trash(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 2
        state["active_player"] = "P2"
        state["phase"] = "main"

        player = state["players"]["P2"]
        event_card = self.engine.build_card_instance("P2", "OP12-097")
        player["hand"] = [event_card]
        player["don_area"] = ["P2-DON-01"]
        player["don_deck"] = [f"P2-DON-{index:02d}" for index in range(2, 11)]
        player["spent_don"] = []
        player["attached_don"] = {}
        player["board"] = []
        player["trash"] = []

        action = {
            "type": "play_card",
            "payload": {"card_id": event_card["instance_id"]},
        }

        self.assertTrue(self.engine.is_valid_action(state, action))
        result = self.engine.apply_action(state, action)

        self.assertEqual(result["destination"], "trash")
        self.assertTrue(result["effect_resolved"])
        self.assertEqual(len(player["hand"]), 1)
        self.assertGreaterEqual(len(player["trash"]), 1)
        self.assertEqual(player["trash"][0]["card_id"], "OP12-097")

    def test_manual_zone_primitives_keep_state_valid(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        player = state["players"]["P2"]

        starting_deck = len(player["deck"])
        revealed = self.engine.manual_reveal_top(state, "P2", 2)
        self.assertEqual(len(revealed), 2)
        self.assertEqual(len(player["deck"]), starting_deck)

        drawn = self.engine.manual_draw(state, "P2", 1)
        self.assertEqual(len(drawn), 1)
        self.assertEqual(len(player["hand"]), 6)

        discard_id = player["hand"][0]["instance_id"]
        self.engine.manual_discard(state, "P2", discard_id)
        self.assertEqual(len(player["trash"]), 1)

        trashed = self.engine.manual_trash_top(state, "P2", 2)
        self.assertEqual(len(trashed), 2)
        self.assertEqual(len(player["trash"]), 3)

        life_added = self.engine.manual_add_life(state, "P2", 1)
        self.assertEqual(len(life_added), 1)
        self.assertEqual(len(player["life_cards"]), 5)

        hand_card = player["hand"][0]["instance_id"]
        self.engine.manual_move_card(state, "P2", hand_card, "hand", "deck", position="top")
        self.assertEqual(player["deck"][0]["instance_id"], hand_card)

        board_card = self.engine.build_card_instance("P2", "OP12-086")
        player["board"].append(board_card)
        self.engine.validate_state(state)
        self.engine.manual_ko(state, "P2", board_card["instance_id"])
        self.assertEqual(player["trash"][-1]["instance_id"], board_card["instance_id"])

        self.engine.validate_state(state)

    def test_op12_097_resolves_search_and_trash(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 2
        state["active_player"] = "P2"
        state["phase"] = "main"
        player = state["players"]["P2"]

        event_card = self.engine.build_card_instance("P2", "OP12-097")
        top_a = self.engine.build_card_instance("P2", "OP12-089")
        top_b = self.engine.build_card_instance("P2", "OP12-097")
        top_c = self.engine.build_card_instance("P2", "OP10-109")

        player["hand"] = [event_card]
        player["deck"] = [top_a, top_b, top_c]
        player["trash"] = []
        player["don_area"] = ["P2-DON-01"]
        player["don_deck"] = [f"P2-DON-{index:02d}" for index in range(2, 11)]

        result = self.engine.apply_action(
            state,
            {"type": "play_card", "payload": {"card_id": event_card["instance_id"]}},
        )

        self.assertTrue(result["effect_resolved"])
        self.assertEqual(result["effect_result"]["added"], top_a["instance_id"])
        self.assertEqual([card["instance_id"] for card in player["trash"]], [event_card["instance_id"], top_b["instance_id"], top_c["instance_id"]])
        self.assertEqual(player["hand"][0]["instance_id"], top_a["instance_id"])

    def test_op12_097_uses_choice_provider_for_search(self) -> None:
        engine = SpyEngine(agent=self.agent, effect_choice_provider=choose_by_card_id("OP12-093"))
        state = engine.create_initial_state(seed=7)
        state["turn"] = 2
        state["active_player"] = "P2"
        state["phase"] = "main"
        player = state["players"]["P2"]

        event_card = engine.build_card_instance("P2", "OP12-097")
        top_a = engine.build_card_instance("P2", "OP12-089")
        top_b = engine.build_card_instance("P2", "OP10-109")
        top_c = engine.build_card_instance("P2", "OP12-093")

        player["hand"] = [event_card]
        player["deck"] = [top_a, top_b, top_c]
        player["trash"] = []
        player["don_area"] = ["P2-DON-01"]
        player["don_deck"] = [f"P2-DON-{index:02d}" for index in range(2, 11)]

        result = engine.apply_action(
            state,
            {"type": "play_card", "payload": {"card_id": event_card["instance_id"]}},
        )

        self.assertEqual(result["effect_result"]["added"], top_c["instance_id"])
        self.assertEqual(player["hand"][0]["instance_id"], top_c["instance_id"])

    def test_op12_086_resolves_search_on_play(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 2
        state["active_player"] = "P2"
        state["phase"] = "main"
        player = state["players"]["P2"]

        koala = self.engine.build_card_instance("P2", "OP12-086")
        top_a = self.engine.build_card_instance("P2", "OP12-093")
        top_b = self.engine.build_card_instance("P2", "OP12-086")
        top_c = self.engine.build_card_instance("P2", "OP14-108")

        player["hand"] = [koala]
        player["deck"] = [top_a, top_b, top_c]
        player["trash"] = []
        player["don_area"] = ["P2-DON-01"]
        player["don_deck"] = [f"P2-DON-{index:02d}" for index in range(2, 11)]

        result = self.engine.apply_action(
            state,
            {"type": "play_card", "payload": {"card_id": koala["instance_id"]}},
        )

        self.assertTrue(result["effect_resolved"])
        self.assertEqual(result["effect_result"]["added"], top_a["instance_id"])
        self.assertEqual(len(player["board"]), 1)
        self.assertEqual([card["instance_id"] for card in player["trash"]], [top_b["instance_id"], top_c["instance_id"]])

    def test_op12_094_recycles_and_plays_from_trash(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = "P2"
        state["phase"] = "main"
        player = state["players"]["P2"]

        dragon = self.engine.build_card_instance("P2", "OP12-094")
        trash_a = self.engine.build_card_instance("P2", "OP12-089")
        trash_b = self.engine.build_card_instance("P2", "OP12-093")
        trash_c = self.engine.build_card_instance("P2", "PRB02-014")
        trash_play = self.engine.build_card_instance("P2", "OP12-086")

        player["hand"] = [dragon]
        player["trash"] = [trash_a, trash_b, trash_c, trash_play]
        player["deck"] = []
        player["board"] = []
        player["don_area"] = [f"P2-DON-{index:02d}" for index in range(1, 9)]
        player["don_deck"] = [f"P2-DON-{index:02d}" for index in range(9, 11)]

        result = self.engine.apply_action(
            state,
            {"type": "play_card", "payload": {"card_id": dragon["instance_id"]}},
        )

        self.assertTrue(result["effect_resolved"])
        self.assertEqual(len(result["effect_result"]["recycled"]), 3)
        self.assertIsNotNone(result["effect_result"]["played_from_trash"])
        self.assertEqual(len(player["board"]), 2)

    def test_op12_094_effect_play_triggers_opponent_leader_reaction(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = "P2"
        state["phase"] = "main"
        player = state["players"]["P2"]
        opponent = state["players"]["P1"]

        dragon = self.engine.build_card_instance("P2", "OP12-094")
        trash_a = self.engine.build_card_instance("P2", "OP12-089")
        trash_b = self.engine.build_card_instance("P2", "OP12-093")
        trash_c = self.engine.build_card_instance("P2", "PRB02-014")
        trash_play = self.engine.build_card_instance("P2", "OP12-086")

        player["hand"] = [dragon]
        player["trash"] = [trash_a, trash_b, trash_c, trash_play]
        player["deck"] = []
        player["life_cards"] = [self.engine.build_card_instance("P2", "OP12-086")]
        player["life"] = 1
        player["board"] = []
        player["don_area"] = [f"P2-DON-{index:02d}" for index in range(1, 9)]
        player["don_deck"] = [f"P2-DON-{index:02d}" for index in range(9, 11)]
        opponent["leader"]["card_id"] = "OP12-081"
        opponent["leader"]["name"] = "Koala"

        result = self.engine.apply_action(
            state,
            {"type": "play_card", "payload": {"card_id": dragon["instance_id"]}},
        )

        self.assertEqual(result["effect_result"]["leader_reaction"]["effect"], "OP12-081")
        self.assertEqual(player["life"], 0)
        self.assertEqual(len(player["hand"]), 1)

    def test_sabo_cost_reduction_uses_trash_count(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 4
        state["active_player"] = "P2"
        state["phase"] = "main"
        player = state["players"]["P2"]

        sabo = self.engine.build_card_instance("P2", "PRB02-014")
        player["hand"] = [sabo]
        player["trash"] = [self.engine.build_card_instance("P2", "OP12-086") for _ in range(15)]
        player["don_area"] = ["P2-DON-01", "P2-DON-02", "P2-DON-03"]
        player["don_deck"] = [f"P2-DON-{index:02d}" for index in range(4, 11)]
        player["board"] = []

        action = {"type": "play_card", "payload": {"card_id": sabo["instance_id"]}}
        self.assertTrue(self.engine.is_valid_action(state, action))
        result = self.engine.apply_action(state, action)

        self.assertEqual(result["paid_cost"], 3)
        self.assertEqual(len(player["board"]), 1)

    def test_op12_119_adds_life_and_discards(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = "P2"
        state["phase"] = "main"
        player = state["players"]["P2"]

        kuma = self.engine.build_card_instance("P2", "OP12-119")
        extra = self.engine.build_card_instance("P2", "OP12-086")
        top_life = self.engine.build_card_instance("P2", "OP12-093")

        player["hand"] = [kuma, extra]
        player["deck"] = [top_life]
        player["trash"] = []
        player["board"] = []
        player["life_cards"] = []
        player["life"] = 0
        player["don_area"] = [f"P2-DON-{index:02d}" for index in range(1, 7)]
        player["don_deck"] = [f"P2-DON-{index:02d}" for index in range(7, 11)]
        player["spent_don"] = []

        result = self.engine.apply_action(
            state,
            {"type": "play_card", "payload": {"card_id": kuma["instance_id"]}},
        )

        self.assertTrue(result["effect_resolved"])
        self.assertEqual(result["effect_result"]["added_to_life"], [top_life["instance_id"]])
        self.assertEqual(player["life"], 1)
        self.assertEqual(player["life_cards"][0]["instance_id"], top_life["instance_id"])
        self.assertEqual(player["trash"][0]["instance_id"], extra["instance_id"])
        self.assertEqual(player["board"][0]["temporary_cost_bonus"], 2)

    def test_op12_087_discards_for_cost_and_hits_opponent_hand(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 4
        state["active_player"] = "P2"
        state["phase"] = "main"
        player = state["players"]["P2"]
        opponent = state["players"]["P1"]

        robin = self.engine.build_card_instance("P2", "OP12-087")
        extra_a = self.engine.build_card_instance("P2", "OP12-086")
        extra_b = self.engine.build_card_instance("P2", "OP12-093")
        opp_cards = [self.engine.build_card_instance("P1", "OP12-086") for _ in range(5)]

        player["hand"] = [robin, extra_a, extra_b]
        player["trash"] = []
        player["board"] = []
        player["don_area"] = [f"P2-DON-{index:02d}" for index in range(1, 7)]
        player["don_deck"] = [f"P2-DON-{index:02d}" for index in range(7, 11)]
        opponent["hand"] = opp_cards
        opponent["trash"] = []

        result = self.engine.apply_action(
            state,
            {"type": "play_card", "payload": {"card_id": robin["instance_id"]}},
        )

        self.assertTrue(result["effect_resolved"])
        self.assertEqual(len(result["effect_result"]["opponent_discarded"]), 2)
        self.assertEqual(len(opponent["hand"]), 3)
        self.assertEqual(len(opponent["trash"]), 2)

    def test_op14_108_kos_valid_target(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 4
        state["active_player"] = "P2"
        state["phase"] = "main"
        player = state["players"]["P2"]
        opponent = state["players"]["P1"]

        rayleigh = self.engine.build_card_instance("P2", "OP14-108")
        target = self.engine.build_card_instance("P1", "OP12-119")
        opponent["board"] = [target]
        opponent["trash"] = []
        opponent["life_cards"] = opponent["life_cards"][:3]
        opponent["life"] = 3
        player["hand"] = [rayleigh]
        player["board"] = []
        player["don_area"] = [f"P2-DON-{index:02d}" for index in range(1, 7)]
        player["don_deck"] = [f"P2-DON-{index:02d}" for index in range(7, 11)]

        result = self.engine.apply_action(
            state,
            {"type": "play_card", "payload": {"card_id": rayleigh["instance_id"]}},
        )

        self.assertEqual(result["effect_result"]["ko_target"], target["instance_id"])
        self.assertEqual(len(opponent["board"]), 0)
        self.assertEqual(opponent["trash"][-1]["instance_id"], target["instance_id"])

    def test_eb03_053_attaches_spent_don_and_takes_opponent_life(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 4
        state["active_player"] = "P2"
        state["phase"] = "main"
        player = state["players"]["P2"]
        opponent = state["players"]["P1"]

        card = self.engine.build_card_instance("P2", "EB03-053")
        player["hand"] = [card]
        player["board"] = []
        player["don_area"] = [f"P2-DON-{index:02d}" for index in range(1, 6)]
        player["spent_don"] = ["P2-SPENT-01"]
        player["don_deck"] = [f"P2-DON-{index:02d}" for index in range(7, 11)]
        player["attached_don"] = {}
        opponent_starting_hand = len(opponent["hand"])

        result = self.engine.apply_action(
            state,
            {"type": "play_card", "payload": {"card_id": card["instance_id"]}},
        )

        self.assertTrue(result["effect_resolved"])
        self.assertEqual(result["effect_result"]["attached_to"], player["leader"]["instance_id"])
        self.assertEqual(player["attached_don"][player["leader"]["instance_id"]], 1)
        self.assertEqual(len(result["effect_result"]["opponent_life_to_hand"]), 1)
        self.assertEqual(len(opponent["hand"]), opponent_starting_hand + 1)

    def test_op10_109_on_ko_trashes_opponent_life(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 5
        state["active_player"] = "P1"
        state["phase"] = "main"
        attacker_player = state["players"]["P1"]
        defender_player = state["players"]["P2"]

        attacker = self.engine.build_card_instance("P1", "OP12-119")
        attacker["played_turn"] = 4
        hawkins = self.engine.build_card_instance("P2", "OP10-109")
        hawkins["played_turn"] = 4
        hawkins["state"] = "rested"
        starting_life = attacker_player["life"]

        attacker_player["board"] = [attacker]
        defender_player["board"] = [hawkins]
        defender_player["hand"] = []

        result = self.engine.apply_action(
            state,
            {"type": "attack", "payload": {"attacker_id": attacker["instance_id"], "target": hawkins["instance_id"]}},
        )

        self.assertTrue(result["ko"])
        self.assertEqual(result["ko_result"]["effect_result"]["effect"], "OP10-109")
        self.assertEqual(attacker_player["life"], starting_life - 1)
        self.assertEqual(len(result["ko_result"]["effect_result"]["trashed_life"]), 1)

    def test_eb03_042_on_ko_can_play_supported_character(self) -> None:
        engine = SpyEngine(agent=self.agent, effect_choice_provider=choose_by_card_id("OP12-087"))
        state = engine.create_initial_state(seed=7)
        state["turn"] = 5
        state["active_player"] = "P1"
        state["phase"] = "main"
        attacker_player = state["players"]["P1"]
        defender_player = state["players"]["P2"]

        attacker = engine.build_card_instance("P1", "OP12-119")
        attacker["played_turn"] = 4
        koala = engine.build_card_instance("P2", "EB03-042")
        koala["played_turn"] = 4
        koala["state"] = "rested"
        robin = engine.build_card_instance("P2", "OP12-087")

        attacker_player["board"] = [attacker]
        defender_player["board"] = [koala]
        defender_player["hand"] = [robin]
        defender_player["trash"] = []

        result = engine.apply_action(
            state,
            {"type": "attack", "payload": {"attacker_id": attacker["instance_id"], "target": koala["instance_id"]}},
        )

        self.assertTrue(result["ko"])
        self.assertEqual(result["ko_result"]["effect_result"]["effect"], "EB03-042")
        self.assertEqual(result["ko_result"]["effect_result"]["played"], robin["instance_id"])
        self.assertTrue(any(card["instance_id"] == robin["instance_id"] for card in defender_player["board"]))

    def test_manual_counter_event_adds_battle_power_bonus(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = "P1"
        state["phase"] = "main"
        player = state["players"]["P2"]
        target = self.engine.build_card_instance("P2", "OP12-086")
        counter_card = self.engine.build_card_instance("P2", "OP12-098")
        big_character = self.engine.build_card_instance("P2", "OP12-089")
        big_character["temporary_cost_bonus"] = 4

        player["board"] = [target, big_character]
        player["hand"] = [counter_card]
        player["trash"] = []

        result = self.engine.manual_use_counter(state, "P2", counter_card["instance_id"], target["instance_id"])

        self.assertEqual(result["power_bonus"], 4000)
        self.assertEqual(target["battle_power_bonus"], 4000)
        self.assertEqual(player["trash"][-1]["instance_id"], counter_card["instance_id"])

    def test_character_counter_uses_printed_counter_value_without_extra_multiplier(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = "P1"
        state["phase"] = "main"
        player = state["players"]["P2"]
        target = player["leader"]
        counter_card = self.engine.build_card_instance("P2", "OP12-112")
        player["hand"] = [counter_card]

        self.engine._open_battle_context(state, state["players"]["P1"]["leader"], "leader")
        self.engine._advance_battle_context(state, "counter_window", {"current_target": "leader"})

        result = self.engine.manual_use_counter(state, "P2", counter_card["instance_id"], target["instance_id"])

        self.assertEqual(result["power_bonus"], 2000)
        self.assertEqual(target["battle_power_bonus"], 2000)

    def test_counter_event_cannot_be_played_during_own_turn(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = "P2"
        state["phase"] = "main"
        player = state["players"]["P2"]
        counter_event = self.engine.build_card_instance("P2", "OP12-098")
        player["hand"] = [counter_event]
        player["don_area"] = [f"P2-DON-{index:02d}" for index in range(1, 3)]

        action = {"type": "play_card", "payload": {"card_id": counter_event["instance_id"]}}

        self.assertFalse(self.engine.is_valid_action(state, action))
        with self.assertRaises(InvalidActionError):
            self.engine.apply_action(state, action)

    def test_character_with_counter_can_still_be_played_during_own_turn(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = "P2"
        state["phase"] = "main"
        player = state["players"]["P2"]
        character = self.engine.build_card_instance("P2", "OP12-086")
        player["hand"] = [character]
        player["don_area"] = [f"P2-DON-{index:02d}" for index in range(1, 3)]

        action = {"type": "play_card", "payload": {"card_id": character["instance_id"]}}

        self.assertTrue(self.engine.is_valid_action(state, action))

    def test_manual_counter_cannot_be_used_during_your_own_turn(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = "P2"
        state["phase"] = "main"
        player = state["players"]["P2"]
        target = self.engine.build_card_instance("P2", "OP12-086")
        counter_card = self.engine.build_card_instance("P2", "OP12-098")
        player["board"] = [target]
        player["hand"] = [counter_card]

        with self.assertRaises(ValueError):
            self.engine.manual_use_counter(state, "P2", counter_card["instance_id"], target["instance_id"])

    def test_manual_counter_requires_counter_window_if_battle_context_exists(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = "P1"
        state["phase"] = "main"
        player = state["players"]["P2"]
        target = self.engine.build_card_instance("P2", "OP12-086")
        attacker = self.engine.build_card_instance("P1", "OP12-119")
        counter_card = self.engine.build_card_instance("P2", "OP12-098")
        player["board"] = [target]
        player["hand"] = [counter_card]
        state["battle_context"] = {
            "active": True,
            "turn": state["turn"],
            "attacking_player": "P1",
            "defending_player": "P2",
            "attacker_id": attacker["instance_id"],
            "original_target": target["instance_id"],
            "current_target": target["instance_id"],
            "stage": "blocker_window",
            "events": [],
            "status": "in_progress",
        }

        with self.assertRaises(ValueError):
            self.engine.manual_use_counter(state, "P2", counter_card["instance_id"], target["instance_id"])

    def test_manual_trigger_op12_112_draws_two(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        player = state["players"]["P2"]
        trigger_card = player["life_cards"][0]
        trigger_card["card_id"] = "OP12-112"
        trigger_card["name"] = "Baby 5"
        player["deck"] = [
            self.engine.build_card_instance("P2", "OP12-086"),
            self.engine.build_card_instance("P2", "OP12-093"),
        ] + player["deck"]
        starting_hand = len(player["hand"])

        result = self.engine.manual_activate_trigger(state, "P2", trigger_card["instance_id"])

        self.assertEqual(result["effect_result"]["effect"], "OP12-112")
        self.assertEqual(len(result["effect_result"]["drawn"]), 2)
        self.assertEqual(len(player["hand"]), starting_hand + 2)
        self.assertEqual(player["life"], 3)

    def test_manual_trigger_op06_115_adds_life_and_discards(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        player = state["players"]["P2"]
        trigger_card = player["life_cards"][0]
        trigger_card["card_id"] = "OP06-115"
        trigger_card["name"] = "You're the One Who Should Disappear."
        player["life_cards"] = [trigger_card]
        player["life"] = 1
        top_deck = self.engine.build_card_instance("P2", "OP12-086")
        discard_card = self.engine.build_card_instance("P2", "OP12-093")
        player["deck"] = [top_deck] + player["deck"]
        player["hand"] = [discard_card]

        result = self.engine.manual_activate_trigger(state, "P2", trigger_card["instance_id"])

        self.assertEqual(result["effect_result"]["effect"], "OP06-115")
        self.assertEqual(result["effect_result"]["added_to_life"], [top_deck["instance_id"]])
        self.assertEqual(result["effect_result"]["discarded"], [discard_card["instance_id"]])
        self.assertEqual(player["life"], 1)

    def test_manual_trigger_requires_trigger_window_if_battle_context_exists(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        player = state["players"]["P2"]
        trigger_card = player["life_cards"][0]
        trigger_card["card_id"] = "OP12-112"
        trigger_card["name"] = "Baby 5"
        state["battle_context"] = {
            "active": True,
            "turn": state["turn"],
            "attacking_player": "P1",
            "defending_player": "P2",
            "attacker_id": "P1-CARD-999",
            "original_target": "leader",
            "current_target": "leader",
            "stage": "damage_resolution",
            "events": [],
            "status": "in_progress",
        }

        with self.assertRaises(ValueError):
            self.engine.manual_activate_trigger(state, "P2", trigger_card["instance_id"])

    def test_manual_trigger_in_trigger_window_updates_battle_context(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        player = state["players"]["P2"]
        trigger_card = player["life_cards"][0]
        trigger_card["card_id"] = "OP12-112"
        trigger_card["name"] = "Baby 5"
        player["deck"] = [
            self.engine.build_card_instance("P2", "OP12-086"),
            self.engine.build_card_instance("P2", "OP12-093"),
        ] + player["deck"]
        state["battle_context"] = {
            "active": True,
            "turn": state["turn"],
            "attacking_player": "P1",
            "defending_player": "P2",
            "attacker_id": "P1-CARD-999",
            "original_target": "leader",
            "current_target": "leader",
            "stage": "trigger_window",
            "events": [],
            "status": "in_progress",
        }

        result = self.engine.manual_activate_trigger(state, "P2", trigger_card["instance_id"])

        self.assertEqual(result["effect_result"]["effect"], "OP12-112")
        trigger_events = [event for event in state["battle_context"]["events"] if event["stage"] == "trigger_window"]
        self.assertTrue(any(event.get("manual_trigger") is True for event in trigger_events))
        self.assertTrue(any(event.get("resolution") == "trigger" for event in trigger_events))

    def test_manual_resolve_life_damage_adds_card_to_hand(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        player = state["players"]["P2"]
        player["life_cards"][0]["card_id"] = "OP12-094"
        player["life_cards"][0]["name"] = "Monkey.D.Dragon"
        top_life = player["life_cards"][0]["instance_id"]
        starting_hand = len(player["hand"])

        result = self.engine.manual_resolve_life_damage(state, "P2", 1)

        self.assertEqual(result[0]["revealed"], top_life)
        self.assertEqual(result[0]["resolution"], "hand")
        self.assertEqual(player["life"], 3)
        self.assertEqual(len(player["hand"]), starting_hand + 1)

    def test_manual_resolve_life_damage_can_activate_trigger(self) -> None:
        engine = SpyEngine(agent=self.agent, trigger_choice_provider=always_trigger)
        state = engine.create_initial_state(seed=7)
        player = state["players"]["P2"]
        trigger_card = player["life_cards"][0]
        trigger_card["card_id"] = "OP12-112"
        trigger_card["name"] = "Baby 5"
        player["hand"] = []
        player["deck"] = [
            engine.build_card_instance("P2", "OP12-086"),
            engine.build_card_instance("P2", "OP12-093"),
        ] + player["deck"]

        result = engine.manual_resolve_life_damage(state, "P2", 1)

        self.assertEqual(result[0]["resolution"], "trigger")
        self.assertEqual(result[0]["result"]["effect_result"]["effect"], "OP12-112")
        self.assertEqual(len(result[0]["result"]["effect_result"]["drawn"]), 2)

    def test_manual_set_card_state_updates_board_card(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        player = state["players"]["P2"]
        card = self.engine.build_card_instance("P2", "OP12-086")
        player["board"] = [card]

        result = self.engine.manual_set_card_state(state, "P2", card["instance_id"], "rested")

        self.assertEqual(result["to"], "rested")
        self.assertEqual(player["board"][0]["state"], "rested")

    def test_manual_move_don_attaches_and_returns(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        player = state["players"]["P2"]
        target = self.engine.build_card_instance("P2", "OP12-086")
        player["board"] = [target]
        player["don_area"] = ["P2-DON-01", "P2-DON-02"]
        player["don_deck"] = [f"P2-DON-{index:02d}" for index in range(3, 11)]

        attach_result = self.engine.manual_move_don(
            state,
            "P2",
            "don_area",
            "attached",
            2,
            attach_target=target["instance_id"],
        )
        self.assertEqual(attach_result["to"], "attached")
        self.assertEqual(player["attached_don"][target["instance_id"]], 2)
        self.assertEqual(len(player["don_area"]), 0)

        return_result = self.engine.manual_move_don(
            state,
            "P2",
            "attached",
            "don_area",
            1,
            attach_target=target["instance_id"],
        )
        self.assertEqual(return_result["from"], "attached")
        self.assertEqual(player["attached_don"][target["instance_id"]], 1)
        self.assertEqual(len(player["don_area"]), 1)

    def test_attached_don_power_only_applies_during_owner_turn(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = "P2"
        state["phase"] = "main"
        player = state["players"]["P2"]
        leader = player["leader"]
        base_power = leader["power"]
        player["don_deck"] = player["don_deck"][1:]
        player["attached_don"] = {leader["instance_id"]: 1}

        self.assertEqual(self.engine._current_power(state, player, leader), base_power + 1000)

        self.engine.end_phase(state)

        self.assertEqual(state["active_player"], "P1")
        self.assertEqual(player["attached_don"][leader["instance_id"]], 1)
        self.assertEqual(self.engine._current_power(state, player, leader), base_power)

    def test_on_ko_effects_chain_for_hack(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 5
        state["active_player"] = "P1"
        state["phase"] = "main"
        attacker_player = state["players"]["P1"]
        defender_player = state["players"]["P2"]

        attacker = self.engine.build_card_instance("P1", "OP12-119")
        attacker["played_turn"] = 4
        hack = self.engine.build_card_instance("P2", "OP12-089")
        hack["played_turn"] = 4
        hack["state"] = "rested"
        small_target = self.engine.build_card_instance("P1", "OP12-086")

        attacker_player["board"] = [attacker, small_target]
        defender_player["board"] = [hack]
        defender_player["hand"] = []
        defender_player["trash"] = []

        result = self.engine.apply_action(
            state,
            {"type": "attack", "payload": {"attacker_id": attacker["instance_id"], "target": hack["instance_id"]}},
        )

        self.assertTrue(result["ko"])
        self.assertEqual(result["ko_result"]["effect_result"]["ko_target"], small_target["instance_id"])
        self.assertEqual(len(attacker_player["board"]), 1)

    def test_leader_attack_draws_and_life_goes_to_hand(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = "P2"
        state["phase"] = "main"
        player = state["players"]["P2"]
        opponent = state["players"]["P1"]

        leader = player["leader"]
        char_a = self.engine.build_card_instance("P2", "OP12-089")
        char_b = self.engine.build_card_instance("P2", "OP12-093")
        char_a["played_turn"] = 2
        char_b["played_turn"] = 2
        player["board"] = [char_a, char_b]
        player["deck"] = [self.engine.build_card_instance("P2", "OP12-086")]
        opponent["hand"] = []
        opponent["board"] = []
        opponent["life_cards"][0]["card_id"] = "OP12-094"
        opponent["life_cards"][0]["name"] = "Monkey.D.Dragon"
        opponent_starting_hand = len(opponent["hand"])
        player_starting_hand = len(player["hand"])

        result = self.engine.apply_action(
            state,
            {"type": "attack", "payload": {"attacker_id": leader["instance_id"], "target": "leader"}},
        )

        self.assertEqual(len(result["life_to_hand"]), 1)
        self.assertEqual(opponent["life"], 3)
        self.assertEqual(len(opponent["hand"]), opponent_starting_hand + 1)
        self.assertEqual(result["leader_effect"]["effect"], "OP12-081")
        self.assertEqual(len(player["hand"]), player_starting_hand + 1)

    def test_blocker_redirects_attack(self) -> None:
        engine = SpyEngine(agent=self.agent, defense_choice_provider=choose_blocker("OP12-089"))
        state = engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = "P1"
        state["phase"] = "main"

        attacker_player = state["players"]["P1"]
        defender_player = state["players"]["P2"]
        attacker = engine.build_card_instance("P1", "OP12-119")
        attacker["played_turn"] = 2
        blocker = engine.build_card_instance("P2", "OP12-089")
        blocker["played_turn"] = 2

        attacker_player["board"] = [attacker]
        defender_player["board"] = [blocker]

        result = engine.apply_action(
            state,
            {"type": "attack", "payload": {"attacker_id": attacker["instance_id"], "target": "leader"}},
        )

        self.assertEqual(result["final_target"], blocker["instance_id"])
        self.assertEqual(result["defense"]["blocker_id"], blocker["instance_id"])
        self.assertEqual(defender_player["life"], 4)
        self.assertIsNone(state["battle_context"])
        self.assertEqual(result["battle_context"]["status"], "battle_complete")
        self.assertTrue(any(event["stage"] == "blocker_window" for event in result["battle_context"]["events"]))
        self.assertTrue(any(event["stage"] == "counter_window" for event in result["battle_context"]["events"]))
        self.assertTrue(any(event["stage"] == "cleanup" for event in result["battle_context"]["events"]))
        blocker_event = next(event for event in result["battle_context"]["events"] if event["stage"] == "blocker_window")
        self.assertEqual(blocker_event["chosen_blocker"], blocker["instance_id"])

    def test_default_counter_can_prevent_leader_damage(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = "P1"
        state["phase"] = "main"

        attacker_player = state["players"]["P1"]
        defender_player = state["players"]["P2"]
        attacker = self.engine.build_card_instance("P1", "OP12-119")
        attacker["played_turn"] = 2
        counter_card = self.engine.build_card_instance("P2", "OP06-115")
        discard_cost = self.engine.build_card_instance("P2", "OP12-086")

        attacker_player["board"] = [attacker]
        defender_player["hand"] = [counter_card, discard_cost]

        result = self.engine.apply_action(
            state,
            {"type": "attack", "payload": {"attacker_id": attacker["instance_id"], "target": "leader"}},
        )

        self.assertTrue(result["blocked_or_countered"])
        self.assertEqual(defender_player["life"], 4)
        self.assertEqual(len(result["defense"]["counters"]), 1)
        trashed_ids = [card["instance_id"] for card in defender_player["trash"]]
        self.assertIn(counter_card["instance_id"], trashed_ids)
        self.assertIn(discard_cost["instance_id"], trashed_ids)
        self.assertIsNone(state["battle_context"])
        self.assertEqual(result["battle_context"]["status"], "blocked_or_countered")
        self.assertTrue(any(event.get("counter_used") == counter_card["instance_id"] for event in result["battle_context"]["events"]))
        counter_window_events = [event for event in result["battle_context"]["events"] if event["stage"] == "counter_window"]
        self.assertTrue(any("target_power_after_counter" in event for event in counter_window_events))
        self.assertTrue(any(event["stage"] == "cleanup" for event in result["battle_context"]["events"]))

    def test_trigger_activates_from_life_damage(self) -> None:
        engine = SpyEngine(agent=self.agent, trigger_choice_provider=always_trigger)
        state = engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = "P1"
        state["phase"] = "main"

        attacker_player = state["players"]["P1"]
        defender_player = state["players"]["P2"]
        attacker = engine.build_card_instance("P1", "OP12-119")
        attacker["played_turn"] = 2
        trigger_card = defender_player["life_cards"][0]
        trigger_card["card_id"] = "OP12-112"
        trigger_card["name"] = "Baby 5"
        defender_player["deck"] = [
            engine.build_card_instance("P2", "OP12-086"),
            engine.build_card_instance("P2", "OP12-093"),
        ] + defender_player["deck"]
        defender_player["hand"] = []
        starting_hand = len(defender_player["hand"])

        attacker_player["board"] = [attacker]
        result = engine.apply_action(
            state,
            {"type": "attack", "payload": {"attacker_id": attacker["instance_id"], "target": "leader"}},
        )

        self.assertIsNotNone(result["trigger_result"])
        self.assertEqual(result["trigger_result"]["effect_result"]["effect"], "OP12-112")
        self.assertEqual(len(result["trigger_result"]["effect_result"]["drawn"]), 2)
        self.assertEqual(len(defender_player["hand"]), starting_hand + 2)
        self.assertIsNone(state["battle_context"])
        self.assertEqual(result["battle_context"]["status"], "damage_resolved")
        self.assertTrue(any(event["stage"] == "trigger_window" for event in result["battle_context"]["events"]))
        self.assertTrue(any(event["stage"] == "cleanup" for event in result["battle_context"]["events"]))
        self.assertTrue(any(event.get("trigger_chosen") is True for event in result["battle_context"]["events"]))
        self.assertTrue(any(event.get("resolution") == "trigger" for event in result["battle_context"]["events"]))

    def test_life_damage_without_trigger_records_trigger_decision_and_cleanup(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = "P1"
        state["phase"] = "main"

        attacker_player = state["players"]["P1"]
        defender_player = state["players"]["P2"]
        attacker = self.engine.build_card_instance("P1", "OP12-119")
        attacker["played_turn"] = 2
        attacker_player["board"] = [attacker]
        defender_player["hand"] = []
        defender_player["life_cards"][0]["card_id"] = "OP12-094"
        defender_player["life_cards"][0]["name"] = "Monkey.D.Dragon"

        result = self.engine.apply_action(
            state,
            {"type": "attack", "payload": {"attacker_id": attacker["instance_id"], "target": "leader"}},
        )

        trigger_events = [event for event in result["battle_context"]["events"] if event["stage"] == "trigger_window"]
        self.assertGreaterEqual(len(trigger_events), 2)
        self.assertTrue(any(event.get("trigger_chosen") is False for event in trigger_events))
        self.assertTrue(any(event.get("resolution") == "hand" for event in trigger_events))
        self.assertTrue(any(event["stage"] == "cleanup" for event in result["battle_context"]["events"]))

    def test_ko_resolution_records_chained_ko_effect(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 5
        state["active_player"] = "P1"
        state["phase"] = "main"
        attacker_player = state["players"]["P1"]
        defender_player = state["players"]["P2"]

        attacker = self.engine.build_card_instance("P1", "OP12-119")
        attacker["played_turn"] = 4
        hack = self.engine.build_card_instance("P2", "OP12-089")
        hack["played_turn"] = 4
        hack["state"] = "rested"
        small_target = self.engine.build_card_instance("P1", "OP12-086")

        attacker_player["board"] = [attacker, small_target]
        defender_player["board"] = [hack]
        defender_player["hand"] = []
        defender_player["trash"] = []

        result = self.engine.apply_action(
            state,
            {"type": "attack", "payload": {"attacker_id": attacker["instance_id"], "target": hack["instance_id"]}},
        )

        ko_events = [event for event in result["battle_context"]["events"] if event["stage"] == "ko_resolution"]
        self.assertTrue(any(event.get("ko_target") == hack["instance_id"] for event in ko_events))
        self.assertTrue(any(event.get("ko_effect") == "OP12-089" for event in ko_events))

    def test_ai_main_phase_uses_one_call_per_turn_and_terminates(self) -> None:
        state = self.engine.run_game(max_turns=10, seed=7)

        self.assertEqual(self.agent.calls, 10)
        self.assertTrue(all(self.engine.applied_action_validity))
        self.assertLessEqual(len(state["logs"]), 60)
        self.assertTrue(any(log["action"]["type"] == "end_turn" for log in state["logs"]))

    def test_ai_turn_plan_executes_multiple_actions_with_one_call(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        self.engine.refresh_phase(state)
        self.engine.draw_phase(state)
        self.engine.don_phase(state)
        state["phase"] = "main"

        self.engine.ai_main_phase(state)

        self.assertEqual(self.agent.calls, 1)
        self.assertGreaterEqual(len(state["logs"]), 2)
        self.assertEqual(state["logs"][-1]["action"]["type"], "end_turn")
        self.assertTrue(all(self.engine.applied_action_validity))
        self.assertTrue(state["replay_log"])
        self.assertIn("diff_lines", state["replay_log"][-1])
        self.assertTrue(state["ai_debug_history"])
        self.assertEqual(state["ai_debug_history"][-1]["turn"], 1)

    def test_stale_plan_falls_back_to_end_turn(self) -> None:
        engine = SpyEngine(agent=StalePlanAgent())
        state = engine.create_initial_state(seed=7)
        engine.refresh_phase(state)
        engine.draw_phase(state)
        engine.don_phase(state)
        state["phase"] = "main"

        engine.ai_main_phase(state)

        self.assertEqual(state["logs"][-1]["action"]["type"], "end_turn")
        self.assertTrue(all(engine.applied_action_validity))

    def test_replay_log_records_before_after_and_diff(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        self.engine.refresh_phase(state)
        self.engine.draw_phase(state)
        self.engine.don_phase(state)
        state["phase"] = "main"
        player = state["players"]["P1"]
        target = player["leader"]

        result = self.engine.apply_action(
            state,
            {"type": "attach_don", "payload": {"card_id": target["instance_id"], "amount": 1}},
        )

        self.assertEqual(result["attached_to"], target["instance_id"])
        replay_entry = state["replay_log"][-1]
        self.assertEqual(replay_entry["action"]["type"], "attach_don")
        self.assertIn("before", replay_entry)
        self.assertIn("after", replay_entry)
        self.assertTrue(any("attached" in line.lower() for line in replay_entry["diff_lines"]))


if __name__ == "__main__":
    unittest.main()
