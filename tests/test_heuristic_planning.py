import unittest

from ai.planning import HeuristicPlanningAgent
from cli_game import build_local_planning_agent
from glat_engine import GLATEngine
from referee import get_legal_actions


class HeuristicPlanningAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = GLATEngine(agent=HeuristicPlanningAgent())

    def test_scores_and_selects_leader_attack_when_legal(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = "P1"
        state["phase"] = "main"
        state["players"]["P1"]["hand"] = []
        state["players"]["P1"]["board"] = []
        state["players"]["P1"]["don_area"] = []

        legal_actions = get_legal_actions(state, self.engine)
        agent = HeuristicPlanningAgent()

        chosen_index = agent.get_action(state, legal_actions)

        self.assertEqual(
            legal_actions[chosen_index],
            {"type": "attack", "payload": {"attacker_id": "P1-LEADER", "target": "leader"}},
        )
        self.assertTrue(agent.last_scored_actions)
        self.assertEqual(agent.last_scored_actions[0].index, chosen_index)

    def test_turn_plan_keeps_end_turn_as_final_action(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        legal_actions = [
            {"type": "attack", "payload": {"attacker_id": "P1-LEADER", "target": "leader"}},
            {"type": "end_turn", "payload": {}},
        ]

        plan = HeuristicPlanningAgent().get_turn_plan(state, legal_actions)

        self.assertEqual(plan[-1], 1)

    def test_build_local_planning_agent_can_select_heuristic(self) -> None:
        agent = build_local_planning_agent("heuristic")

        self.assertIsInstance(agent, HeuristicPlanningAgent)


if __name__ == "__main__":
    unittest.main()
