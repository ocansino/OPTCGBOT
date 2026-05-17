import argparse
import copy
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from cli_game import action_label, build_local_planning_agent
from glat_engine import GLATEngine
from operator_gui import (
    AI_PLAYER,
    HUMAN_PLAYER,
    append_console_entry,
    collect_ai_debug_lines,
    collect_battle_trace_lines,
    collect_latest_diff_lines,
    collect_replay_diff_lines,
    collect_replay_log_lines,
    ensure_human_turn_ready,
    finish_opponent_intake_session,
    format_console_lines,
    process_console_command,
    replay_entry_label,
    replay_snapshot_to_display_state,
    save_state,
    summarize_action_log_entry,
    summarize_action_log_entry_with_effects,
    apply_operator_action,
)
from referee import get_legal_actions


DEFAULT_STATE_PATH = "web_game_state.json"


def _card_view(card: Optional[Dict[str, Any]], hidden: bool = False) -> Dict[str, Any]:
    card = card or {}
    if hidden:
        return {
            "hidden": True,
            "instance_id": card.get("instance_id"),
            "card_id": "Hidden",
            "name": "Hidden card",
            "state": card.get("state", "hidden"),
        }
    return {
        "hidden": False,
        "instance_id": card.get("instance_id"),
        "card_id": card.get("card_id"),
        "name": card.get("name"),
        "category": card.get("category"),
        "cost": card.get("cost"),
        "power": card.get("power"),
        "counter": card.get("counter"),
        "state": card.get("state"),
        "played_turn": card.get("played_turn"),
        "battle_power_bonus": card.get("battle_power_bonus", 0),
        "manual_power_bonus": card.get("manual_power_bonus", 0),
        "rush": bool(card.get("rush", False)),
        "temporary_cost_bonus": card.get("temporary_cost_bonus", 0),
    }


