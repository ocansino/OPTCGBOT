import unittest

from glat_engine import GLATEngine, InvalidActionError


class GLATEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = GLATEngine()

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


if __name__ == "__main__":
    unittest.main()
