import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from web_app import WebMatchSession, create_server


class WebCockpitSessionTests(unittest.TestCase):
    def make_session(self, match_mode: str = "physical_reported") -> WebMatchSession:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        return WebMatchSession(
            state_path=str(Path(temp_dir.name) / "state.json"),
            match_mode=match_mode,
            use_fake_ai=True,
            auto_load=False,
        )

    def test_state_view_hides_ai_hand_cards(self) -> None:
        session = self.make_session()

        state_view = session.state_view()
        ai_player = state_view["players"]["P1"]

        self.assertEqual(ai_player["hand_count"], 5)
        self.assertEqual(ai_player["hand"], [])
        self.assertEqual(ai_player["deck_count"], len(session.state["players"]["P1"]["deck"]))

    def test_physical_reported_command_creates_card_and_prompt(self) -> None:
        session = self.make_session(match_mode="physical_reported")
        session.state["turn"] = 3
        session.state["active_player"] = "P2"
        session.state["phase"] = "main"
        session.state["players"]["P2"]["hand"] = []

        response = session.submit_command("play OP12-021")

        self.assertTrue(response["ok"])
        self.assertIn("Recorded physical play", response["message"])
        self.assertEqual(session.state["players"]["P2"]["board"][-1]["card_id"], "OP12-021")
        self.assertEqual(response["prompt"]["type"], "unsupported_effect")
        self.assertTrue(response["prompt"]["choices"])
        self.assertEqual(response["state"]["players"]["P2"]["board"][-1]["card_id"], "OP12-021")

    def test_choice_command_clears_pending_prompt(self) -> None:
        session = self.make_session(match_mode="physical_reported")
        session.state["turn"] = 3
        session.state["active_player"] = "P2"
        session.state["phase"] = "main"

        response = session.submit_command("play OP12-021")
        self.assertTrue(response["ok"])

        response = session.submit_choice("skip effect")

        self.assertTrue(response["ok"])
        self.assertIsNone(response["prompt"])
        self.assertIsNone(session.state.get("pending_console_prompt"))
        self.assertEqual(session.state["unsupported_effect_resolutions"][-1]["resolution"], "skipped")

    def test_digital_strict_rejects_untracked_physical_play(self) -> None:
        session = self.make_session(match_mode="digital_strict")
        session.state["turn"] = 3
        session.state["active_player"] = "P2"
        session.state["phase"] = "main"
        session.state["players"]["P2"]["hand"] = []

        response = session.submit_command("play OP12-021")

        self.assertFalse(response["ok"])
        self.assertIn("physical_reported", response["message"])
        self.assertFalse(
            any(card["card_id"] == "OP12-021" for card in session.state["players"]["P2"]["board"])
        )

    def test_replay_view_returns_selected_diff_after_command(self) -> None:
        session = self.make_session(match_mode="physical_reported")
        session.state["turn"] = 3
        session.state["active_player"] = "P2"
        session.state["phase"] = "main"

        response = session.submit_command("play OP12-021")
        self.assertTrue(response["ok"])

        replay = session.replay_view()

        self.assertGreaterEqual(len(replay["entries"]), 1)
        self.assertIsNotNone(replay["selected"])
        self.assertIn("physical_reported_play", replay["selected"]["label"])

    def test_ai_attack_pauses_for_human_defense_choice(self) -> None:
        session = self.make_session(match_mode="physical_reported")
        session.state["turn"] = 3
        session.state["active_player"] = "P1"
        session.state["phase"] = "refresh"
        counter = session.engine.build_card_instance("P2", "OP12-098")
        session.state["players"]["P2"]["hand"] = [counter]

        response = session.run_ai_turn()

        self.assertTrue(response["ok"])
        self.assertEqual(response["prompt"]["type"], "defense_choice")
        self.assertTrue(any(choice["choice"] == f"counter:{counter['instance_id']}" for choice in response["prompt"]["choices"]))
        self.assertTrue(any(entry["status"] == "pending_defense" for entry in session.state["ai_debug_history"][-1]["executed_actions"]))
        self.assertEqual(len(session.state["players"]["P2"]["trash"]), 0)

    def test_human_can_decline_web_defense_without_auto_counter(self) -> None:
        session = self.make_session(match_mode="physical_reported")
        session.state["turn"] = 3
        session.state["active_player"] = "P1"
        session.state["phase"] = "refresh"
        counter = session.engine.build_card_instance("P2", "OP12-098")
        session.state["players"]["P2"]["hand"] = [counter]
        response = session.run_ai_turn()
        self.assertEqual(response["prompt"]["type"], "defense_choice")

        response = session.submit_choice("no_defense")

        self.assertTrue(response["ok"])
        self.assertIsNone(response["prompt"])
        self.assertEqual(session.state["players"]["P2"]["hand"][0]["instance_id"], counter["instance_id"])
        self.assertFalse(any(card["instance_id"] == counter["instance_id"] for card in session.state["players"]["P2"]["trash"]))


class WebCockpitHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.server = create_server(
            port=0,
            state_path=str(Path(temp_dir.name) / "state.json"),
            match_mode="physical_reported",
            use_fake_ai=True,
        )
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

        import threading

        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.shutdown)

    def request_json(self, path: str, body: dict | None = None) -> dict:
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(f"{self.base_url}{path}", data=data, headers=headers)
        try:
            with urlopen(request, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            self.fail(f"HTTP request failed: {exc.code} {exc.read().decode('utf-8')}")

    def request_text(self, path: str) -> str:
        with urlopen(Request(f"{self.base_url}{path}"), timeout=5) as response:
            return response.read().decode("utf-8")

    def test_root_serves_cockpit_page(self) -> None:
        html = self.request_text("/")

        self.assertIn("GLAT Cockpit", html)
        self.assertIn("/static/styles.css", html)
        self.assertIn("/static/app.js", html)

    def test_static_assets_are_served(self) -> None:
        script = self.request_text("/static/app.js")

        self.assertIn("/api/state", script)
        self.assertIn("/api/command", script)

    def test_state_endpoint_returns_match_state(self) -> None:
        response = self.request_json("/api/state")

        self.assertTrue(response["ok"])
        self.assertEqual(response["state"]["match"]["mode"], "physical_reported")
        self.assertEqual(response["state"]["players"]["P1"]["hand"], [])

    def test_command_endpoint_returns_updated_state_and_prompt(self) -> None:
        session = self.server.RequestHandlerClass.session
        session.state["turn"] = 3
        session.state["active_player"] = "P2"
        session.state["phase"] = "main"

        response = self.request_json("/api/command", {"command": "play OP12-021"})

        self.assertTrue(response["ok"])
        self.assertEqual(response["prompt"]["type"], "unsupported_effect")
        self.assertEqual(response["state"]["players"]["P2"]["board"][-1]["card_id"], "OP12-021")

    def test_choice_endpoint_routes_prompt_choice(self) -> None:
        session = self.server.RequestHandlerClass.session
        session.state["turn"] = 3
        session.state["active_player"] = "P2"
        session.state["phase"] = "main"
        response = self.request_json("/api/command", {"command": "play OP12-021"})
        self.assertTrue(response["ok"])

        response = self.request_json("/api/choice", {"choice": "manual done"})

        self.assertTrue(response["ok"])
        self.assertIsNone(response["prompt"])

    def test_replay_endpoint_returns_entries_after_command(self) -> None:
        session = self.server.RequestHandlerClass.session
        session.state["turn"] = 3
        session.state["active_player"] = "P2"
        session.state["phase"] = "main"
        response = self.request_json("/api/command", {"command": "play OP12-021"})
        self.assertTrue(response["ok"])

        response = self.request_json("/api/replay")

        self.assertTrue(response["ok"])
        self.assertGreaterEqual(len(response["replay"]["entries"]), 1)
        self.assertIsNotNone(response["replay"]["selected"])


if __name__ == "__main__":
    unittest.main()
