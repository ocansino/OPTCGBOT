import unittest

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

    def test_turn_progression_runs_ten_turns(self) -> None:
        state = self.engine.run_game(max_turns=10, seed=7)

        self.engine.validate_state(state)
        self.assertEqual(state["turn"], 11)
        self.assertEqual(state["phase"], "end")
        self.assertGreater(len(state["logs"]), 0)

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

    def test_manual_counter_event_adds_battle_power_bonus(self) -> None:
        state = self.engine.create_initial_state(seed=7)
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


if __name__ == "__main__":
    unittest.main()
