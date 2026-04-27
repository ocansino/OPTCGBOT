import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from contextlib import redirect_stdout

import cli_game
from cli_game import HUMAN_PLAYER
from glat_engine import GLATEngine
from referee import get_legal_actions


class OpponentIntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = GLATEngine(agent=cli_game.FakePlanningAgent())

    def test_begin_and_finish_opponent_intake_session(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"

        session = cli_game.begin_opponent_intake_session(state)
        cli_game.log_opponent_intake_event(state, "main", "Played a card")
        cli_game.finish_opponent_intake_session(state, "completed")

        self.assertEqual(session["status"], "completed")
        self.assertEqual(len(session["events"]), 1)
        self.assertEqual(session["events"][0]["summary"], "Played a card")
        self.assertIsNone(cli_game.get_active_opponent_intake(state))

    def test_guided_attack_logs_attack_event(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        attacker = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-119")
        attacker["played_turn"] = 2
        state["players"][HUMAN_PLAYER]["board"] = [attacker]
        state["players"]["P1"]["board"] = []
        cli_game.begin_opponent_intake_session(state)

        with patch("cli_game.choose_from_menu", return_value=0):
            applied = cli_game.guided_attack(self.engine, state)

        self.assertTrue(applied)
        session = cli_game.get_active_opponent_intake(state)
        self.assertIsNotNone(session)
        self.assertEqual(session["events"][-2]["stage"], "attack")
        self.assertIn("Attack declared", session["events"][-2]["summary"])
        self.assertEqual(session["events"][-2]["details"]["action"]["type"], "attack")
        self.assertEqual(session["events"][-1]["stage"], "battle_trace")

    def test_guided_opponent_turn_step_can_finish_turn(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        cli_game.begin_opponent_intake_session(state)

        with TemporaryDirectory() as temp_dir:
            state_out = str(Path(temp_dir) / "cli_game_state.test.json")
            with patch("cli_game.choose_from_menu", return_value=9):
                result = cli_game.guided_opponent_turn_step(self.engine, state, state_out)

        self.assertTrue(result)
        self.assertEqual(state["active_player"], "P1")
        self.assertEqual(state["turn"], 4)
        history = state["cli_context"]["opponent_turn_history"]
        self.assertEqual(history[-1]["status"], "completed")

    def test_print_summary_shows_human_hand_contents(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["players"][HUMAN_PLAYER]["hand"] = [
            self.engine.build_card_instance(HUMAN_PLAYER, "OP12-086")
        ]

        output = StringIO()
        with redirect_stdout(output):
            cli_game.print_summary(state)

        rendered = output.getvalue()
        self.assertIn("Your hand:", rendered)
        self.assertIn("OP12-086", rendered)

    def test_fake_planning_agent_prefers_legal_attack_when_available(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = cli_game.AI_PLAYER
        state["phase"] = "main"
        ai_player = state["players"][cli_game.AI_PLAYER]
        attacker = self.engine.build_card_instance(cli_game.AI_PLAYER, "OP12-119")
        attacker["played_turn"] = 2
        ai_player["board"] = [attacker]
        ai_player["don_area"] = ["P1-DON-01"]
        legal_actions = get_legal_actions(state, self.engine)

        plan = cli_game.FakePlanningAgent().get_turn_plan(state, legal_actions)
        planned_actions = [legal_actions[index]["type"] for index in plan[:-1]]

        self.assertIn("attack", planned_actions)

    def test_fake_planning_agent_plays_card_before_spending_don_on_attacks(self) -> None:
        legal_actions = [
            {"type": "play_card", "payload": {"card_id": "P1-CARD-001"}},
            {"type": "attach_don", "payload": {"card_id": "P1-LEADER", "amount": 1}},
            {"type": "attack", "payload": {"attacker_id": "P1-LEADER", "target": "leader"}},
            {"type": "end_turn", "payload": {}},
        ]
        state = self.engine.create_initial_state(seed=7)

        plan = cli_game.FakePlanningAgent().get_turn_plan(state, legal_actions)

        self.assertEqual(legal_actions[plan[0]]["type"], "play_card")

    def test_handle_shorthand_report_can_play_card_by_card_id(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        state["players"][HUMAN_PLAYER]["hand"] = [self.engine.build_card_instance(HUMAN_PLAYER, "OP12-086")]
        state["players"][HUMAN_PLAYER]["don_deck"] = []
        state["players"][HUMAN_PLAYER]["spent_don"] = []
        state["players"][HUMAN_PLAYER]["attached_don"] = {}
        state["players"][HUMAN_PLAYER]["don_area"] = [f"DON-{index}" for index in range(1, 11)]
        cli_game.begin_opponent_intake_session(state)

        handled = cli_game.handle_shorthand_report(self.engine, state, "played OP12-086")

        self.assertTrue(handled)
        self.assertEqual(len(state["players"][HUMAN_PLAYER]["board"]), 1)
        self.assertEqual(state["players"][HUMAN_PLAYER]["board"][0]["card_id"], "OP12-086")
        self.assertIn("Shorthand play report", state["cli_context"]["opponent_turn_history"][-1]["events"][-1]["summary"])

    def test_handle_shorthand_report_accepts_natural_play_phrase(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        state["players"][HUMAN_PLAYER]["hand"] = [self.engine.build_card_instance(HUMAN_PLAYER, "OP12-086")]
        state["players"][HUMAN_PLAYER]["don_deck"] = []
        state["players"][HUMAN_PLAYER]["spent_don"] = []
        state["players"][HUMAN_PLAYER]["attached_don"] = {}
        state["players"][HUMAN_PLAYER]["don_area"] = [f"DON-{index}" for index in range(1, 11)]
        cli_game.begin_opponent_intake_session(state)

        handled = cli_game.handle_shorthand_report(self.engine, state, "I played OP12-086")

        self.assertTrue(handled)
        self.assertEqual(state["players"][HUMAN_PLAYER]["board"][0]["card_id"], "OP12-086")

    def test_handle_shorthand_report_can_attack_leader(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        attacker = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-119")
        attacker["played_turn"] = 2
        state["players"][HUMAN_PLAYER]["board"] = [attacker]
        cli_game.begin_opponent_intake_session(state)

        handled = cli_game.handle_shorthand_report(self.engine, state, "attack OP12-119 leader")

        self.assertTrue(handled)
        events = state["cli_context"]["opponent_turn_history"][-1]["events"]
        self.assertEqual(events[-2]["stage"], "attack")
        self.assertIn("Shorthand attack report", events[-2]["summary"])
        self.assertEqual(events[-1]["stage"], "battle_trace")
        self.assertTrue(events[-1]["details"]["battle_lines"])

    def test_handle_shorthand_report_accepts_swing_phrase(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        attacker = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-119")
        attacker["played_turn"] = 2
        state["players"][HUMAN_PLAYER]["board"] = [attacker]
        cli_game.begin_opponent_intake_session(state)

        handled = cli_game.handle_shorthand_report(self.engine, state, "swing OP12-119 at your leader")

        self.assertTrue(handled)
        self.assertIn("Shorthand attack report", state["cli_context"]["opponent_turn_history"][-1]["events"][-2]["summary"])

    def test_handle_shorthand_report_can_use_counter_on_leader(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = "P1"
        state["phase"] = "main"
        counter_card = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-098")
        state["players"][HUMAN_PLAYER]["hand"] = [counter_card]
        cli_game.begin_opponent_intake_session(state)

        handled = cli_game.handle_shorthand_report(self.engine, state, "counter OP12-098 leader")

        self.assertTrue(handled)
        self.assertEqual(len(state["players"][HUMAN_PLAYER]["trash"]), 1)
        self.assertEqual(state["players"][HUMAN_PLAYER]["trash"][0]["card_id"], "OP12-098")
        self.assertIn("Shorthand counter report", state["cli_context"]["opponent_turn_history"][-1]["events"][-1]["summary"])

    def test_handle_shorthand_report_accepts_natural_counter_phrase(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = "P1"
        state["phase"] = "main"
        counter_card = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-098")
        state["players"][HUMAN_PLAYER]["hand"] = [counter_card]
        cli_game.begin_opponent_intake_session(state)

        handled = cli_game.handle_shorthand_report(self.engine, state, "use counter OP12-098 on leader")

        self.assertTrue(handled)
        self.assertEqual(state["players"][HUMAN_PLAYER]["trash"][0]["card_id"], "OP12-098")

    def test_handle_shorthand_report_can_resolve_life_phrase(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        ai_starting_life = state["players"]["P1"]["life"]
        cli_game.begin_opponent_intake_session(state)

        handled = cli_game.handle_shorthand_report(self.engine, state, "your leader took 1 life")

        self.assertTrue(handled)
        self.assertEqual(state["players"]["P1"]["life"], ai_starting_life - 1)
        self.assertIn("Shorthand life report", state["cli_context"]["opponent_turn_history"][-1]["events"][-1]["summary"])

    def test_handle_shorthand_report_prompts_when_multiple_cards_match(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        first = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-086")
        second = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-086")
        state["players"][HUMAN_PLAYER]["hand"] = [first, second]
        state["players"][HUMAN_PLAYER]["don_deck"] = []
        state["players"][HUMAN_PLAYER]["spent_don"] = []
        state["players"][HUMAN_PLAYER]["attached_don"] = {}
        state["players"][HUMAN_PLAYER]["don_area"] = [f"DON-{index}" for index in range(1, 11)]
        cli_game.begin_opponent_intake_session(state)

        with patch("cli_game.choose_from_menu", return_value=1):
            handled = cli_game.handle_shorthand_report(self.engine, state, "played OP12-086")

        self.assertTrue(handled)
        self.assertEqual(state["players"][HUMAN_PLAYER]["board"][0]["instance_id"], second["instance_id"])

    def test_handle_shorthand_report_can_reuse_same_attacker_memory(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        first = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-119")
        second = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-089")
        first["played_turn"] = 2
        second["played_turn"] = 2
        second["state"] = "rested"
        state["players"][HUMAN_PLAYER]["board"] = [first, second]
        cli_game.begin_opponent_intake_session(state)

        handled_first = cli_game.handle_shorthand_report(self.engine, state, "attack OP12-119 leader")
        handled_second = cli_game.handle_shorthand_report(self.engine, state, "set same attacker rested")

        self.assertTrue(handled_first)
        self.assertTrue(handled_second)
        self.assertEqual(first["state"], "rested")

    def test_handle_shorthand_report_can_choose_other_matching_card(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        first = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-086")
        second = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-086")
        state["players"][HUMAN_PLAYER]["hand"] = [first, second]
        state["players"][HUMAN_PLAYER]["don_deck"] = []
        state["players"][HUMAN_PLAYER]["spent_don"] = []
        state["players"][HUMAN_PLAYER]["attached_don"] = {}
        state["players"][HUMAN_PLAYER]["don_area"] = [f"DON-{index}" for index in range(1, 11)]
        cli_game.begin_opponent_intake_session(state)

        with patch("cli_game.choose_from_menu", return_value=0):
            handled_first = cli_game.handle_shorthand_report(self.engine, state, "played OP12-086")

        replacement = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-086")
        state["players"][HUMAN_PLAYER]["hand"] = [second, replacement]

        with patch("cli_game.choose_from_menu", return_value=0):
            handled_second = cli_game.handle_shorthand_report(self.engine, state, "played other Koala")

        self.assertTrue(handled_first)
        self.assertTrue(handled_second)
        self.assertEqual(len(state["players"][HUMAN_PLAYER]["board"]), 2)

    def test_handle_shorthand_report_can_discard_by_phrase(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        card = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-086")
        state["players"][HUMAN_PLAYER]["hand"] = [card]
        cli_game.begin_opponent_intake_session(state)

        handled = cli_game.handle_shorthand_report(self.engine, state, "discard OP12-086")

        self.assertTrue(handled)
        self.assertEqual(state["players"][HUMAN_PLAYER]["trash"][-1]["card_id"], "OP12-086")

    def test_handle_shorthand_report_can_draw_by_phrase(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        starting_hand = len(state["players"][HUMAN_PLAYER]["hand"])
        cli_game.begin_opponent_intake_session(state)

        handled = cli_game.handle_shorthand_report(self.engine, state, "I drew 1")

        self.assertTrue(handled)
        self.assertEqual(len(state["players"][HUMAN_PLAYER]["hand"]), starting_hand + 1)

    def test_handle_shorthand_report_can_trash_top_by_phrase(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        starting_trash = len(state["players"][HUMAN_PLAYER]["trash"])
        cli_game.begin_opponent_intake_session(state)

        handled = cli_game.handle_shorthand_report(self.engine, state, "I trashed top 2")

        self.assertTrue(handled)
        self.assertEqual(len(state["players"][HUMAN_PLAYER]["trash"]), starting_trash + 2)

    def test_handle_shorthand_report_can_set_state_by_phrase(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        card = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-119")
        card["played_turn"] = 2
        state["players"][HUMAN_PLAYER]["board"] = [card]
        cli_game.begin_opponent_intake_session(state)

        handled = cli_game.handle_shorthand_report(self.engine, state, "set OP12-119 rested")

        self.assertTrue(handled)
        self.assertEqual(card["state"], "rested")

    def test_handle_shorthand_report_can_use_same_target_memory(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        attacker = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-119")
        attacker["played_turn"] = 2
        target = self.engine.build_card_instance("P1", "OP12-089")
        target["played_turn"] = 2
        target["state"] = "rested"
        state["players"][HUMAN_PLAYER]["board"] = [attacker]
        state["players"]["P1"]["board"] = [target]
        cli_game.begin_opponent_intake_session(state)

        handled_attack = cli_game.handle_shorthand_report(self.engine, state, "attack OP12-119 OP12-089")
        handled_ko = cli_game.handle_shorthand_report(self.engine, state, "ko same target")

        self.assertTrue(handled_attack)
        self.assertTrue(handled_ko)
        self.assertEqual(len(state["players"]["P1"]["board"]), 0)

    def test_handle_shorthand_report_can_use_that_one_memory(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        card = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-119")
        card["played_turn"] = 2
        state["players"][HUMAN_PLAYER]["board"] = [card]
        cli_game.begin_opponent_intake_session(state)

        handled_first = cli_game.handle_shorthand_report(self.engine, state, "set OP12-119 rested")
        handled_second = cli_game.handle_shorthand_report(self.engine, state, "set that one active")

        self.assertTrue(handled_first)
        self.assertTrue(handled_second)
        self.assertEqual(card["state"], "active")

    def test_handle_shorthand_report_can_use_other_one_memory(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        first = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-086")
        second = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-086")
        state["players"][HUMAN_PLAYER]["hand"] = [first, second]
        cli_game.begin_opponent_intake_session(state)

        with patch("cli_game.choose_from_menu", return_value=0):
            handled_first = cli_game.handle_shorthand_report(self.engine, state, "discard OP12-086")
        handled_second = cli_game.handle_shorthand_report(self.engine, state, "discard other one")

        self.assertTrue(handled_first)
        self.assertTrue(handled_second)
        self.assertEqual(len(state["players"][HUMAN_PLAYER]["hand"]), 0)
        self.assertEqual(len(state["players"][HUMAN_PLAYER]["trash"]), 2)

    def test_handle_shorthand_report_can_chain_play_and_attach(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        state["players"][HUMAN_PLAYER]["hand"] = [self.engine.build_card_instance(HUMAN_PLAYER, "OP12-086")]
        state["players"][HUMAN_PLAYER]["don_deck"] = []
        state["players"][HUMAN_PLAYER]["spent_don"] = []
        state["players"][HUMAN_PLAYER]["attached_don"] = {}
        state["players"][HUMAN_PLAYER]["don_area"] = [f"DON-{index}" for index in range(1, 11)]
        cli_game.begin_opponent_intake_session(state)

        handled = cli_game.handle_shorthand_report(self.engine, state, "played OP12-086 then attach 1 leader")

        self.assertTrue(handled)
        self.assertEqual(len(state["players"][HUMAN_PLAYER]["board"]), 1)
        self.assertEqual(state["players"][HUMAN_PLAYER]["attached_don"][state["players"][HUMAN_PLAYER]["leader"]["instance_id"]], 1)

    def test_handle_shorthand_report_can_chain_attack_and_counter_same_target(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = "P1"
        state["phase"] = "main"
        counter_card = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-098")
        state["players"][HUMAN_PLAYER]["hand"] = [counter_card]
        cli_game.begin_opponent_intake_session(state)

        handled = cli_game.handle_shorthand_report(self.engine, state, "your leader took 1 life then counter OP12-098 leader")

        self.assertTrue(handled)
        self.assertEqual(state["players"][HUMAN_PLAYER]["trash"][-1]["card_id"], "OP12-098")

    def test_handle_shorthand_report_chains_stop_on_first_failure(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        state["players"][HUMAN_PLAYER]["hand"] = [self.engine.build_card_instance(HUMAN_PLAYER, "OP12-086")]
        state["players"][HUMAN_PLAYER]["don_deck"] = []
        state["players"][HUMAN_PLAYER]["spent_don"] = []
        state["players"][HUMAN_PLAYER]["attached_don"] = {}
        state["players"][HUMAN_PLAYER]["don_area"] = [f"DON-{index}" for index in range(1, 11)]
        cli_game.begin_opponent_intake_session(state)

        handled = cli_game.handle_shorthand_report(self.engine, state, "played OP12-086 then nonsense command")

        self.assertTrue(handled)
        self.assertEqual(len(state["players"][HUMAN_PLAYER]["board"]), 1)

    def test_handle_shorthand_report_can_use_same_target_when_last_target_was_leader(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = "P1"
        state["phase"] = "main"
        counter_card = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-098")
        state["players"][HUMAN_PLAYER]["hand"] = [counter_card]
        cli_game.begin_opponent_intake_session(state)

        handled_attack = cli_game.handle_shorthand_report(self.engine, state, "your leader took 1 life")
        handled_counter = cli_game.handle_shorthand_report(self.engine, state, "counter OP12-098 same target")

        self.assertTrue(handled_attack)
        self.assertTrue(handled_counter)
        self.assertEqual(state["players"][HUMAN_PLAYER]["trash"][-1]["card_id"], "OP12-098")

    def test_pressure_full_reported_opponent_turn_records_battle_trace_and_completes(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        human = state["players"][HUMAN_PLAYER]
        ai = state["players"]["P1"]

        play_card = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-086")
        human["hand"] = [play_card]
        human["don_deck"] = []
        human["spent_don"] = []
        human["attached_don"] = {}
        human["don_area"] = [f"DON-{index}" for index in range(1, 11)]
        ai["hand"] = []
        ai["board"] = []
        cli_game.begin_opponent_intake_session(state)

        self.assertTrue(cli_game.handle_shorthand_report(self.engine, state, "played OP12-086 then attach 1 leader"))
        self.assertTrue(cli_game.handle_shorthand_report(self.engine, state, "attack leader"))

        end_action = {"type": "end_turn", "payload": {}}
        self.assertTrue(cli_game.run_logged_human_action(self.engine, state, end_action, "end", "Opponent turn ended."))
        self.engine.end_phase(state)
        cli_game.finish_opponent_intake_session(state, "completed")

        history = state["cli_context"]["opponent_turn_history"][-1]
        self.assertEqual(history["status"], "completed")
        self.assertEqual(state["active_player"], "P1")
        self.assertTrue(any(event["stage"] == "attack" for event in history["events"]))
        self.assertTrue(any(event["stage"] == "battle_trace" for event in history["events"]))
        battle_trace_event = next(event for event in history["events"] if event["stage"] == "battle_trace")
        self.assertTrue(any("damage_resolution" in line for line in battle_trace_event["details"]["battle_lines"]))

    def test_pressure_attack_into_blocker_records_redirect_trace(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        human = state["players"][HUMAN_PLAYER]
        ai = state["players"]["P1"]

        attacker = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-119")
        attacker["played_turn"] = 2
        blocker = self.engine.build_card_instance("P1", "OP12-089")
        blocker["played_turn"] = 2
        human["board"] = [attacker]
        ai["board"] = [blocker]
        ai["hand"] = []
        cli_game.begin_opponent_intake_session(state)

        self.assertTrue(cli_game.handle_shorthand_report(self.engine, state, "attack OP12-119 leader"))

        history = state["cli_context"]["opponent_turn_history"][-1]
        battle_trace_event = history["events"][-1]
        self.assertEqual(battle_trace_event["stage"], "battle_trace")
        self.assertTrue(any("chosen_blocker" in line for line in battle_trace_event["details"]["battle_lines"]))
        self.assertEqual(state["players"]["P1"]["life"], 4)

    def test_pressure_attack_into_trigger_records_trigger_resolution_trace(self) -> None:
        state = self.engine.create_initial_state(seed=7)
        state["turn"] = 3
        state["active_player"] = HUMAN_PLAYER
        state["phase"] = "main"
        human = state["players"][HUMAN_PLAYER]
        ai = state["players"]["P1"]

        attacker = self.engine.build_card_instance(HUMAN_PLAYER, "OP12-119")
        attacker["played_turn"] = 2
        human["board"] = [attacker]
        ai["hand"] = []
        trigger_card = ai["life_cards"][0]
        trigger_card["card_id"] = "OP12-112"
        trigger_card["name"] = "Baby 5"
        ai["deck"] = [
            self.engine.build_card_instance("P1", "OP12-086"),
            self.engine.build_card_instance("P1", "OP12-093"),
        ] + ai["deck"]
        cli_game.begin_opponent_intake_session(state)
        original_trigger_provider = self.engine.trigger_choice_provider
        self.engine.trigger_choice_provider = lambda **_: True
        try:
            self.assertTrue(cli_game.handle_shorthand_report(self.engine, state, "attack OP12-119 leader"))
        finally:
            self.engine.trigger_choice_provider = original_trigger_provider

        history = state["cli_context"]["opponent_turn_history"][-1]
        battle_trace_event = history["events"][-1]
        self.assertEqual(battle_trace_event["stage"], "battle_trace")
        self.assertTrue(any("resolution=trigger" in line for line in battle_trace_event["details"]["battle_lines"]))


if __name__ == "__main__":
    unittest.main()
