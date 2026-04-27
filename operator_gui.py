import argparse
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional, Tuple

from cli_game import (
    FakePlanningAgent,
    action_label,
    apply_human_action,
    begin_opponent_intake_session,
    cli_defense_choice,
    cli_effect_choice,
    cli_trigger_choice,
    format_battle_context_lines,
    finish_opponent_intake_session,
    get_last_battle_context,
    handle_shorthand_report,
    load_state,
    log_opponent_intake_event,
    parse_command_to_action,
    run_manual_command,
    save_state,
    start_manual_turn,
)
from glat_engine import GLATEngine


AI_PLAYER = "P1"
HUMAN_PLAYER = "P2"
SUPPORTED_AUTOMATIC_EFFECT_IDS = {
    "EB03-042",
    "EB03-053",
    "OP06-115",
    "OP10-109",
    "OP12-086",
    "OP12-087",
    "OP12-089",
    "OP12-094",
    "OP12-097",
    "OP12-098",
    "OP12-112",
    "OP12-119",
    "OP14-108",
}


def format_summary_lines(state: Dict[str, Any]) -> List[str]:
    lines = [
        f"Turn {state['turn']} | Active: {state['active_player']} | Phase: {state['phase']} | Winner: {state['winner'] or '-'}"
    ]
    for player_id in (AI_PLAYER, HUMAN_PLAYER):
        player = state["players"][player_id]
        label = "AI" if player_id == AI_PLAYER else "Human"
        attached = sum(player["attached_don"].values())
        lines.append(
            f"{player_id} ({label}) | Life {player['life']} | Hand {len(player['hand'])} | "
            f"Board {len(player['board'])} | Deck {len(player['deck'])} | Trash {len(player['trash'])} | "
            f"DON area {len(player['don_area'])} | Attached DON {attached}"
        )
    return lines


def collect_recent_log_lines(state: Dict[str, Any], count: int = 12) -> List[str]:
    logs = state.get("logs", [])[-count:]
    return [
        f"Turn {log['turn']} {log['player']} {log['action']['type']}: {log['result']}"
        for log in logs
    ]


def append_console_entry(
    state: Dict[str, Any],
    speaker: str,
    message: str,
    kind: str = "system",
    command: Optional[str] = None,
) -> Dict[str, Any]:
    entry = {
        "index": len(state.setdefault("command_console", [])) + 1,
        "turn": state.get("turn"),
        "phase": state.get("phase"),
        "speaker": speaker,
        "kind": kind,
        "message": message,
    }
    if command is not None:
        entry["command"] = command
    state["command_console"].append(entry)
    return entry


def format_console_lines(state: Dict[str, Any], count: int = 24) -> List[str]:
    entries = state.get("command_console", [])[-count:]
    if not entries:
        return ["No console messages yet."]
    lines = []
    for entry in entries:
        speaker = entry.get("speaker", "System")
        message = entry.get("message", "")
        turn = entry.get("turn", "-")
        lines.append(f"#{entry.get('index')} | T{turn} | {speaker}: {message}")
    return lines