def _pending_prompt_view(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    web_prompt = state.get("pending_web_prompt")
    if web_prompt:
        return copy.deepcopy(web_prompt)

    pending = state.get("pending_console_prompt")
    if not pending:
        return None

    if pending.get("type") == "unsupported_effect":
        card_id = pending.get("card_id", "the card")
        return {
            "type": "unsupported_effect",
            "title": "Unsupported effect",
            "message": (
                f"{card_id} has rules text that was not auto-resolved. "
                "Resolve it manually, skip it, record a note, or mark it for later implementation."
            ),
            "card_id": pending.get("card_id"),
            "instance_id": pending.get("instance_id"),
            "choices": [
                {"id": "skip_effect", "label": "Skip", "command": "skip effect"},
                {"id": "manual_done", "label": "Manual Done", "command": "manual done"},
                {"id": "implement_later", "label": "Implement Later", "command": "implement later"},
            ],
            "allows_note": True,
        }

    return {
        "type": pending.get("type", "prompt"),
        "title": "Pending prompt",
        "message": str(pending),
        "choices": [],
        "allows_note": False,
    }


def _player_view(state: Dict[str, Any], player_id: str) -> Dict[str, Any]:
    player = state["players"][player_id]
    hide_hand = player_id == AI_PLAYER
    attached_don = player.get("attached_don", {})
    visible_life_cards = []
    leader = player.get("leader", {})

    def current_power(card: Dict[str, Any]) -> int:
        attached = attached_don.get(card.get("instance_id"), 0) if state.get("active_player") == player_id else 0
        return (
            (card.get("power") or 0)
            + (attached * 1000)
            + (card.get("battle_power_bonus") or 0)
            + (card.get("manual_power_bonus") or 0)
        )

    return {
        "id": player_id,
        "label": "AI" if player_id == AI_PLAYER else "Human",
        "life": player.get("life", len(player.get("life_cards", []))),
        "leader": {
            **_card_view(leader),
            "current_power": current_power(leader),
        },
        "board": [
            {
                **_card_view(card),
                "current_power": current_power(card),
                "attached_don": attached_don.get(card.get("instance_id"), 0),
                "has_blocker": bool(state.get("_display_blockers", {}).get(card.get("instance_id"), False)),
            }
            for card in player.get("board", [])
        ],
        "hand": [] if hide_hand else [_card_view(card) for card in player.get("hand", [])],
        "hand_count": len(player.get("hand", [])),
        "life_cards": visible_life_cards,
        "life_count": len(player.get("life_cards", [])),
        "deck_count": len(player.get("deck", [])),
        "trash": [_card_view(card) for card in player.get("trash", [])],
        "trash_count": len(player.get("trash", [])),
        "don": {
            "deck_count": len(player.get("don_deck", [])),
            "area_count": len(player.get("don_area", [])),
            "spent_count": len(player.get("spent_don", [])),
            "attached_total": sum(attached_don.values()),
            "attached": dict(attached_don),
        },
    }


def serialize_state(state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "match": {
            "mode": state.get("match_mode"),
            "turn": state.get("turn"),
            "active_player": state.get("active_player"),
            "phase": state.get("phase"),
            "winner": state.get("winner"),
        },
        "players": {
            AI_PLAYER: _player_view(state, AI_PLAYER),
            HUMAN_PLAYER: _player_view(state, HUMAN_PLAYER),
        },
        "console": {
            "entries": list(state.get("command_console", [])),
            "lines": format_console_lines(state),
        },
        "prompt": _pending_prompt_view(state),
        "debug": {
            "latest_diff": collect_latest_diff_lines(state),
            "battle_trace": collect_battle_trace_lines(state),
            "ai_debug": collect_ai_debug_lines(state),
            "replay": collect_replay_log_lines(state, count=20),
        },
    }


def serialize_replay(state: Dict[str, Any], position: Optional[int] = None) -> Dict[str, Any]:
    entries = state.get("replay_log", [])
    selected_position = None
    selected = None
    if entries:
        selected_position = len(entries) - 1 if position is None else max(0, min(position, len(entries) - 1))
        entry = entries[selected_position]
        selected = {
            "position": selected_position,
            "label": replay_entry_label(entry),
            "diff_lines": collect_replay_diff_lines(state, selected_position),
            "before": replay_snapshot_to_display_state(entry.get("before", {})),
            "after": replay_snapshot_to_display_state(entry.get("after", {})),
        }

    return {
        "entries": [
            {
                "position": index,
                "index": entry.get("index"),
                "label": replay_entry_label(entry),
                "turn": entry.get("turn"),
                "player": entry.get("player"),
                "action_type": entry.get("action", {}).get("type"),
                "diff_lines": entry.get("diff_lines", []),
            }
            for index, entry in enumerate(entries)
        ],
        "selected": selected,
        "selected_position": selected_position,
    }


class WebMatchSession:
    def __init__(
        self,
        state_path: str = DEFAULT_STATE_PATH,
        seed: Optional[int] = None,
        match_mode: str = "physical_reported",
        use_fake_ai: bool = False,
        ai_mode: str = "gemini",
        cards_path: str = "cards.json",
        player_cards_path: Optional[str] = None,
        auto_load: bool = True,
    ) -> None:
        self.state_path = Path(state_path)
        agent = build_local_planning_agent(ai_mode, use_fake_ai=use_fake_ai)
        self._active_defense_choice: Optional[Dict[str, Any]] = None
        self.engine = GLATEngine(
            cards_path=cards_path,
            player_cards_path=player_cards_path,
            agent=agent,
            effect_choice_provider=self._web_effect_choice,
            defense_choice_provider=self._web_defense_choice,
        )
        if auto_load and self.state_path.exists():
            self.state = self.engine.load_state(str(self.state_path))
        else:
            self.state = self.engine.create_initial_state(seed=seed, match_mode=match_mode)
            append_console_entry(
                self.state,
                "System",
                f"Started {self.state['match_mode']} match.",
                kind="system",
            )

    def _web_effect_choice(
        self,
        state: Dict[str, Any],
        player_id: str,
        prompt: str,
        options: list[Dict[str, Any]],
        optional: bool,
    ) -> Optional[str]:
        if player_id == HUMAN_PLAYER:
            return None
        return "__default__"

    def _web_defense_choice(
        self,
        state: Dict[str, Any],
        defender_id: str,
        attacker: Dict[str, Any],
        target: str,
        blocker_options: list[Dict[str, Any]],
        counter_options: list[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if defender_id != HUMAN_PLAYER:
            return {"mode": "default"}
        if self._active_defense_choice is not None:
            return copy.deepcopy(self._active_defense_choice)
        return {"blocker_id": None, "counter_ids": []}

    def state_view(self) -> Dict[str, Any]:
        return serialize_state(self.state)

    def replay_view(self, position: Optional[int] = None) -> Dict[str, Any]:
        return serialize_replay(self.state, position)

    def save(self) -> None:
        save_state(self.engine, self.state, str(self.state_path))

    def _control_response(self, changed: bool, message: str, command: str) -> Dict[str, Any]:
        append_console_entry(
            self.state,
            "System",
            message,
            kind="success" if changed else "warning",
            command=command,
        )
        if changed:
            self.save()
        return self._response(changed, message)

    def _response(self, ok: bool, message: str) -> Dict[str, Any]:
        return {
            "ok": ok,
            "message": message,
            "state": self.state_view(),
            "prompt": _pending_prompt_view(self.state),
        }

    def submit_command(self, command: str) -> Dict[str, Any]:
        normalized = command.strip().lower()
        if normalized in {"run ai", "run ai turn", "ai", "ai turn"}:
            return self.run_ai_turn(command=command)
        if normalized in {"prepare human", "prepare human turn", "prepare turn"}:
            return self.prepare_human_turn(command=command)
        if normalized in {"end human", "end human turn", "end turn"}:
            return self.end_human_turn(command=command)
        if normalized in {"save", "save state"}:
            append_console_entry(self.state, "You", command.strip(), kind="command", command=command.strip())
            self.save()
            return self._control_response(True, "Saved current state.", command.strip())
        if normalized in {"refresh", "refresh view"}:
            append_console_entry(self.state, "You", command.strip(), kind="command", command=command.strip())
            return self._control_response(False, "Refreshed state.", command.strip())

        changed, message = process_console_command(self.engine, self.state, command)
        if changed:
            self.save()
        return self._response(changed, message)

    def submit_choice(self, choice: str) -> Dict[str, Any]:
        pending_web_prompt = self.state.get("pending_web_prompt") or {}
        if pending_web_prompt.get("type") == "defense_choice":
            return self.submit_defense_choice(choice)
        return self.submit_command(choice)

    def prepare_human_turn(self, command: str = "prepare human turn") -> Dict[str, Any]:
        append_console_entry(self.state, "You", command.strip(), kind="command", command=command.strip())
        changed, message = ensure_human_turn_ready(self.engine, self.state)
        return self._control_response(changed, message, command.strip())

    def end_human_turn(self, command: str = "end human turn") -> Dict[str, Any]:
        append_console_entry(self.state, "You", command.strip(), kind="command", command=command.strip())
        if self.state.get("active_player") != HUMAN_PLAYER:
            return self._control_response(False, "It is not the human turn.", command.strip())
        end_action = {"type": "end_turn", "payload": {}}
        if not apply_operator_action(self.engine, self.state, end_action):
            return self._control_response(False, "Could not end the human turn.", command.strip())
        self.engine.end_phase(self.state)
        finish_opponent_intake_session(self.state, "completed")
        return self._control_response(True, "Ended human turn.", command.strip())

    def _attack_defense_prompt(self, action: Dict[str, Any], ai_debug_entry: Dict[str, Any], planned_index: int) -> Optional[Dict[str, Any]]:
        if action.get("type") != "attack":
            return None
        payload = action.get("payload", {})
        attacker = self.engine._find_card_by_instance(self.state["players"][AI_PLAYER], payload.get("attacker_id"))
        if attacker is None:
            return None
        defender = self.state["players"][HUMAN_PLAYER]
        blocker_options = [
            card
            for card in defender.get("board", [])
            if card.get("state") == "active" and self.engine._has_blocker(defender, card)
        ]
        counter_options = self.engine._available_counter_cards(defender)
        if not blocker_options and not counter_options:
            return None

        attacker_power = self.engine._current_power(self.state, self.state["players"][AI_PLAYER], attacker)
        target_ref = payload.get("target", "leader")
        target_card = defender["leader"] if target_ref == "leader" else self.engine._find_card_by_instance(defender, target_ref)
        target_power = self.engine._current_power(self.state, defender, target_card) if target_card else None
        choices = [
            {
                "id": "no_defense",
                "label": "No Defense",
                "choice": "no_defense",
            }
        ]
        for card in blocker_options:
            choices.append(
                {
                    "id": f"blocker:{card['instance_id']}",
                    "label": f"Block with {card['card_id']}",
                    "choice": f"blocker:{card['instance_id']}",
                }
            )
        for card in counter_options:
            bonus = self.engine._counter_bonus_preview(defender, card)
            choices.append(
                {
                    "id": f"counter:{card['instance_id']}",
                    "label": f"Counter {card['card_id']} (+{bonus})",
                    "choice": f"counter:{card['instance_id']}",
                }
            )

        prompt = {
            "type": "defense_choice",
            "title": "Choose defense",
            "message": (
                f"AI attacks {target_ref} with {attacker.get('card_id')} "
                f"({attacker_power} power)."
            ),
            "attacker": _card_view(attacker),
            "target": target_ref,
            "attacker_power": attacker_power,
            "target_power": target_power,
            "action": copy.deepcopy(action),
            "planned_index": planned_index,
            "ai_debug_index": len(self.state.get("ai_debug_history", [])) - 1,
            "choices": choices,
            "allows_note": False,
        }
        self.state["pending_web_prompt"] = prompt
        ai_debug_entry["executed_actions"].append(
            {
                "status": "pending_defense",
                "planned_index": planned_index,
                "action": copy.deepcopy(action),
            }
        )
        append_console_entry(
            self.state,
            "System",
            prompt["message"],
            kind="warning",
        )
        return prompt

    def _run_ai_main_phase_until_prompt(self) -> bool:
        if hasattr(self.engine.agent, "get_next_action"):
            return self._run_ai_main_phase_replanning_until_prompt()
        return self._run_ai_main_phase_planned_until_prompt()

    def _scored_actions_view(self) -> list[Dict[str, Any]]:
        return [
            {
                "index": item.index,
                "action": copy.deepcopy(item.action),
                "score": item.score,
                "reasons": list(item.reasons),
                "risk_flags": list(item.risk_flags),
                "summary": item.summary,
                "lookahead_score": item.lookahead_score,
                "rollout_score": item.rollout_score,
            }
            for item in getattr(self.engine.agent, "last_scored_actions", [])
        ]

    def _run_ai_main_phase_replanning_until_prompt(self) -> bool:
        self.state["phase"] = "main"
        ai_debug_entry = {
            "turn": self.state["turn"],
            "player": self.state["active_player"],
            "phase": self.state["phase"],
            "planning_mode": "replan_each_action",
            "legal_actions": [],
            "scored_actions": [],
            "decision_steps": [],
            "planned_indices": [],
            "planned_actions": [],
            "executed_actions": [],
            "fallback_end_turn": False,
        }
        self.state.setdefault("ai_debug_history", []).append(ai_debug_entry)

        max_actions = int(getattr(self.engine.agent, "max_plan_actions", 6) or 6)
        for step in range(max_actions):
            if self.state["winner"]:
                break
            legal_actions = get_legal_actions(self.state, self.engine)
            if not legal_actions:
                break
            if step == 0:
                ai_debug_entry["legal_actions"] = [copy.deepcopy(action) for action in legal_actions]

            raw_index = self.engine.agent.get_next_action(self.state, legal_actions)
            scored_actions = self._scored_actions_view()
            if step == 0:
                ai_debug_entry["scored_actions"] = scored_actions
            ai_debug_entry["decision_steps"].append(
                {
                    "step": step + 1,
                    "legal_actions": [copy.deepcopy(action) for action in legal_actions],
                    "scored_actions": scored_actions,
                    "chosen_index": raw_index,
                }
            )
            ai_debug_entry["planned_indices"].append(raw_index)

            if not isinstance(raw_index, int) or not (0 <= raw_index < len(legal_actions)):
                ai_debug_entry["executed_actions"].append(
                    {"status": "skipped_invalid_index", "planned_index": raw_index}
                )
                break

            action = copy.deepcopy(legal_actions[raw_index])
            ai_debug_entry["planned_actions"].append(copy.deepcopy(action))
            if not self.engine.is_valid_action(self.state, action):
                ai_debug_entry["executed_actions"].append(
                    {
                        "status": "skipped_invalid",
                        "planned_index": raw_index,
                        "action": copy.deepcopy(action),
                    }
                )
                break

            if self._attack_defense_prompt(action, ai_debug_entry, raw_index) is not None:
                return True

            self.engine.apply_action(self.state, action)
            ai_debug_entry["executed_actions"].append(
                {
                    "status": "applied",
                    "planned_index": raw_index,
                    "action": copy.deepcopy(action),
                }
            )
            if action["type"] == "end_turn":
                return False

        fallback_end_turn = {"type": "end_turn", "payload": {}}
        if self.engine.is_valid_action(self.state, fallback_end_turn):
            ai_debug_entry["fallback_end_turn"] = True
            self.engine.apply_action(self.state, fallback_end_turn)
        return False

    def _run_ai_main_phase_planned_until_prompt(self) -> bool:
        self.state["phase"] = "main"
        legal_actions = get_legal_actions(self.state, self.engine)
        if not legal_actions:
            return False

        planned_indices = self.engine.agent.get_turn_plan(self.state, legal_actions)
        if not isinstance(planned_indices, list) or not planned_indices:
            planned_indices = [len(legal_actions) - 1]
        scored_actions = self._scored_actions_view()
        ai_debug_entry = {
            "turn": self.state["turn"],
            "player": self.state["active_player"],
            "phase": self.state["phase"],
            "planning_mode": "static_turn_plan",
            "legal_actions": [copy.deepcopy(action) for action in legal_actions],
            "scored_actions": scored_actions,
            "planned_indices": list(planned_indices),
            "planned_actions": [
                copy.deepcopy(legal_actions[index])
                for index in planned_indices
                if isinstance(index, int) and 0 <= index < len(legal_actions)
            ],
            "executed_actions": [],
            "fallback_end_turn": False,
        }
        self.state.setdefault("ai_debug_history", []).append(ai_debug_entry)

        for raw_index in planned_indices[:6]:
            if self.state["winner"]:
                break
            if not isinstance(raw_index, int) or not (0 <= raw_index < len(legal_actions)):
                break

            action = copy.deepcopy(legal_actions[raw_index])
            if not self.engine.is_valid_action(self.state, action):
                ai_debug_entry["executed_actions"].append(
                    {
                        "status": "skipped_invalid",
                        "planned_index": raw_index,
                        "action": copy.deepcopy(action),
                    }
                )
                break

            if self._attack_defense_prompt(action, ai_debug_entry, raw_index) is not None:
                return True

            self.engine.apply_action(self.state, action)
            ai_debug_entry["executed_actions"].append(
                {
                    "status": "applied",
                    "planned_index": raw_index,
                    "action": copy.deepcopy(action),
                }
            )
            if action["type"] == "end_turn":
                return False

        fallback_end_turn = {"type": "end_turn", "payload": {}}
        if self.engine.is_valid_action(self.state, fallback_end_turn):
            ai_debug_entry["fallback_end_turn"] = True
            self.engine.apply_action(self.state, fallback_end_turn)
        return False

    def submit_defense_choice(self, choice: str) -> Dict[str, Any]:
        prompt = self.state.get("pending_web_prompt")
        if not prompt or prompt.get("type") != "defense_choice":
            return self._response(False, "No defense prompt is pending.")

        blocker_id = None
        counter_ids: list[str] = []
        if choice.startswith("blocker:"):
            blocker_id = choice.split(":", 1)[1]
        elif choice.startswith("counter:"):
            counter_ids = [choice.split(":", 1)[1]]
        elif choice not in {"no_defense", "none", "no defense"}:
            return self._response(False, f"Unknown defense choice: {choice}")

        action = copy.deepcopy(prompt["action"])
        self._active_defense_choice = {"blocker_id": blocker_id, "counter_ids": counter_ids}
        try:
            before_log_count = len(self.state.get("logs", []))
            self.engine.apply_action(self.state, action)
        finally:
            self._active_defense_choice = None

        debug_index = prompt.get("ai_debug_index")
        if isinstance(debug_index, int) and 0 <= debug_index < len(self.state.get("ai_debug_history", [])):
            self.state["ai_debug_history"][debug_index]["executed_actions"].append(
                {
                    "status": "applied_after_defense",
                    "planned_index": prompt.get("planned_index"),
                    "action": copy.deepcopy(action),
                    "defense_choice": {"blocker_id": blocker_id, "counter_ids": list(counter_ids)},
                }
            )
        ai_logs = [
            log
            for log in self.state.get("logs", [])[before_log_count:]
            if log.get("player") == AI_PLAYER
        ]
        for log in ai_logs:
            append_console_entry(self.state, "AI", summarize_action_log_entry_with_effects(self.state, log), kind="ai")

        self.state["pending_web_prompt"] = None
        append_console_entry(
            self.state,
            "System",
            "Defense resolved.",
            kind="success",
        )
        self.save()
        return self._response(True, "Defense resolved.")

    def run_ai_turn(self, command: str = "run ai turn") -> Dict[str, Any]:
        append_console_entry(self.state, "You", command.strip(), kind="command", command=command.strip())
        if self.state.get("active_player") != AI_PLAYER:
            return self._control_response(False, "It is not the AI turn.", command.strip())
        if self.state.get("pending_web_prompt"):
            return self._control_response(False, "Resolve the pending prompt before continuing the AI turn.", command.strip())

        before_log_count = len(self.state.get("logs", []))
        if self.state.get("phase") != "main":
            self.engine.refresh_phase(self.state)
            self.engine.draw_phase(self.state)
            self.engine.don_phase(self.state)
        paused_for_defense = self._run_ai_main_phase_until_prompt()
        ai_logs = [
            log
            for log in self.state.get("logs", [])[before_log_count:]
            if log.get("player") == AI_PLAYER
        ]
        if paused_for_defense:
            self.save()
            return self._response(True, "AI attack pending defense choice.")
        self.engine.end_phase(self.state)
        if not ai_logs:
            append_console_entry(self.state, "AI", "Turn completed with no logged actions.", kind="ai")
        for log in ai_logs:
            append_console_entry(self.state, "AI", summarize_action_log_entry_with_effects(self.state, log), kind="ai")
        return self._control_response(True, "Ran AI turn.", command.strip())

    def new_game(self, seed: Optional[int] = None, match_mode: str = "physical_reported") -> Dict[str, Any]:
        self.state = self.engine.create_initial_state(seed=seed, match_mode=match_mode)
        append_console_entry(
            self.state,
            "System",
            f"Started {self.state['match_mode']} match.",
            kind="system",
        )
        self.save()
        return self._response(True, "Started new game.")


class CockpitRequestHandler(BaseHTTPRequestHandler):
    session: WebMatchSession
    static_root = Path(__file__).parent / "web" / "static"

    def _send_json(self, payload: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def _send_static(self, relative_path: str) -> None:
        path = (self.static_root / relative_path).resolve()
        if not str(path).startswith(str(self.static_root.resolve())) or not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        content_type = "text/html; charset=utf-8"
        if path.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif path.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            self._send_json({"ok": True, "state": self.session.state_view()})
            return
        if parsed.path == "/api/replay":
            query = parse_qs(parsed.query)
            position = None
            if "position" in query:
                try:
                    position = int(query["position"][0])
                except (TypeError, ValueError):
                    position = None
            self._send_json({"ok": True, "replay": self.session.replay_view(position)})
            return
        if parsed.path in {"/", "/index.html"}:
            self._send_static("index.html")
            return
        if parsed.path.startswith("/static/"):
            self._send_static(parsed.path.removeprefix("/static/"))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        try:
            payload = self._read_json()
            parsed = urlparse(self.path)
            if parsed.path == "/api/command":
                self._send_json(self.session.submit_command(str(payload.get("command", ""))))
                return
            if parsed.path == "/api/choice":
                choice = str(payload.get("choice") or payload.get("command") or "")
                self._send_json(self.session.submit_choice(choice))
                return
            if parsed.path == "/api/ai-turn":
                self._send_json(self.session.run_ai_turn())
                return
            if parsed.path == "/api/prepare-human-turn":
                self._send_json(self.session.prepare_human_turn())
                return
            if parsed.path == "/api/end-human-turn":
                self._send_json(self.session.end_human_turn())
                return
            if parsed.path == "/api/new-game":
                seed = int(payload["seed"]) if "seed" in payload and payload["seed"] is not None else None
                match_mode = str(payload.get("match_mode", "physical_reported"))
                self._send_json(self.session.new_game(seed=seed, match_mode=match_mode))
                return
            if parsed.path == "/api/save":
                self.session.save()
                self._send_json(self.session._response(True, "Saved current state."))
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        except Exception as exc:
            self._send_json(
                {"ok": False, "message": f"Request failed: {exc}", "state": self.session.state_view()},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def log_message(self, format: str, *args: Any) -> None:
        return


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    state_path: str = DEFAULT_STATE_PATH,
    seed: Optional[int] = None,
    match_mode: str = "physical_reported",
    use_fake_ai: bool = False,
    ai_mode: str = "gemini",
    cards_path: str = "cards.json",
    player_cards_path: Optional[str] = None,
) -> ThreadingHTTPServer:
    session = WebMatchSession(
        state_path=state_path,
        seed=seed,
        match_mode=match_mode,
        use_fake_ai=use_fake_ai,
        ai_mode=ai_mode,
        cards_path=cards_path,
        player_cards_path=player_cards_path,
    )

    class Handler(CockpitRequestHandler):
        pass

    Handler.session = session
    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local GLAT web cockpit.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--state", default=DEFAULT_STATE_PATH)
    parser.add_argument("--cards", default="cards.json", help="AI/P1 deck JSON path.")
    parser.add_argument("--player-cards", default=None, help="Player/P2 deck JSON path.")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--match-mode", choices=["digital_strict", "physical_reported"], default="physical_reported")
    parser.add_argument("--fake-ai", action="store_true", help="Use deterministic local AI instead of Gemini.")
    parser.add_argument(
        "--ai",
        choices=["gemini", "fake", "heuristic"],
        default="gemini",
        help="AI policy to use for P1 turns.",
    )
    args = parser.parse_args()

    server = create_server(
        host=args.host,
        port=args.port,
        state_path=args.state,
        seed=args.seed,
        match_mode=args.match_mode,
        use_fake_ai=args.fake_ai,
        ai_mode=args.ai,
        cards_path=args.cards,
        player_cards_path=args.player_cards,
    )
    print(f"GLAT web cockpit listening at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping GLAT web cockpit.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
