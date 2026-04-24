import unittest

from cli_game import FakePlanningAgent
from glat_engine import GLATEngine
from operator_gui import (
    HUMAN_PLAYER,
    collect_intake_log_lines,
    ensure_human_turn_ready,
    format_summary_lines,
    process_operator_command,
)


class OperatorGuiHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = GLATEngine(agent=FakePlanningAgent())

    def test_ensure_human_turn_ready_starts_manual_turn(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "refresh"

        changed, message = ensure_human_turn_ready(self.engine, state)

        self.assertTrue(changed)
        self.assertEqual(state["phase"], "main")
        self.assertIn("prepared", message.lower())
        self.assertIn("cli_context", state)

    def test_process_operator_command_applies_shorthand(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        state["players"][HUMAN_PLAYER]["hand"] = [self.engine.build_card_instance(HUMAN_PLAYER, "OP12-086")]
        state["players"][HUMAN_PLAYER]["don_deck"] = []
        state["players"][HUMAN_PLAYER]["spent_don"] = []
        state["players"][HUMAN_PLAYER]["attached_don"] = {}
        state["players"][HUMAN_PLAYER]["don_area"] = [f"DON-{index}" for index in range(1, 11)]

        changed, message = process_operator_command(self.engine, state, "played OP12-086 then attach 1 leader")

        self.assertTrue(changed)
        self.assertIn("Applied shorthand", message)
        self.assertEqual(len(state["players"][HUMAN_PLAYER]["board"]), 1)
        self.assertEqual(
            state["players"][HUMAN_PLAYER]["attached_don"][state["players"][HUMAN_PLAYER]["leader"]["instance_id"]],
            1,
        )

    def test_format_summary_and_intake_lines(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        summary_lines = format_summary_lines(state)
        intake_lines = collect_intake_log_lines(state)

        self.assertTrue(summary_lines)
        self.assertIn("Turn", summary_lines[0])
        self.assertEqual(intake_lines, ["No opponent intake history yet."])


if __name__ == "__main__":
    unittest.main()
