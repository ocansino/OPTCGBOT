import unittest

from cli_game import FakePlanningAgent
from glat_engine import GLATEngine
from operator_gui import (
    append_console_entry,
    build_hidden_life_cards,
    card_detail_lines,
    card_tile_lines,
    collect_ai_debug_lines,
    collect_battle_trace_lines,
    collect_latest_diff_lines,
    HUMAN_PLAYER,
    collect_intake_log_lines,
    collect_replay_log_lines,
    ensure_human_turn_ready,
    format_console_lines,
    format_summary_lines,
    process_console_command,
    process_operator_command,
    replay_entry_label,
    replay_snapshot_to_display_state,
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

    def test_console_command_records_user_and_system_entries(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        state["players"][HUMAN_PLAYER]["hand"] = [self.engine.build_card_instance(HUMAN_PLAYER, "OP12-086")]
        state["players"][HUMAN_PLAYER]["don_deck"] = []
        state["players"][HUMAN_PLAYER]["spent_don"] = []
        state["players"][HUMAN_PLAYER]["attached_don"] = {}
        state["players"][HUMAN_PLAYER]["don_area"] = [f"DON-{index}" for index in range(1, 11)]

        changed, message = process_console_command(self.engine, state, "played OP12-086")

        self.assertTrue(changed)
        self.assertIn("Applied shorthand", message)
        self.assertEqual(len(state["command_console"]), 2)
        self.assertEqual(state["command_console"][0]["speaker"], "You")
        self.assertEqual(state["command_console"][1]["speaker"], "System")
        self.assertTrue(any("played OP12-086" in line for line in format_console_lines(state)))

    def test_physical_reported_play_can_create_known_card_outside_hand(self) -> None:
        state = self.engine.create_initial_state(seed=7, match_mode="physical_reported")
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        state["players"][HUMAN_PLAYER]["hand"] = []

        changed, message = process_console_command(self.engine, state, "play OP12-021")

        self.assertTrue(changed)
        self.assertIn("Recorded physical play", message)
        self.assertEqual(state["players"][HUMAN_PLAYER]["board"][-1]["card_id"], "OP12-021")
        self.assertEqual(state["replay_log"][-1]["action"]["type"], "physical_reported_play")

    def test_digital_strict_does_not_create_untracked_physical_play(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        state["players"][HUMAN_PLAYER]["hand"] = []

        changed, message = process_console_command(self.engine, state, "play OP12-021")

        self.assertFalse(changed)
        self.assertIn("physical_reported", message)
        self.assertFalse(
            any(card["card_id"] == "OP12-021" for card in state["players"][HUMAN_PLAYER]["board"])
        )

    def test_unsupported_effect_prompt_is_recorded_for_physical_card(self) -> None:
        state = self.engine.create_initial_state(seed=7, match_mode="physical_reported")
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"

        changed, message = process_console_command(self.engine, state, "play OP12-021")

        self.assertTrue(changed)
        self.assertIn("not auto-resolved", message)
        self.assertEqual(state["pending_console_prompt"]["type"], "unsupported_effect")
        self.assertEqual(state["pending_console_prompt"]["card_id"], "OP12-021")

    def test_skip_pending_unsupported_effect_clears_prompt(self) -> None:
        state = self.engine.create_initial_state(seed=7, match_mode="physical_reported")
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"

        changed, _ = process_console_command(self.engine, state, "play OP12-021")
        self.assertTrue(changed)

        changed, message = process_console_command(self.engine, state, "skip effect")

        self.assertTrue(changed)
        self.assertIn("Skipped", message)
        self.assertIsNone(state["pending_console_prompt"])

    def test_console_entry_helper_numbers_entries(self) -> None:
        state = self.engine.create_initial_state(seed=7)

        first = append_console_entry(state, "System", "Hello")
        second = append_console_entry(state, "AI", "Thinking")

        self.assertEqual(first["index"], 1)
        self.assertEqual(second["index"], 2)

    def test_format_summary_and_intake_lines(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        summary_lines = format_summary_lines(state)
        intake_lines = collect_intake_log_lines(state)
        battle_lines = collect_battle_trace_lines(state)

        self.assertTrue(summary_lines)
        self.assertIn("Turn", summary_lines[0])
        self.assertEqual(intake_lines, ["No opponent intake history yet."])
        self.assertEqual(battle_lines, ["No battle trace yet."])

    def test_collect_battle_trace_lines_after_attack(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        attacker = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-119")
        attacker["played_turn"] = 2
        state["players"][HUMAN_PLAYER]["board"] = [attacker]

        changed, _ = process_operator_command(self.engine, state, "attack OP12-119 leader")

        self.assertTrue(changed)
        battle_lines = collect_battle_trace_lines(state)
        self.assertTrue(any("Battle | status" in line for line in battle_lines))
        self.assertTrue(any("damage_resolution" in line or "cleanup" in line for line in battle_lines))

    def test_collect_replay_and_diff_lines_after_command(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        state["players"][HUMAN_PLAYER]["don_area"] = ["DON-01"]
        state["players"][HUMAN_PLAYER]["don_deck"] = state["players"][HUMAN_PLAYER]["don_deck"][1:]

        changed, _ = process_operator_command(self.engine, state, "attach 1 leader")

        self.assertTrue(changed)
        replay_lines = collect_replay_log_lines(state)
        diff_lines = collect_latest_diff_lines(state)
        self.assertTrue(any("attach_don" in line for line in replay_lines))
        self.assertTrue(any("attached" in line.lower() for line in diff_lines))

    def test_collect_ai_debug_lines_after_ai_main_phase(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        self.engine.refresh_phase(state)
        self.engine.draw_phase(state)
        self.engine.don_phase(state)
        state["phase"] = "main"

        self.engine.ai_main_phase(state)

        ai_debug_lines = collect_ai_debug_lines(state)
        self.assertTrue(any("planned" in line for line in ai_debug_lines))

    def test_card_tile_and_detail_lines_include_operational_fields(self) -> None:
        card = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-086")
        card["attached_don"] = 1

        tile = card_tile_lines(card)
        details = card_detail_lines(card, HUMAN_PLAYER, "hand")

        self.assertIn("OP12-086", tile[0])
        self.assertTrue(any("DON 1" in line for line in tile))
        self.assertTrue(any("Location: P2 / hand" in line for line in details))
        self.assertTrue(any("instance_id" in line for line in details))

    def test_hidden_life_cards_do_not_expose_card_identity(self) -> None:
        hidden_life = build_hidden_life_cards(HUMAN_PLAYER, 2)

        self.assertEqual(len(hidden_life), 2)
        self.assertTrue(all(card["face_down"] for card in hidden_life))
        self.assertEqual(hidden_life[0]["card_id"], "Life")
        self.assertIn("Face-down", card_tile_lines(hidden_life[0])[1])
        self.assertTrue(any("Hidden until revealed" in line for line in card_detail_lines(hidden_life[0])))

    def test_replay_snapshot_to_display_state_supports_board_renderer_shape(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        state["players"][HUMAN_PLAYER]["don_area"] = ["DON-01"]
        state["players"][HUMAN_PLAYER]["don_deck"] = state["players"][HUMAN_PLAYER]["don_deck"][1:]

        changed, _ = process_operator_command(self.engine, state, "attach 1 leader")

        self.assertTrue(changed)
        entry = state["replay_log"][-1]
        display_state = replay_snapshot_to_display_state(entry["after"])
        self.assertEqual(display_state["turn"], entry["turn"])
        self.assertIn(HUMAN_PLAYER, display_state["players"])
        self.assertEqual(len(display_state["players"][HUMAN_PLAYER]["hand"]), entry["after"]["players"][HUMAN_PLAYER]["hand_count"])
        self.assertTrue(all(card["face_down"] for card in display_state["players"][HUMAN_PLAYER]["life_cards"]))
        self.assertIn("Replay #", replay_entry_label(entry))


if __name__ == "__main__":
    unittest.main()
