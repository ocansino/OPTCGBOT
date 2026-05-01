import contextlib
import io
import unittest

from cli_game import FakePlanningAgent
from glat_engine import GLATEngine
from operator_gui import (
    AI_PLAYER,
    append_console_entry,
    build_hidden_life_cards,
    card_detail_lines,
    card_tile_lines,
    collect_ai_debug_lines,
    collect_battle_trace_lines,
    collect_latest_diff_lines,
    collect_replay_diff_lines,
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

    def test_physical_reported_play_can_enter_rested(self) -> None:
        state = self.engine.create_initial_state(seed=7, match_mode="physical_reported")
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        state["players"][HUMAN_PLAYER]["hand"] = []

        changed, message = process_console_command(self.engine, state, "play rested OP12-021")

        self.assertTrue(changed)
        self.assertIn("rested", message)
        self.assertEqual(state["players"][HUMAN_PLAYER]["board"][-1]["card_id"], "OP12-021")
        self.assertEqual(state["players"][HUMAN_PLAYER]["board"][-1]["state"], "rested")
        self.assertEqual(state["replay_log"][-1]["result"]["state"], "rested")

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
        self.assertTrue(any("PENDING" in line for line in format_console_lines(state)))

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
        self.assertEqual(state["unsupported_effect_resolutions"][-1]["resolution"], "skipped")

    def test_manual_done_pending_unsupported_effect_clears_prompt(self) -> None:
        state = self.engine.create_initial_state(seed=7, match_mode="physical_reported")
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"

        changed, _ = process_console_command(self.engine, state, "play OP12-021")
        self.assertTrue(changed)

        changed, message = process_console_command(self.engine, state, "manual done")

        self.assertTrue(changed)
        self.assertIn("manually resolved", message)
        self.assertIsNone(state["pending_console_prompt"])
        self.assertEqual(state["unsupported_effect_resolutions"][-1]["resolution"], "manual_resolved")

    def test_implement_later_records_unsupported_effect_backlog(self) -> None:
        state = self.engine.create_initial_state(seed=7, match_mode="physical_reported")
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"

        changed, _ = process_console_command(self.engine, state, "play OP12-021")
        self.assertTrue(changed)

        changed, message = process_console_command(self.engine, state, "implement later")

        self.assertTrue(changed)
        self.assertIn("later", message)
        self.assertIsNone(state["pending_console_prompt"])
        self.assertEqual(state["unsupported_effect_backlog"][-1]["card_id"], "OP12-021")
        self.assertEqual(state["unsupported_effect_resolutions"][-1]["resolution"], "implement_later")

    def test_note_pending_unsupported_effect_records_resolution(self) -> None:
        state = self.engine.create_initial_state(seed=7, match_mode="physical_reported")
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"

        changed, _ = process_console_command(self.engine, state, "play OP12-021")
        self.assertTrue(changed)

        changed, message = process_console_command(self.engine, state, "note blocker text ignored")

        self.assertTrue(changed)
        self.assertIn("Recorded note", message)
        self.assertIsNone(state["pending_console_prompt"])
        self.assertEqual(state["unsupported_effect_resolutions"][-1]["resolution"], "note_only")
        self.assertEqual(state["unsupported_effect_resolutions"][-1]["note"], "blocker text ignored")

    def test_correct_life_command_records_audited_correction(self) -> None:
        state = self.engine.create_initial_state(seed=7, match_mode="physical_reported")

        changed, message = process_console_command(self.engine, state, "correct life P2 2")

        self.assertTrue(changed)
        self.assertIn("Correction recorded", message)
        self.assertEqual(state["players"][HUMAN_PLAYER]["life"], 2)
        self.assertEqual(len(state["players"][HUMAN_PLAYER]["life_cards"]), 2)
        self.assertEqual(state["operator_corrections"][-1]["command"], "correct life P2 2")
        self.assertEqual(state["replay_log"][-1]["action"]["type"], "operator_correction_life")

    def test_set_correction_command_updates_board_state(self) -> None:
        state = self.engine.create_initial_state(seed=7, match_mode="physical_reported")
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        card = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-119")
        card["played_turn"] = 2
        state["players"][HUMAN_PLAYER]["board"] = [card]

        changed, message = process_console_command(self.engine, state, "set OP12-119 rested")

        self.assertTrue(changed)
        self.assertIn("Correction recorded", message)
        self.assertEqual(state["players"][HUMAN_PLAYER]["board"][0]["state"], "rested")
        self.assertEqual(state["operator_corrections"][-1]["command"], "set OP12-119 rested")

    def test_remove_correction_moves_card_to_trash(self) -> None:
        state = self.engine.create_initial_state(seed=7, match_mode="physical_reported")
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"

        changed, _ = process_console_command(self.engine, state, "play OP12-021")
        self.assertTrue(changed)

        changed, message = process_console_command(self.engine, state, "remove OP12-021")

        self.assertTrue(changed)
        self.assertIn("Correction recorded", message)
        self.assertFalse(any(card["card_id"] == "OP12-021" for card in state["players"][HUMAN_PLAYER]["board"]))
        self.assertTrue(any(card["card_id"] == "OP12-021" for card in state["players"][HUMAN_PLAYER]["trash"]))

    def test_find_command_lists_ambiguous_correction_matches(self) -> None:
        state = self.engine.create_initial_state(seed=7, match_mode="physical_reported")
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"

        for _ in range(2):
            changed, _ = process_console_command(self.engine, state, "play OP12-021")
            self.assertTrue(changed)
            changed, _ = process_console_command(self.engine, state, "manual done")
            self.assertTrue(changed)

        changed, message = process_console_command(self.engine, state, "remove OP12-021")
        self.assertFalse(changed)
        self.assertIn("Ambiguous", message)
        self.assertIn("find OP12-021", message)

        changed, message = process_console_command(self.engine, state, "find OP12-021 board")
        self.assertFalse(changed)
        self.assertIn("Matches", message)
        self.assertIn("P2/board", message)

    def test_physical_reported_full_turn_pressure_test_reaches_ai_response(self) -> None:
        state = self.engine.create_initial_state(seed=7, match_mode="physical_reported")
        self.engine.run_turn(state)
        self.assertEqual(state["active_player"], HUMAN_PLAYER)

        changed, message = ensure_human_turn_ready(self.engine, state)
        self.assertTrue(changed)
        self.assertEqual(state["phase"], "main")
        self.assertIn("prepared", message.lower())

        commands = [
            "play OP12-021",
            "manual done",
            "attach 1 leader",
            "attack leader",
            "end",
        ]
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            for command in commands:
                changed, message = process_console_command(self.engine, state, command)
                self.assertTrue(changed, msg=f"{command}: {message}")

        self.assertEqual(stdout.getvalue(), "")

        self.assertEqual(state["active_player"], AI_PLAYER)
        self.assertIsNone(state.get("pending_console_prompt"))
        self.assertTrue(
            any(card["card_id"] == "OP12-021" for card in state["players"][HUMAN_PLAYER]["board"])
        )

        log_count_before_ai = len(state["logs"])
        self.engine.run_turn(state)

        self.assertEqual(state["active_player"], HUMAN_PLAYER)
        self.assertGreater(len(state["logs"]), log_count_before_ai)
        self.assertTrue(state["ai_debug_history"])
        self.assertTrue(any(entry.get("speaker") == "You" for entry in state["command_console"]))

    def test_physical_reported_multi_turn_pressure_test_with_correction(self) -> None:
        state = self.engine.create_initial_state(seed=7, match_mode="physical_reported")
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            self.engine.run_turn(state)
            for turn_index in range(2):
                self.assertEqual(state["active_player"], HUMAN_PLAYER)
                changed, message = ensure_human_turn_ready(self.engine, state)
                self.assertTrue(changed, msg=message)

                changed, message = process_console_command(self.engine, state, "play OP12-021")
                self.assertTrue(changed, msg=message)
                newest_card = state["players"][HUMAN_PLAYER]["board"][-1]
                if turn_index == 0:
                    changed, message = process_console_command(self.engine, state, "manual done")
                    self.assertTrue(changed, msg=message)
                    changed, message = process_console_command(self.engine, state, "correct life P2 3")
                    self.assertTrue(changed, msg=message)
                    changed, message = process_console_command(
                        self.engine,
                        state,
                        f"set {newest_card['instance_id']} rested",
                    )
                    self.assertTrue(changed, msg=message)
                else:
                    changed, message = process_console_command(self.engine, state, "implement later")
                    self.assertTrue(changed, msg=message)
                    changed, message = process_console_command(
                        self.engine,
                        state,
                        f"remove {newest_card['instance_id']}",
                    )
                    self.assertTrue(changed, msg=message)

                changed, message = process_console_command(self.engine, state, "attach 1 leader")
                self.assertTrue(changed, msg=message)
                changed, message = process_console_command(self.engine, state, "attack leader")
                self.assertTrue(changed, msg=message)
                changed, message = process_console_command(self.engine, state, "end")
                self.assertTrue(changed, msg=message)

                self.assertEqual(state["active_player"], AI_PLAYER)
                self.engine.run_turn(state)

        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(state["active_player"], HUMAN_PLAYER)
        self.assertIsNone(state.get("pending_console_prompt"))
        self.assertTrue(state.get("operator_corrections"))
        self.assertTrue(state.get("unsupported_effect_resolutions"))
        self.assertTrue(state.get("unsupported_effect_backlog"))
        self.assertTrue(
            any(card["card_id"] == "OP12-021" for card in state["players"][HUMAN_PLAYER]["trash"])
        )
        self.assertGreaterEqual(len(state["ai_debug_history"]), 3)

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

    def test_duplicate_attack_reference_uses_only_legal_matching_copy(self) -> None:
        state = self.engine.create_initial_state(seed=7, match_mode="physical_reported")
        state["turn"] = 4
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        older = self.engine.build_card_instance(HUMAN_PLAYER, "OP06-090")
        newer = self.engine.build_card_instance(HUMAN_PLAYER, "OP06-090")
        older["played_turn"] = 3
        newer["played_turn"] = 4
        state["players"][HUMAN_PLAYER]["board"] = [older, newer]

        changed, message = process_console_command(self.engine, state, "attack OP06-090 leader")

        self.assertTrue(changed, msg=message)
        self.assertEqual(older["state"], "rested")
        self.assertEqual(newer["state"], "active")

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

    def test_collect_replay_diff_lines_can_target_selected_entry(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        state["players"][HUMAN_PLAYER]["don_area"] = ["DON-01", "DON-02"]
        state["players"][HUMAN_PLAYER]["don_deck"] = state["players"][HUMAN_PLAYER]["don_deck"][2:]

        changed, _ = process_operator_command(self.engine, state, "attach 1 leader")
        self.assertTrue(changed)
        changed, _ = process_operator_command(self.engine, state, "end")
        self.assertTrue(changed)

        first_diff = collect_replay_diff_lines(state, 0)
        second_diff = collect_replay_diff_lines(state, 1)

        self.assertIn("attach_don", first_diff[0])
        self.assertIn("end_turn", second_diff[0])
        self.assertNotEqual(first_diff[0], second_diff[0])

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
