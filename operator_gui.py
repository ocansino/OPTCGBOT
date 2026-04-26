import argparse
import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional, Tuple

from cli_game import (
    FakePlanningAgent,
    action_label,
    apply_human_action,
    begin_opponent_intake_session,
    card_label,
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
    print_opponent_intake_log,
    run_manual_command,
    save_state,
    start_manual_turn,
)
from glat_engine import GLATEngine


AI_PLAYER = "P1"
HUMAN_PLAYER = "P2"


def format_zone_cards(cards: List[Dict[str, Any]]) -> List[str]:
    return [card_label(card) for card in cards]


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

    return False, f"Could not apply command: {raw}"


class OperatorGUI(tk.Tk):
    def __init__(self, state_path: str, use_fake_ai: bool = False, seed: int = 7) -> None:
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
            self.state = self.engine.create_initial_state(seed=seed)
            save_state(self.engine, self.state, str(self.state_path))

        self.status_var = tk.StringVar(value="Ready")
        self.command_var = tk.StringVar()

        self._build_layout()
        self.refresh_view()

    def _build_layout(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=3)
        root.columnconfigure(1, weight=2)
        root.rowconfigure(1, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="GLAT Operator Panel", font=("Georgia", 18, "bold")).grid(row=0, column=0, sticky="w")
        self.header_label = ttk.Label(header, text="")
        self.header_label.grid(row=0, column=1, sticky="e")

        left = ttk.Frame(root)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        right = ttk.Frame(root)
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)
        right.rowconfigure(3, weight=1)
        right.rowconfigure(4, weight=1)
        right.rowconfigure(5, weight=1)

        self._build_player_panel(left, AI_PLAYER, 0)
        self._build_player_panel(left, HUMAN_PLAYER, 1)
        self._build_controls(right)
        self._build_logs(right)

        footer = ttk.Label(root, textvariable=self.status_var, anchor="w")
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))

    def _build_player_panel(self, parent: ttk.Frame, player_id: str, row: int) -> None:
        label = "AI" if player_id == AI_PLAYER else "Human / Opponent"
        frame = ttk.LabelFrame(parent, text=label, padding=10)
        frame.grid(row=row, column=0, sticky="nsew", pady=(0, 10) if row == 0 else 0)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(2, weight=1)
        frame.rowconfigure(2, weight=1)
        frame.rowconfigure(4, weight=1)

        summary = tk.Text(frame, height=4, wrap="word")
        summary.grid(row=0, column=0, columnspan=3, sticky="ew")
        summary.configure(state="disabled")

        ttk.Label(frame, text="Board").grid(row=1, column=0, sticky="w", pady=(8, 4))
        ttk.Label(frame, text="Hand").grid(row=1, column=1, sticky="w", pady=(8, 4))
        ttk.Label(frame, text="Life").grid(row=1, column=2, sticky="w", pady=(8, 4))

        board = tk.Listbox(frame, exportselection=False)
        board.grid(row=2, column=0, sticky="nsew", padx=(0, 6))
        hand = tk.Listbox(frame, exportselection=False)
        hand.grid(row=2, column=1, sticky="nsew", padx=6)
        life = tk.Listbox(frame, exportselection=False)
        life.grid(row=2, column=2, sticky="nsew", padx=(6, 0))

        ttk.Label(frame, text="Trash").grid(row=3, column=0, sticky="w", pady=(8, 4))
        ttk.Label(frame, text="DON / Misc").grid(row=3, column=1, columnspan=2, sticky="w", pady=(8, 4))

        trash = tk.Listbox(frame, exportselection=False)
        trash.grid(row=4, column=0, sticky="nsew", padx=(0, 6))
        misc = tk.Text(frame, height=6, wrap="word")
        misc.grid(row=4, column=1, columnspan=2, sticky="nsew", padx=(6, 0))
        misc.configure(state="disabled")

        setattr(self, f"{player_id.lower()}_summary", summary)
        setattr(self, f"{player_id.lower()}_board", board)
        setattr(self, f"{player_id.lower()}_hand", hand)
        setattr(self, f"{player_id.lower()}_life", life)
        setattr(self, f"{player_id.lower()}_trash", trash)
        setattr(self, f"{player_id.lower()}_misc", misc)

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

        command_frame = ttk.LabelFrame(parent, text="Report / Command", padding=10)
        command_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        command_frame.columnconfigure(0, weight=1)
        ttk.Entry(command_frame, textvariable=self.command_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(command_frame, text="Apply Command", command=self.apply_command).grid(row=0, column=1, padx=(8, 0))
        ttk.Label(
            command_frame,
            text='Examples: "played OP12-086 then attach 1 leader", "attack OP12-119 leader", "discard other one"',
            wraplength=420,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _build_logs(self, parent: ttk.Frame) -> None:
        summary_frame = ttk.LabelFrame(parent, text="Match Summary", padding=10)
        summary_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 10))
        summary_frame.columnconfigure(0, weight=1)
        summary_frame.rowconfigure(0, weight=1)
        self.summary_text = tk.Text(summary_frame, wrap="word")
        self.summary_text.grid(row=0, column=0, sticky="nsew")
        self.summary_text.configure(state="disabled")

        intake_frame = ttk.LabelFrame(parent, text="Opponent Intake", padding=10)
        intake_frame.grid(row=3, column=0, sticky="nsew", pady=(0, 10))
        intake_frame.columnconfigure(0, weight=1)
        intake_frame.rowconfigure(0, weight=1)
        self.intake_list = tk.Listbox(intake_frame, exportselection=False)
        self.intake_list.grid(row=0, column=0, sticky="nsew")

        log_frame = ttk.LabelFrame(parent, text="Action Log", padding=10)
        log_frame.grid(row=4, column=0, sticky="nsew", pady=(0, 10))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_list = tk.Listbox(log_frame, exportselection=False)
        self.log_list.grid(row=0, column=0, sticky="nsew")

        battle_frame = ttk.LabelFrame(parent, text="Battle Trace", padding=10)
        battle_frame.grid(row=5, column=0, sticky="nsew")
        battle_frame.columnconfigure(0, weight=1)
        battle_frame.rowconfigure(0, weight=1)
        self.battle_list = tk.Listbox(battle_frame, exportselection=False)
        self.battle_list.grid(row=0, column=0, sticky="nsew")

    def _set_text(self, widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _set_listbox(self, widget: tk.Listbox, items: List[str]) -> None:
        widget.delete(0, tk.END)
        for item in items:
            widget.insert(tk.END, item)

    def refresh_view(self) -> None:
        self.header_label.config(text=f"State file: {self.state_path.resolve()}")
        self._set_text(self.summary_text, "\n".join(format_summary_lines(self.state)))
        self._set_listbox(self.log_list, collect_recent_log_lines(self.state))
        self._set_listbox(self.intake_list, collect_intake_log_lines(self.state))
        self._set_listbox(self.battle_list, collect_battle_trace_lines(self.state))

        for player_id in (AI_PLAYER, HUMAN_PLAYER):
            player = self.state["players"][player_id]
            summary = getattr(self, f"{player_id.lower()}_summary")
            board = getattr(self, f"{player_id.lower()}_board")
            hand = getattr(self, f"{player_id.lower()}_hand")
            life = getattr(self, f"{player_id.lower()}_life")
            trash = getattr(self, f"{player_id.lower()}_trash")
            misc = getattr(self, f"{player_id.lower()}_misc")

            summary_lines = [
                card_label(player["leader"]),
                f"Life {player['life']} | Deck {len(player['deck'])} | Trash {len(player['trash'])}",
            ]
            misc_lines = [
                f"DON area: {len(player['don_area'])}",
                f"DON deck: {len(player['don_deck'])}",
                f"Spent DON: {len(player['spent_don'])}",
                f"Attached DON map: {player['attached_don']}",
            ]
            self._set_text(summary, "\n".join(summary_lines))
            self._set_listbox(board, format_zone_cards(player["board"]))
            self._set_listbox(hand, format_zone_cards(player["hand"]))
            self._set_listbox(life, format_zone_cards(player["life_cards"]))
            self._set_listbox(trash, format_zone_cards(player["trash"][-12:]))
            self._set_text(misc, "\n".join(misc_lines))

    def _save_and_refresh(self, message: str) -> None:
        save_state(self.engine, self.state, str(self.state_path))
        self.refresh_view()
        self.status_var.set(message)

    def new_game(self) -> None:
        self.state = self.engine.create_initial_state(seed=7)
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
        self._save_and_refresh("Ran AI turn.")

    def prepare_human_turn(self) -> None:
        try:
            _, message = ensure_human_turn_ready(self.engine, self.state)
        except Exception as exc:
            messagebox.showerror("Prepare failed", str(exc))
            self.status_var.set(f"Prepare failed: {exc}")
            return
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
        self._save_and_refresh("Ended human turn.")

    def apply_command(self) -> None:
        try:
            changed, message = process_operator_command(self.engine, self.state, self.command_var.get())
        except Exception as exc:
            messagebox.showerror("Command failed", str(exc))
            self.status_var.set(f"Command failed: {exc}")
            return
        if changed:
            self._save_and_refresh(message)
            self.command_var.set("")
            return
        self.status_var.set(message)


def main() -> None:
    parser = argparse.ArgumentParser(description="GLAT local operator GUI")
    parser.add_argument("--state-out", default="cli_game_state.json")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--fake-ai", action="store_true", help="Use deterministic fake AI instead of Gemini")
    args = parser.parse_args()

    app = OperatorGUI(state_path=args.state_out, use_fake_ai=args.fake_ai, seed=args.seed)
    app.mainloop()


if __name__ == "__main__":
    main()
