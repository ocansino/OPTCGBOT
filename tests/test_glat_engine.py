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
        self.assertEqual(len(player["hand"]), 0)
        self.assertEqual(len(player["trash"]), 1)
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
        self.assertEqual(len(player["life_cards"]), 1)

        hand_card = player["hand"][0]["instance_id"]
        self.engine.manual_move_card(state, "P2", hand_card, "hand", "deck", position="top")
        self.assertEqual(player["deck"][0]["instance_id"], hand_card)

        board_card = self.engine.build_card_instance("P2", "OP12-086")
        player["board"].append(board_card)
        self.engine.validate_state(state)
        self.engine.manual_ko(state, "P2", board_card["instance_id"])
        self.assertEqual(player["trash"][-1]["instance_id"], board_card["instance_id"])

        self.engine.validate_state(state)

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