def _extract_physical_play_card_id(command: str) -> Optional[str]:
    match = re.match(
        r"^(?:i\s+)?(?:play|played)\s+([A-Za-z]{2,4}\d{2}-\d{3})\s*$",
        command.strip(),
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return match.group(1).upper()


def _card_has_unresolved_text(card_data: Dict[str, Any]) -> bool:
    return bool(card_data.get("effect") or card_data.get("trigger"))


def _unsupported_effect_message(card_id: str) -> str:
    return (
        f"{card_id} has rules text that was not auto-resolved. "
        "Use manual commands to resolve it, type 'skip effect', or type 'note <text>'."
    )


def _handle_pending_console_prompt(
    state: Dict[str, Any],
    command: str,
) -> Optional[Tuple[bool, str]]:
    pending = state.get("pending_console_prompt")
    if not pending:
        return None

    raw = command.strip()
    normalized = raw.lower()
    if normalized in {"skip", "skip effect"}:
        card_id = pending.get("card_id", "the card")
        state["pending_console_prompt"] = None
        return True, f"Skipped pending effect for {card_id}."

    if normalized.startswith("note "):
        note_text = raw[5:].strip()
        if not note_text:
            return False, "Enter note text after 'note'."
        state.setdefault("operator_notes", []).append(
            {
                "turn": state.get("turn"),
                "phase": state.get("phase"),
                "prompt": dict(pending),
                "note": note_text,
            }
        )
        card_id = pending.get("card_id", "the card")
        state["pending_console_prompt"] = None
        return True, f"Recorded note for {card_id}."

    return None


def apply_physical_reported_play(
    engine: GLATEngine,
    state: Dict[str, Any],
    card_id: str,
) -> Tuple[bool, str]:
    if state.get("match_mode") != "physical_reported":
        return False, "Physical reported plays are only available in physical_reported mode."

    card_data = engine.lookup_card_data(card_id)
    if card_data is None:
        return False, f"Unknown card id: {card_id}"
    if card_data.get("category") == "Leader":
        return False, "Leader cards cannot be played into the physical report zones."

    player = state["players"][HUMAN_PLAYER]
    if card_data.get("category") == "Character" and len(player["board"]) >= 5:
        return False, "Human character area is full."

    before_snapshot = engine._snapshot_state_for_replay(state)
    card = engine.build_card_instance(HUMAN_PLAYER, card_id)
    card["played_turn"] = state.get("turn")
    card["state"] = "active"

    destination = "board" if card.get("category") == "Character" else "trash"
    if destination == "board":
        player["board"].append(card)
    else:
        player["trash"].append(card)

    unsupported_effect = (
        _card_has_unresolved_text(card_data)
        and card["card_id"] not in SUPPORTED_AUTOMATIC_EFFECT_IDS
    )
    result: Dict[str, Any] = {
        "reported_play": card["card_id"],
        "destination": destination,
        "mode": "physical_reported",
        "cost_handled_by": "physical_table",
        "effect_status": "unsupported_manual_required" if unsupported_effect else "no_auto_prompt",
    }
    if unsupported_effect:
        result["manual_effect_options"] = ["skip effect", "manual commands", "note <text>"]
        state["pending_console_prompt"] = {
            "type": "unsupported_effect",
            "card_id": card["card_id"],
            "instance_id": card["instance_id"],
            "options": result["manual_effect_options"],
        }

    engine.validate_state(state)
    engine.log_action(
        state,
        HUMAN_PLAYER,
        {"type": "physical_reported_play", "payload": {"card_id": card["card_id"]}},
        result,
        before_snapshot=before_snapshot,
    )
    message = f"Recorded physical play: {card['card_id']} to {destination}."
    if unsupported_effect:
        message = f"{message} {_unsupported_effect_message(card['card_id'])}"
    return True, message


def collect_replay_log_lines(state: Dict[str, Any], count: int = 12) -> List[str]:
    entries = state.get("replay_log", [])[-count:]
    if not entries:
        return ["No replay entries yet."]
    return [
        (
            f"#{entry['index']} | Turn {entry['turn']} {entry['player']} "
            f"{entry['action']['type']} | "
            f"{', '.join(entry.get('diff_lines', [])[:2]) or 'no visible state change'}"
        )
        for entry in entries
    ]


def collect_latest_diff_lines(state: Dict[str, Any]) -> List[str]:
    entries = state.get("replay_log", [])
    if not entries:
        return ["No replay diff yet."]
    latest = entries[-1]
    header = (
        f"Step {latest['index']} | Turn {latest['turn']} | "
        f"{latest['player']} {latest['action']['type']}"
    )
    return [header, *(latest.get("diff_lines") or ["No visible state changes recorded."])]


def collect_ai_debug_lines(state: Dict[str, Any], count: int = 2) -> List[str]:
    entries = state.get("ai_debug_history", [])[-count:]
    if not entries:
        return ["No AI debug history yet."]
    lines: List[str] = []
    for entry in entries:
        lines.append(
            f"Turn {entry['turn']} {entry['player']} | legal {len(entry.get('legal_actions', []))} | "
            f"planned {entry.get('planned_indices', [])} | fallback_end_turn={entry.get('fallback_end_turn', False)}"
        )
        for executed in entry.get("executed_actions", []):
            action = executed.get("action", {})
            lines.append(f"  {executed.get('status', 'unknown')}: {action.get('type', '-')}")
    return lines


def collect_intake_log_lines(state: Dict[str, Any]) -> List[str]:
    context = state.get("cli_context", {})
    history = context.get("opponent_turn_history", [])
    if not history:
        return ["No opponent intake history yet."]
    session = history[-1]
    header = (
        f"Turn {session['turn']} | Phase {session.get('phase', '-')} | "
        f"Status {session.get('status', '-')}"
    )
    lines = [header]
    for event in session.get("events", [])[-12:]:
        lines.append(f"{event['index']}. [{event['stage']}] {event['summary']}")
    return lines


def collect_battle_trace_lines(state: Dict[str, Any]) -> List[str]:
    live_context = state.get("battle_context")
    if live_context:
        return format_battle_context_lines(live_context)
    last_context = get_last_battle_context(state)
    if last_context:
        return format_battle_context_lines(last_context)
    return ["No battle trace yet."]


def card_tile_lines(card: Dict[str, Any]) -> List[str]:
    if card.get("face_down"):
        label = card.get("name") or "Face-down card"
        return [card.get("card_id", "Hidden"), label, card.get("state", "hidden")]

    card_id = card.get("card_id", "?")
    name = card.get("name") or card_id
    if len(name) > 24:
        name = f"{name[:21]}..."
    lines = [card_id, name]
    details: List[str] = []
    if card.get("cost") is not None:
        details.append(f"cost {card.get('cost')}")
    if card.get("power") is not None:
        details.append(f"pow {card.get('power')}")
    if details:
        lines.append(" | ".join(details))
    state = card.get("state")
    attached = card.get("attached_don", 0)
    if state or attached:
        lines.append(f"{state or 'active'} | DON {attached}")
    return lines


def card_detail_lines(
    card: Optional[Dict[str, Any]],
    player_id: Optional[str] = None,
    zone: Optional[str] = None,
) -> List[str]:
    if not card:
        return ["Select a card to inspect it."]
    if card.get("face_down"):
        lines = []
        if player_id or zone:
            lines.append(f"Location: {player_id or '-'} / {zone or '-'}")
        lines.extend(
            [
                "card: Face-down life card",
                "details: Hidden until revealed by damage or an effect.",
            ]
        )
        return lines
    lines = []
    if player_id or zone:
        lines.append(f"Location: {player_id or '-'} / {zone or '-'}")
    for key in (
        "card_id",
        "name",
        "instance_id",
        "category",
        "cost",
        "base_cost",
        "power",
        "counter",
        "state",
        "attached_don",
        "played_turn",
    ):
        if key in card:
            lines.append(f"{key}: {card[key]}")
    if card.get("types"):
        lines.append(f"types: {', '.join(card['types'])}")
    return lines


def replay_snapshot_to_display_state(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    players: Dict[str, Any] = {}
    for player_id, player in snapshot.get("players", {}).items():
        hand_cards = [
            {
                "instance_id": f"{player_id}-HAND-{index + 1:02d}",
                "card_id": card_id,
                "name": card_id,
                "state": "known",
            }
            for index, card_id in enumerate(player.get("hand_cards", []))
        ]
        life_cards = build_hidden_life_cards(player_id, player.get("life_cards_count", len(player.get("life_cards", []))))
        trash_cards = [
            {
                "instance_id": f"{player_id}-TRASH-{index + 1:02d}",
                "card_id": card_id,
                "name": card_id,
                "state": "trash",
            }
            for index, card_id in enumerate(player.get("trash_cards", []))
        ]
        leader = dict(player.get("leader", {}))
        leader.setdefault("name", leader.get("card_id", "Leader"))
        players[player_id] = {
            "life": player.get("life", player.get("life_cards_count", 0)),
            "deck": [{"card_id": "DECK", "instance_id": f"{player_id}-DECK"}]
            * player.get("deck_count", 0),
            "hand": hand_cards,
            "board": [dict(card) for card in player.get("board", [])],
            "trash": trash_cards,
            "life_cards": life_cards,
            "don_deck": ["DON"] * max(0, 10 - player.get("don_area_count", 0) - player.get("spent_don_count", 0) - player.get("attached_don_total", 0)),
            "don_area": ["DON"] * player.get("don_area_count", 0),
            "spent_don": ["DON"] * player.get("spent_don_count", 0),
            "attached_don": dict(player.get("attached_don", {})),
            "leader": leader,
            "turn_flags": {},
        }
    return {
        "turn": snapshot.get("turn", "-"),
        "match_mode": snapshot.get("match_mode", "digital_strict"),
        "active_player": snapshot.get("active_player", "-"),
        "phase": snapshot.get("phase", "-"),
        "winner": snapshot.get("winner"),
        "players": players,
        "logs": [],
        "replay_log": [],
        "ai_debug_history": [],
        "battle_context": None,
    }


def replay_entry_label(entry: Dict[str, Any]) -> str:
    action = entry.get("action", {})
    return (
        f"Replay #{entry.get('index')} | Turn {entry.get('turn')} | "
        f"{entry.get('player')} {action.get('type', '-')}"
    )


def build_hidden_life_cards(player_id: str, count: int) -> List[Dict[str, Any]]:
    return [
        {
            "instance_id": f"{player_id}-LIFE-{index + 1:02d}",
            "card_id": "Life",
            "name": f"Face-down Life {index + 1}",
            "state": "face down",
            "face_down": True,
        }
        for index in range(count)
    ]


def ensure_human_turn_ready(engine: GLATEngine, state: Dict[str, Any]) -> Tuple[bool, str]:
    if state["active_player"] != HUMAN_PLAYER:
        return False, "It is not the human/opponent turn."
    if state["phase"] != "main":
        start_manual_turn(engine, state)
        begin_opponent_intake_session(state)
        log_opponent_intake_event(
            state,
            "turn_start",
            "Started operator GUI opponent turn intake after refresh/draw/DON.",
            {
                "turn": state["turn"],
                "active_player": state["active_player"],
                "phase": state["phase"],
            },
        )
        return True, "Human turn prepared for opponent intake."
    return False, "Human turn already in main phase."


def process_operator_command(
    engine: GLATEngine,
    state: Dict[str, Any],
    command: str,
) -> Tuple[bool, str]:
    raw = command.strip()
    if not raw:
        return False, "Enter a command first."

    pending_result = _handle_pending_console_prompt(state, raw)
    if pending_result is not None:
        return pending_result

    physical_play_card_id = _extract_physical_play_card_id(raw)
    if physical_play_card_id is not None and state.get("match_mode") == "physical_reported":
        return apply_physical_reported_play(engine, state, physical_play_card_id)

    if handle_shorthand_report(engine, state, raw):
        return True, f"Applied shorthand: {raw}"

    if run_manual_command(engine, state, raw):
        return True, f"Applied manual command: {raw}"

    action = parse_command_to_action(raw)
    if action and apply_human_action(engine, state, action):
        if action["type"] == "end_turn":
            engine.end_phase(state)
            finish_opponent_intake_session(state, "completed")
        return True, f"Applied action: {action_label(action)}"

    if physical_play_card_id is not None:
        return apply_physical_reported_play(engine, state, physical_play_card_id)

    return False, f"Could not apply command: {raw}"


def process_console_command(
    engine: GLATEngine,
    state: Dict[str, Any],
    command: str,
) -> Tuple[bool, str]:
    raw = command.strip()
    if not raw:
        message = "Enter a command first."
        append_console_entry(state, "System", message, kind="warning")
        return False, message

    append_console_entry(state, "You", raw, kind="command", command=raw)
    changed, message = process_operator_command(engine, state, raw)
    append_console_entry(
        state,
        "System",
        message,
        kind="success" if changed else "warning",
        command=raw,
    )
    return changed, message


class OperatorGUI(tk.Tk):
    def __init__(
        self,
        state_path: str,
        use_fake_ai: bool = False,
        seed: int = 7,
        match_mode: str = "digital_strict",
    ) -> None:
        super().__init__()
        self.title("GLAT Operator Panel")
        self.geometry("1480x920")
        self.minsize(1240, 780)

        self.state_path = Path(state_path)
        agent = FakePlanningAgent() if use_fake_ai else None
        self.engine = GLATEngine(
            agent=agent,
            effect_choice_provider=cli_effect_choice,
            defense_choice_provider=cli_defense_choice,
            trigger_choice_provider=cli_trigger_choice,
        )
        if self.state_path.exists():
            self.state = load_state(self.engine, str(self.state_path))
        else:
            self.state = self.engine.create_initial_state(seed=seed, match_mode=match_mode)
            append_console_entry(
                self.state,
                "System",
                f"Started {self.state['match_mode']} match.",
                kind="system",
            )
            save_state(self.engine, self.state, str(self.state_path))

        self.status_var = tk.StringVar(value="Ready")
        self.command_var = tk.StringVar()
        self.console_popup_command_var = tk.StringVar()
        self.console_window: Optional[tk.Toplevel] = None
        self.console_text: Optional[tk.Text] = None
        self.replay_position: Optional[int] = None
        self.replay_side = "after"
        self.selected_card: Optional[Dict[str, Any]] = None
        self.selected_location = ("-", "-")
        self.zone_frames: Dict[str, ttk.Frame] = {}
        self.zone_canvases: Dict[str, tk.Canvas] = {}

        self._build_layout()
        self.refresh_view()

    def _build_layout(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=7)
        root.columnconfigure(1, weight=3)
        root.rowconfigure(1, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="GLAT Table Operator", font=("Georgia", 18, "bold")).grid(row=0, column=0, sticky="w")
        self.header_label = ttk.Label(header, text="")
        self.header_label.grid(row=0, column=1, sticky="e")

        table = ttk.Frame(root)
        table.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)

        right = ttk.Frame(root)
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)
        right.rowconfigure(4, weight=1)

        self._build_table(table)
        self._build_controls(right)
        self._build_debug_panels(right)
        self._build_card_details(right)

        footer = ttk.Label(root, textvariable=self.status_var, anchor="w")
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))

    def _build_debug_panels(self, parent: ttk.Frame) -> None:
        debug_frame = ttk.LabelFrame(parent, text="Debug Panels", padding=10)
        debug_frame.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        debug_frame.columnconfigure(0, weight=1)
        debug_frame.rowconfigure(0, weight=1)
        self.debug_notebook = ttk.Notebook(debug_frame)
        self.debug_notebook.grid(row=0, column=0, sticky="nsew")
        self.replay_list = tk.Listbox(self.debug_notebook, exportselection=False)
        self.diff_list = tk.Listbox(self.debug_notebook, exportselection=False)
        self.battle_list = tk.Listbox(self.debug_notebook, exportselection=False)
        self.ai_debug_list = tk.Listbox(self.debug_notebook, exportselection=False)
        self.intake_list = tk.Listbox(self.debug_notebook, exportselection=False)
        self.debug_notebook.add(self.replay_list, text="Replay")
        self.debug_notebook.add(self.diff_list, text="Diff")
        self.debug_notebook.add(self.battle_list, text="Battle")
        self.debug_notebook.add(self.ai_debug_list, text="AI")
        self.debug_notebook.add(self.intake_list, text="Intake")

    def _build_table(self, parent: ttk.Frame) -> None:
        table = ttk.LabelFrame(parent, text="Board", padding=10)
        table.grid(row=0, column=0, sticky="nsew")
        for col in range(5):
            table.columnconfigure(col, weight=1)
        for row in range(8):
            table.rowconfigure(row, weight=1)

        self.match_banner = ttk.Label(table, anchor="center", font=("Segoe UI", 12, "bold"))
        self.match_banner.grid(row=0, column=0, columnspan=5, sticky="ew", pady=(0, 8))

        self._zone(table, "P1_hand", "AI Hand", 1, 0, 3)
        self._zone(table, "P1_life", "AI Life", 1, 3, 1)
        self._zone(table, "P1_piles", "AI Deck / Trash", 1, 4, 1)
        self._zone(table, "P1_leader", "AI Leader", 2, 0, 1)
        self._zone(table, "P1_board", "AI Character Area", 2, 1, 3)
        self._zone(table, "P1_don", "AI Cost / DON Area", 2, 4, 1)

        center = ttk.LabelFrame(table, text="Shared Battle / Selected Action", padding=8)
        center.grid(row=3, column=0, columnspan=5, sticky="nsew", pady=8)
        center.columnconfigure(0, weight=1)
        self.center_text = tk.Text(center, height=5, wrap="word")
        self.center_text.grid(row=0, column=0, sticky="nsew")
        self.center_text.configure(state="disabled")

        self._zone(table, "P2_don", "Human Cost / DON Area", 4, 0, 1)
        self._zone(table, "P2_board", "Human Character Area", 4, 1, 3)
        self._zone(table, "P2_leader", "Human Leader", 4, 4, 1)
        self._zone(table, "P2_piles", "Human Deck / Trash", 5, 0, 1)
        self._zone(table, "P2_life", "Human Life", 5, 1, 1)
        self._zone(table, "P2_hand", "Human Hand", 5, 2, 3)

    def _zone(self, parent: ttk.Frame, key: str, title: str, row: int, column: int, columnspan: int) -> None:
        frame = ttk.LabelFrame(parent, text=title, padding=6)
        frame.grid(row=row, column=column, columnspan=columnspan, sticky="nsew", padx=4, pady=4)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        canvas = tk.Canvas(frame, height=100, highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame, orient="horizontal", command=canvas.xview)
        canvas.configure(xscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=1, column=0, sticky="ew")

        inner = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind(
            "<Configure>",
            lambda _event, zone_key=key: self.zone_canvases[zone_key].configure(
                scrollregion=self.zone_canvases[zone_key].bbox("all")
            ),
        )
        self.zone_frames[key] = inner
        self.zone_canvases[key] = canvas

    def _build_controls(self, parent: ttk.Frame) -> None:
        actions = ttk.LabelFrame(parent, text="Operator Controls", padding=10)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)

        ttk.Button(actions, text="Refresh View", command=self.refresh_view).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(actions, text="New Game", command=self.new_game).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Button(actions, text="Load State", command=self.load_state_dialog).grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(8, 0))
        ttk.Button(actions, text="Save State", command=self.save_current_state).grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(8, 0))
        ttk.Button(actions, text="Run AI Turn", command=self.run_ai_turn).grid(row=2, column=0, sticky="ew", padx=(0, 6), pady=(8, 0))
        ttk.Button(actions, text="Prepare Human Turn", command=self.prepare_human_turn).grid(row=2, column=1, sticky="ew", padx=(6, 0), pady=(8, 0))
        ttk.Button(actions, text="End Human Turn", command=self.end_human_turn).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        replay = ttk.LabelFrame(parent, text="Replay View", padding=10)
        replay.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        replay.columnconfigure(0, weight=1)
        replay.columnconfigure(1, weight=1)
        replay.columnconfigure(2, weight=1)
        self.replay_label = ttk.Label(replay, text="Live state", anchor="center")
        self.replay_label.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        ttk.Button(replay, text="<", command=self.replay_back).grid(row=1, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(replay, text="Return Live", command=self.return_to_live).grid(row=1, column=1, sticky="ew", padx=4)
        ttk.Button(replay, text=">", command=self.replay_forward).grid(row=1, column=2, sticky="ew", padx=(4, 0))
        ttk.Button(replay, text="Restart This Turn", command=self.replay_restart_turn).grid(
            row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0)
        )

        command_frame = ttk.LabelFrame(parent, text="Console Command", padding=10)
        command_frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        command_frame.columnconfigure(0, weight=1)
        ttk.Button(command_frame, text="Open Match Console", command=self.open_match_console).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8)
        )
        ttk.Entry(command_frame, textvariable=self.command_var).grid(row=1, column=0, sticky="ew")
        ttk.Button(command_frame, text="Apply Command", command=self.apply_command).grid(row=1, column=1, padx=(8, 0))
        ttk.Label(
            command_frame,
            text='Examples: "played OP12-086 then attach 1 leader", "attack OP12-119 leader", "discard other one"',
            wraplength=420,
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _build_card_details(self, parent: ttk.Frame) -> None:
        details = ttk.LabelFrame(parent, text="Card Details", padding=10)
        details.grid(row=3, column=0, sticky="nsew")
        details.columnconfigure(0, weight=1)
        details.rowconfigure(0, weight=1)
        self.card_details = tk.Text(details, wrap="word", height=18)
        self.card_details.grid(row=0, column=0, sticky="nsew")
        self.card_details.configure(state="disabled")

    def _set_text(self, widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _set_listbox(self, widget: tk.Listbox, items: List[str]) -> None:
        widget.delete(0, tk.END)
        for item in items:
            widget.insert(tk.END, item)

    def _display_state(self) -> Dict[str, Any]:
        entries = self.state.get("replay_log", [])
        if self.replay_position is None or not entries:
            return self.state
        position = max(0, min(self.replay_position, len(entries) - 1))
        self.replay_position = position
        return replay_snapshot_to_display_state(entries[position][self.replay_side])

    def _render_zone(self, key: str, cards: List[Dict[str, Any]], empty_text: str = "empty") -> None:
        frame = self.zone_frames[key]
        for child in frame.winfo_children():
            child.destroy()
        if not cards:
            ttk.Label(frame, text=empty_text, foreground="#666").pack(anchor="w")
            self.zone_canvases[key].xview_moveto(0)
            return
        for card in cards:
            text = "\n".join(card_tile_lines(card))
            button = tk.Button(
                frame,
                text=text,
                justify="left",
                anchor="nw",
                width=18,
                height=4,
                relief=tk.RIDGE,
                bg="#f8f2df",
                activebackground="#fff7df",
                command=lambda selected=card, zone=key: self.select_card(selected, zone),
            )
            button.pack(side=tk.LEFT, padx=3, pady=3)
        frame.update_idletasks()
        self.zone_canvases[key].configure(scrollregion=self.zone_canvases[key].bbox("all"))
        self.zone_canvases[key].xview_moveto(0)

    def _render_pile_zone(self, key: str, player: Dict[str, Any], player_id: str) -> None:
        cards = [
            {
                "instance_id": f"{player_id}-DECK",
                "card_id": "Deck",
                "name": f"{len(player.get('deck', []))} cards",
                "state": "hidden",
            },
            {
                "instance_id": f"{player_id}-TRASH",
                "card_id": "Trash",
                "name": f"{len(player.get('trash', []))} cards",
                "state": "open",
            },
        ]
        self._render_zone(key, cards)

    def _render_don_zone(self, key: str, player: Dict[str, Any], player_id: str) -> None:
        cards = [
            {
                "instance_id": f"{player_id}-DON-AREA",
                "card_id": "DON Area",
                "name": f"{len(player.get('don_area', []))} available",
            },
            {
                "instance_id": f"{player_id}-DON-SPENT",
                "card_id": "Spent DON",
                "name": f"{len(player.get('spent_don', []))} spent",
            },
            {
                "instance_id": f"{player_id}-DON-ATTACHED",
                "card_id": "Attached DON",
                "name": f"{sum(player.get('attached_don', {}).values())} attached",
            },
        ]
        self._render_zone(key, cards)

    def _render_player(self, display_state: Dict[str, Any], player_id: str) -> None:
        player = display_state["players"][player_id]
        leader = dict(player["leader"])
        leader["attached_don"] = player.get("attached_don", {}).get(leader.get("instance_id"), leader.get("attached_don", 0))
        board_cards = []
        for card in player.get("board", []):
            view_card = dict(card)
            view_card["attached_don"] = player.get("attached_don", {}).get(
                view_card.get("instance_id"),
                view_card.get("attached_don", 0),
            )
            board_cards.append(view_card)
        self._render_zone(f"{player_id}_leader", [leader])
        self._render_zone(f"{player_id}_board", board_cards, "no characters")
        self._render_zone(f"{player_id}_hand", player.get("hand", []), "no hand cards")
        self._render_zone(
            f"{player_id}_life",
            build_hidden_life_cards(player_id, player.get("life", len(player.get("life_cards", [])))),
            "no life",
        )
        self._render_pile_zone(f"{player_id}_piles", player, player_id)
        self._render_don_zone(f"{player_id}_don", player, player_id)

    def _action_log_lines(self) -> List[str]:
        console_lines = format_console_lines(self.state)
        if console_lines != ["No console messages yet."]:
            return console_lines
        lines = collect_recent_log_lines(self.state, count=18)
        if not lines:
            return ["No actions logged yet."]
        return lines

    def open_match_console(self) -> None:
        if self.console_window is not None and self.console_window.winfo_exists():
            self._refresh_console_window()
            self.console_window.lift()
            return

        window = tk.Toplevel(self)
        window.title("GLAT Match Console")
        window.geometry("760x540")
        window.minsize(520, 360)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)

        history_frame = ttk.LabelFrame(window, text="Match Console", padding=10)
        history_frame.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 8))
        history_frame.columnconfigure(0, weight=1)
        history_frame.rowconfigure(0, weight=1)

        self.console_text = tk.Text(history_frame, wrap="word", height=20)
        scrollbar = ttk.Scrollbar(history_frame, orient="vertical", command=self.console_text.yview)
        self.console_text.configure(yscrollcommand=scrollbar.set, state="disabled")
        self.console_text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        command_frame = ttk.Frame(window, padding=(12, 0, 12, 12))
        command_frame.grid(row=1, column=0, sticky="ew")
        command_frame.columnconfigure(0, weight=1)
        command_entry = ttk.Entry(command_frame, textvariable=self.console_popup_command_var)
        command_entry.grid(row=0, column=0, sticky="ew")
        command_entry.bind("<Return>", lambda _event: self.apply_console_window_command())
        ttk.Button(command_frame, text="Send", command=self.apply_console_window_command).grid(
            row=0, column=1, padx=(8, 0)
        )

        window.protocol("WM_DELETE_WINDOW", self.close_match_console)
        self.console_window = window
        self._refresh_console_window()
        command_entry.focus_set()

    def close_match_console(self) -> None:
        if self.console_window is not None and self.console_window.winfo_exists():
            self.console_window.destroy()
        self.console_window = None
        self.console_text = None

    def _refresh_console_window(self) -> None:
        if self.console_text is None:
            return
        lines = self._action_log_lines()
        self.console_text.configure(state="normal")
        self.console_text.delete("1.0", tk.END)
        self.console_text.insert("1.0", "\n".join(lines))
        self.console_text.configure(state="disabled")
        self.console_text.see(tk.END)

    def apply_console_window_command(self) -> None:
        command = self.console_popup_command_var.get()
        try:
            changed, message = process_console_command(self.engine, self.state, command)
        except Exception as exc:
            messagebox.showerror("Command failed", str(exc), parent=self.console_window)
            append_console_entry(self.state, "System", f"Command failed: {exc}", kind="error")
            self.status_var.set(f"Command failed: {exc}")
            self._refresh_console_window()
            return
        if changed:
            self.console_popup_command_var.set("")
        self._save_and_refresh(message)
        self.status_var.set(message)

    def select_card(self, card: Dict[str, Any], zone: str) -> None:
        self.selected_card = card
        parts = zone.split("_", 1)
        self.selected_location = (parts[0], parts[1] if len(parts) > 1 else zone)
        self._set_text(
            self.card_details,
            "\n".join(card_detail_lines(card, self.selected_location[0], self.selected_location[1])),
        )

    def refresh_view(self) -> None:
        display_state = self._display_state()
        self.header_label.config(text=f"State file: {self.state_path.resolve()}")
        mode = "LIVE"
        if self.replay_position is not None and self.state.get("replay_log"):
            entry = self.state["replay_log"][self.replay_position]
            mode = f"REPLAY {entry['index']} {self.replay_side.upper()}"
            self.replay_label.config(text=replay_entry_label(entry))
        else:
            self.replay_label.config(text="Live state")
        self.match_banner.config(
            text=(
                f"{mode} | {display_state.get('match_mode', 'digital_strict')} | "
                f"Turn {display_state['turn']} | Active {display_state['active_player']} | "
                f"Phase {display_state['phase']} | Winner {display_state.get('winner') or '-'}"
            )
        )
        self._set_text(self.center_text, "\n".join(format_summary_lines(display_state)))
        self._refresh_console_window()
        self._set_listbox(self.replay_list, collect_replay_log_lines(self.state))
        self._set_listbox(self.intake_list, collect_intake_log_lines(self.state))
        self._set_listbox(self.diff_list, collect_latest_diff_lines(self.state))
        self._set_listbox(self.battle_list, collect_battle_trace_lines(self.state))
        self._set_listbox(self.ai_debug_list, collect_ai_debug_lines(self.state))

        for player_id in (AI_PLAYER, HUMAN_PLAYER):
            self._render_player(display_state, player_id)
        self._set_text(
            self.card_details,
            "\n".join(card_detail_lines(self.selected_card, *self.selected_location)),
        )

    def _save_and_refresh(self, message: str) -> None:
        self.replay_position = None
        save_state(self.engine, self.state, str(self.state_path))
        self.refresh_view()
        self.status_var.set(message)

    def return_to_live(self) -> None:
        self.replay_position = None
        self.replay_side = "after"
        self.refresh_view()
        self.status_var.set("Returned to live state.")

    def replay_back(self) -> None:
        entries = self.state.get("replay_log", [])
        if not entries:
            self.status_var.set("No replay entries yet.")
            return
        if self.replay_position is None:
            self.replay_position = len(entries) - 1
        else:
            self.replay_position = max(0, self.replay_position - 1)
        self.replay_side = "after"
        self.refresh_view()
        self.status_var.set("Showing earlier replay snapshot.")

    def replay_forward(self) -> None:
        entries = self.state.get("replay_log", [])
        if not entries:
            self.status_var.set("No replay entries yet.")
            return
        if self.replay_position is None:
            self.replay_position = 0
        elif self.replay_position >= len(entries) - 1:
            self.return_to_live()
            return
        else:
            self.replay_position += 1
        self.replay_side = "after"
        self.refresh_view()
        self.status_var.set("Showing later replay snapshot.")

    def replay_restart_turn(self) -> None:
        entries = self.state.get("replay_log", [])
        if not entries:
            self.status_var.set("No replay entries yet.")
            return
        turn = self.state["turn"] if self.replay_position is None else entries[self.replay_position]["turn"]
        for index, entry in enumerate(entries):
            if entry.get("turn") == turn:
                self.replay_position = index
                self.replay_side = "before"
                self.refresh_view()
                self.status_var.set(f"Showing start of turn {turn}.")
                return
        self.status_var.set(f"No replay entry found for turn {turn}.")

    def new_game(self) -> None:
        match_mode = self.state.get("match_mode", "digital_strict")
        self.state = self.engine.create_initial_state(seed=7, match_mode=match_mode)
        append_console_entry(self.state, "System", f"Started {match_mode} match.", kind="system")
        self._save_and_refresh("Started a new game.")

    def load_state_dialog(self) -> None:
        selected = filedialog.askopenfilename(
            title="Load GLAT state",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir=str(self.state_path.parent),
        )
        if not selected:
            return
        try:
            self.state_path = Path(selected)
            self.state = load_state(self.engine, selected)
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))
            self.status_var.set(f"Load failed: {exc}")
            return
        self._save_and_refresh(f"Loaded state from {selected}")

    def save_current_state(self) -> None:
        try:
            save_state(self.engine, self.state, str(self.state_path))
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            self.status_var.set(f"Save failed: {exc}")
            return
        self.refresh_view()
        self.status_var.set("Saved current state.")

    def run_ai_turn(self) -> None:
        if self.state["active_player"] != AI_PLAYER:
            self.status_var.set("It is not the AI turn.")
            return
        try:
            self.engine.run_turn(self.state)
        except Exception as exc:
            messagebox.showerror("AI turn failed", str(exc))
            self.status_var.set(f"AI turn failed: {exc}")
            return
        append_console_entry(self.state, "AI", "Ran AI turn.", kind="ai")
        self._save_and_refresh("Ran AI turn.")

    def prepare_human_turn(self) -> None:
        try:
            _, message = ensure_human_turn_ready(self.engine, self.state)
        except Exception as exc:
            messagebox.showerror("Prepare failed", str(exc))
            self.status_var.set(f"Prepare failed: {exc}")
            return
        append_console_entry(self.state, "System", message, kind="system")
        self._save_and_refresh(message)

    def end_human_turn(self) -> None:
        if self.state["active_player"] != HUMAN_PLAYER:
            self.status_var.set("It is not the human turn.")
            return
        end_action = {"type": "end_turn", "payload": {}}
        if not apply_human_action(self.engine, self.state, end_action):
            self.status_var.set("Could not end the human turn.")
            return
        self.engine.end_phase(self.state)
        finish_opponent_intake_session(self.state, "completed")
        append_console_entry(self.state, "System", "Ended human turn.", kind="system")
        self._save_and_refresh("Ended human turn.")

    def apply_command(self) -> None:
        try:
            changed, message = process_console_command(self.engine, self.state, self.command_var.get())
        except Exception as exc:
            messagebox.showerror("Command failed", str(exc))
            append_console_entry(self.state, "System", f"Command failed: {exc}", kind="error")
            self.status_var.set(f"Command failed: {exc}")
            return
        if changed:
            self._save_and_refresh(message)
            self.command_var.set("")
            return
        self._save_and_refresh(message)
        self.status_var.set(message)


def main() -> None:
    parser = argparse.ArgumentParser(description="GLAT local operator GUI")
    parser.add_argument("--state-out", default="cli_game_state.json")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--fake-ai", action="store_true", help="Use deterministic fake AI instead of Gemini")
    parser.add_argument(
        "--match-mode",
        choices=["digital_strict", "physical_reported"],
        default="digital_strict",
        help="Match mode for new state files",
    )
    args = parser.parse_args()

    app = OperatorGUI(
        state_path=args.state_out,
        use_fake_ai=args.fake_ai,
        seed=args.seed,
        match_mode=args.match_mode,
    )
    app.mainloop()


if __name__ == "__main__":
    main()
