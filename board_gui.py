import json
import tkinter as tk
from tkinter import messagebox, ttk

from referee import Referee


class BoardGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("OPTCGBOT Board")
        self.geometry("1200x780")
        self.minsize(980, 680)

        self.referee = Referee()
        self.state = self.referee.load_state()

        self.selected_player = tk.StringVar(value="AI")
        self.card_id_var = tk.StringVar()
        self.attach_amount_var = tk.StringVar(value="1")
        self.heal_card_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")

        self.player_widgets = {}

        self._build_layout()
        self.refresh_view()

    def _build_layout(self) -> None:
        container = ttk.Frame(self, padding=12)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)

        header = ttk.Frame(container)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header.columnconfigure(1, weight=1)

        ttk.Label(header, text="OPTCGBOT Board State", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.turn_label = ttk.Label(header, text="")
        self.turn_label.grid(row=0, column=1, sticky="e")

        middle = ttk.Panedwindow(container, orient="horizontal")
        middle.grid(row=1, column=0, sticky="nsew")

        left = ttk.Frame(middle, padding=(0, 0, 8, 0))
        center = ttk.Frame(middle, padding=8)
        right = ttk.Frame(middle, padding=(8, 0, 0, 0))

        middle.add(left, weight=3)
        middle.add(center, weight=2)
        middle.add(right, weight=2)

        self._build_players_panel(left)
        self._build_controls_panel(center)
        self._build_state_panel(right)

        footer = ttk.Label(container, textvariable=self.status_var, anchor="w")
        footer.grid(row=2, column=0, sticky="ew", pady=(10, 0))

    def _build_players_panel(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        self.player_widgets["AI"] = self._build_player_frame(parent, "AI", 0)
        self.player_widgets["Human"] = self._build_player_frame(parent, "Human", 1)

    def _build_player_frame(
        self, parent: ttk.Frame, label: str, row: int
    ) -> dict[str, tk.Widget]:
        frame = ttk.LabelFrame(parent, text=f"{label} Player", padding=10)
        frame.grid(row=row, column=0, sticky="nsew", pady=(0, 10) if row == 0 else 0)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(2, weight=1)
        frame.rowconfigure(4, weight=1)

        leader_label = ttk.Label(frame, text="", font=("Segoe UI", 11, "bold"))
        leader_label.grid(row=0, column=0, columnspan=2, sticky="w")

        stats_label = ttk.Label(frame, text="", justify="left")
        stats_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 8))

        ttk.Label(frame, text="Board").grid(row=2, column=0, sticky="w")
        ttk.Label(frame, text="Hand").grid(row=2, column=1, sticky="w")

        board_list = tk.Listbox(frame, exportselection=False, height=10)
        board_list.grid(row=3, column=0, sticky="nsew", padx=(0, 6))
        hand_list = tk.Listbox(frame, exportselection=False, height=10)
        hand_list.grid(row=3, column=1, sticky="nsew", padx=(6, 0))

        zones_label = ttk.Label(frame, text="", justify="left")
        zones_label.grid(row=4, column=0, columnspan=2, sticky="nw", pady=(8, 0))

        return {
            "leader": leader_label,
            "stats": stats_label,
            "board": board_list,
            "hand": hand_list,
            "zones": zones_label,
        }

    def _build_controls_panel(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)

        turn_frame = ttk.LabelFrame(parent, text="Match", padding=10)
        turn_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        turn_frame.columnconfigure(1, weight=1)

        ttk.Label(turn_frame, text="Selected player").grid(row=0, column=0, sticky="w")
        player_box = ttk.Combobox(
            turn_frame,
            textvariable=self.selected_player,
            values=["AI", "Human"],
            state="readonly",
        )
        player_box.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Button(turn_frame, text="Refresh", command=self.refresh_view).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0)
        )

        action_frame = ttk.LabelFrame(parent, text="Actions", padding=10)
        action_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        action_frame.columnconfigure(1, weight=1)

        ttk.Label(action_frame, text="Card ID").grid(row=0, column=0, sticky="w")
        ttk.Entry(action_frame, textvariable=self.card_id_var).grid(
            row=0, column=1, sticky="ew", padx=(8, 0)
        )

        ttk.Button(action_frame, text="Play Card", command=self.play_card).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )
        ttk.Button(action_frame, text="Draw 1", command=self.draw_card).grid(
            row=2, column=0, sticky="ew", pady=(8, 0)
        )
        ttk.Button(action_frame, text="Shuffle Deck", command=self.shuffle_deck).grid(
            row=2, column=1, sticky="ew", padx=(8, 0), pady=(8, 0)
        )
        ttk.Button(action_frame, text="Take Damage", command=self.take_damage).grid(
            row=3, column=0, sticky="ew", pady=(8, 0)
        )
        ttk.Button(action_frame, text="Remove From Field", command=self.remove_from_field).grid(
            row=3, column=1, sticky="ew", padx=(8, 0), pady=(8, 0)
        )
        ttk.Button(action_frame, text="Rest Card", command=self.rest_card).grid(
            row=4, column=0, sticky="ew", pady=(8, 0)
        )
        ttk.Button(action_frame, text="Unrest Card", command=self.unrest_card).grid(
            row=4, column=1, sticky="ew", padx=(8, 0), pady=(8, 0)
        )

        don_frame = ttk.LabelFrame(parent, text="DON / Life", padding=10)
        don_frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        don_frame.columnconfigure(1, weight=1)

        ttk.Label(don_frame, text="Attach DON amount").grid(row=0, column=0, sticky="w")
        ttk.Entry(don_frame, textvariable=self.attach_amount_var).grid(
            row=0, column=1, sticky="ew", padx=(8, 0)
        )
        ttk.Button(don_frame, text="Attach To Selected Board Card", command=self.attach_don).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )

        ttk.Label(don_frame, text="Heal card ID (optional)").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(don_frame, textvariable=self.heal_card_var).grid(
            row=2, column=1, sticky="ew", padx=(8, 0), pady=(10, 0)
        )
        ttk.Button(don_frame, text="Heal 1", command=self.heal_player).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )

        effect_frame = ttk.LabelFrame(parent, text="Effect Log", padding=10)
        effect_frame.grid(row=3, column=0, sticky="nsew")
        effect_frame.columnconfigure(0, weight=1)
        effect_frame.rowconfigure(0, weight=1)

        self.effect_list = tk.Listbox(effect_frame, exportselection=False, height=12)
        self.effect_list.grid(row=0, column=0, sticky="nsew")

    def _build_state_panel(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        summary_frame = ttk.LabelFrame(parent, text="Summary", padding=10)
        summary_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        summary_frame.columnconfigure(0, weight=1)

        self.summary_label = ttk.Label(summary_frame, text="", justify="left")
        self.summary_label.grid(row=0, column=0, sticky="w")

        raw_frame = ttk.LabelFrame(parent, text="Raw State", padding=10)
        raw_frame.grid(row=1, column=0, sticky="nsew")
        raw_frame.columnconfigure(0, weight=1)
        raw_frame.rowconfigure(0, weight=1)

        self.raw_state_text = tk.Text(raw_frame, wrap="word", width=40)
        self.raw_state_text.grid(row=0, column=0, sticky="nsew")
        self.raw_state_text.configure(state="disabled")

    def _player_alias(self) -> str:
        return self.selected_player.get()

    def _state_key(self, player: str) -> str:
        return "ai_player" if player == "AI" else "human_player"

    def _selected_card_id(self) -> str:
        player = self._player_alias()
        selected = self.player_widgets[player]["hand"].curselection()
        if selected:
            value = self.player_widgets[player]["hand"].get(selected[0])
            return value.split(" | ")[0].strip()
        return self.card_id_var.get().strip().upper()

    def _selected_board_target(self) -> str:
        player = self._player_alias()
        selected = self.player_widgets[player]["board"].curselection()
        if not selected:
            raise ValueError("Select a card on the board first")
        value = self.player_widgets[player]["board"].get(selected[0])
        return value.split(" | ")[0].strip()

    def _run_action(self, action, success_message: str) -> None:
        try:
            action()
        except Exception as exc:
            messagebox.showerror("Action failed", str(exc))
            self.status_var.set(f"Error: {exc}")
            return

        self.refresh_view()
        self.status_var.set(success_message)

    def refresh_view(self) -> None:
        self.state = self.referee.load_state()
        self.turn_label.config(
            text=f"Turn {self.state.get('turn', 0)} | Active: {self.state.get('active_player', '-')}"
        )

        for player in ("AI", "Human"):
            player_state = self.state[self._state_key(player)]
            widgets = self.player_widgets[player]
            leader = player_state["leader"]

            widgets["leader"].config(
                text=f"{leader.get('name', 'Leader')} | {leader.get('power', 0)} power"
            )
            widgets["stats"].config(
                text=(
                    f"Life: {leader.get('life', 0)}\n"
                    f"DON!!: {player_state.get('don_available', 0)}/{player_state.get('don_total', 0)}\n"
                    f"Hand Count: {player_state.get('hand_count', 0)}"
                )
            )

            widgets["board"].delete(0, tk.END)
            for card in player_state.get("board", []):
                total_power = (card.get("base_power") or 0) + card.get("power_bonus", 0)
                rested = "Rested" if card.get("is_rested") else "Active"
                widgets["board"].insert(
                    tk.END,
                    (
                        f"{card.get('id', '')} | {card.get('name', '')} | "
                        f"{total_power} power | DON {card.get('attached_don', 0)} | {rested}"
                    ),
                )

            widgets["hand"].delete(0, tk.END)
            for card_id in player_state.get("hand", []):
                widgets["hand"].insert(tk.END, card_id)

            widgets["zones"].config(
                text=(
                    f"Deck: {len(player_state.get('deck', []))}\n"
                    f"Trash: {len(player_state.get('trash', []))}\n"
                    f"Life Cards: {len(player_state.get('life_cards', []))}"
                )
            )

        self.effect_list.delete(0, tk.END)
        for entry in self.state.get("effect_log", [])[-12:]:
            self.effect_list.insert(
                tk.END,
                f"{entry.get('player')} | {entry.get('source')} -> {entry.get('effect')}",
            )

        ai_state = self.state["ai_player"]
        human_state = self.state["human_player"]
        self.summary_label.config(
            text=(
                f"AI board: {len(ai_state.get('board', []))} cards\n"
                f"Human board: {len(human_state.get('board', []))} cards\n"
                f"AI trash: {len(ai_state.get('trash', []))} cards\n"
                f"Human trash: {len(human_state.get('trash', []))} cards"
            )
        )

        self.raw_state_text.configure(state="normal")
        self.raw_state_text.delete("1.0", tk.END)
        self.raw_state_text.insert("1.0", json.dumps(self.state, indent=2))
        self.raw_state_text.configure(state="disabled")

    def play_card(self) -> None:
        card_id = self._selected_card_id()
        if not card_id:
            messagebox.showwarning("Missing card", "Enter or select a card ID from hand")
            return

        player = self._player_alias()
        self._run_action(
            lambda: self.referee.play_card(player, card_id),
            f"{player} played {card_id}",
        )

    def draw_card(self) -> None:
        player = self._player_alias()
        self._run_action(
            lambda: self.referee.draw_cards(player, 1),
            f"{player} drew 1 card",
        )

    def shuffle_deck(self) -> None:
        player = self._player_alias()
        self._run_action(
            lambda: self.referee.shuffle_deck(player),
            f"{player}'s deck shuffled",
        )

    def take_damage(self) -> None:
        player = self._player_alias()
        self._run_action(
            lambda: self.referee.take_damage(player),
            f"{player} took 1 damage",
        )

    def heal_player(self) -> None:
        player = self._player_alias()
        card_id = self.heal_card_var.get().strip().upper()
        cards = [card_id] if card_id else None
        self._run_action(
            lambda: self.referee.heal(1, player, cards=cards),
            f"{player} healed 1 life",
        )

    def attach_don(self) -> None:
        player = self._player_alias()
        target = self._selected_board_target()
        amount = int(self.attach_amount_var.get())
        self._run_action(
            lambda: self.referee.attach_don(player, target, amount),
            f"Attached {amount} DON!! to {target}",
        )

    def remove_from_field(self) -> None:
        player = self._player_alias()
        target = self._selected_board_target()
        self._run_action(
            lambda: self.referee.remove_from_field(player, target, destination="trash"),
            f"Moved {target} from field to trash",
        )

    def rest_card(self) -> None:
        player = self._player_alias()
        target = self._selected_board_target()
        self._run_action(
            lambda: self.referee.rest_card(player, target),
            f"Rested {target}",
        )

    def unrest_card(self) -> None:
        player = self._player_alias()
        target = self._selected_board_target()
        self._run_action(
            lambda: self.referee.unrest_card(player, target),
            f"Activated {target}",
        )


if __name__ == "__main__":
    app = BoardGUI()
    app.mainloop()
