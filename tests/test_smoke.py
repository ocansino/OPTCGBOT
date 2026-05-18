import unittest

from ai.planning import HeuristicPlanningAgent
from cli_game import FakePlanningAgent, build_local_planning_agent
from glat_engine import GLATEngine
from operator_gui import collect_battle_trace_lines, process_operator_command
from web_app import WebMatchSession


def always_trigger(**_kwargs) -> bool:
    return True


def manual_two_card_counter(**_kwargs):
    return {"manual_counter_cards": 2}


class SmokeTests(unittest.TestCase):
    def test_initial_setup_uses_separate_ai_and_player_decks(self) -> None:
        engine = GLATEngine(agent=FakePlanningAgent())
        state = engine.create_initial_state(seed=7)

        self.assertEqual(state["players"]["P1"]["leader"]["card_id"], "OP12-081")
        self.assertEqual(state["players"]["P2"]["leader"]["card_id"], "OP09-062")
        self.assertEqual(len(state["players"]["P1"]["hand"]), 5)
        self.assertEqual(len(state["players"]["P2"]["hand"]), 5)

    def test_player_nico_robin_banishes_but_ai_koala_does_not(self) -> None:
        engine = GLATEngine(agent=FakePlanningAgent(), trigger_choice_provider=always_trigger)
        state = engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["phase"] = "main"

        state["active_player"] = "P2"
        state["players"]["P1"]["hand"] = []
        state["players"]["P1"]["board"] = []
        result = engine.apply_action(
            state,
            {"type": "attack", "payload": {"attacker_id": "P2-LEADER", "target": "leader"}},
        )
        self.assertTrue(result["banish"])
        self.assertEqual(result["trigger_result"], None)

        state = engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["phase"] = "main"
        state["active_player"] = "P1"
        state["players"]["P2"]["hand"] = []
        state["players"]["P2"]["board"] = []
        state["players"]["P2"]["life_cards"][0]["card_id"] = "OP12-112"
        result = engine.apply_action(
            state,
            {"type": "attack", "payload": {"attacker_id": "P1-LEADER", "target": "leader"}},
        )
        self.assertFalse(result["banish"])
        self.assertNotEqual(result["trigger_result"], None)

    def test_cockpit_command_can_record_physical_play(self) -> None:
        session = WebMatchSession(ai_mode="fake", auto_load=False)
        response = session.submit_command("played P-045")

        self.assertTrue(response["ok"])
        self.assertEqual(response["state"]["players"]["P2"]["board"][0]["card_id"], "P-045")
        self.assertIsNone(response["prompt"])

        response = session.submit_command("heal 1 P2")
        self.assertTrue(response["ok"])

    def test_operator_correction_can_power_individual_card(self) -> None:
        engine = GLATEngine(agent=FakePlanningAgent())
        state = engine.create_initial_state(seed=7, match_mode="physical_reported")
        process_operator_command(engine, state, "played OP12-086")
        card = state["players"]["P2"]["board"][0]

        changed, message = process_operator_command(engine, state, f"power P2 {card['instance_id']} +1000")

        self.assertTrue(changed, message)
        self.assertEqual(card["manual_power_bonus"], 1000)

    def test_manual_status_effect_commands_affect_refresh_and_attack(self) -> None:
        engine = GLATEngine(agent=FakePlanningAgent())
        state = engine.create_initial_state(seed=7, match_mode="physical_reported")
        process_operator_command(engine, state, "played OP12-086")
        card = state["players"]["P2"]["board"][0]

        changed, message = process_operator_command(engine, state, f"cannot attack {card['instance_id']}")
        self.assertTrue(changed, message)
        state["active_player"] = "P2"
        state["turn"] = 3
        state["phase"] = "main"
        self.assertFalse(
            engine.is_valid_action(
                state,
                {"type": "attack", "payload": {"attacker_id": card["instance_id"], "target": "leader"}},
            )
        )

        changed, message = process_operator_command(engine, state, f"freeze {card['instance_id']}")
        self.assertTrue(changed, message)
        card["state"] = "rested"
        engine.refresh_phase(state)
        self.assertEqual(card["state"], "rested")
        self.assertFalse(card["freeze"])

    def test_manual_counter_card_count_reduces_human_hand(self) -> None:
        engine = GLATEngine(agent=FakePlanningAgent(), defense_choice_provider=manual_two_card_counter)
        state = engine.create_initial_state(seed=7)
        state["active_player"] = "P1"
        state["turn"] = 3
        state["phase"] = "main"
        state["players"]["P2"]["board"] = []
        before_hand = len(state["players"]["P2"]["hand"])

        result = engine.apply_action(
            state,
            {"type": "attack", "payload": {"attacker_id": "P1-LEADER", "target": "leader"}},
        )

        self.assertTrue(result["blocked_or_countered"])
        self.assertEqual(len(state["players"]["P2"]["hand"]), before_hand - 2)
        self.assertEqual(result["defense"]["counters"][0]["manual_card_count"], 2)

    def test_local_planning_modes_are_available(self) -> None:
        self.assertIsInstance(build_local_planning_agent("fake"), FakePlanningAgent)
        self.assertIsInstance(build_local_planning_agent("heuristic"), HeuristicPlanningAgent)

    def test_battle_trace_reports_effect_ko_target(self) -> None:
        engine = GLATEngine(agent=FakePlanningAgent())
        state = engine.create_initial_state(seed=7)
        target = engine.build_card_instance("P2", "OP12-086")
        state["players"]["P2"]["trash"] = [target]
        state["logs"].append(
            {
                "turn": 1,
                "player": "P1",
                "action": {"type": "play_card", "payload": {"card_id": "P1-CARD-001"}},
                "result": {
                    "played": "EB03-056",
                    "effect_result": {"effect": "EB03-056", "ko_target": target["instance_id"]},
                },
            }
        )

        lines = collect_battle_trace_lines(state)

        self.assertTrue(any("K.O.'d P2 OP12-086" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
