import json
import tkinter as tk
from tkinter import messagebox, ttk

from referee import Referee


MAT_BG = "#dfe8e5"
ZONE_BG = "#c7d3d1"
ZONE_INNER = "#e5ecea"
OUTLINE = "#506466"
TEXT = "#284245"
ACCENT = "#8aa3a4"
PANEL_BG = "#f3f4ef"


class TableGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("OPTCGBOT Table")
        self.geometry("1500x980")
        self.minsize(1200, 820)
        self.configure(bg=PANEL_BG)

        self.referee = Referee()
        self.referee.initialize_ai_from_cards_json()
        self.state = self.referee.load_state()
        self.card_id_var = tk.StringVar()
        self.attach_amount_var = tk.StringVar(value="1")
        self.heal_card_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        self.player_widgets = {}

        self._build_styles()
        self._build_ui()
        self.refresh_view()

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Panel.TFrame", background=PANEL_BG)
        style.configure("Panel.TLabelframe", background=PANEL_BG, bordercolor=OUTLINE)
        style.configure("Panel.TLabelframe.Label", background=PANEL_BG, foreground=TEXT, font=("Segoe UI", 10, "bold"))
        style.configure("Table.TButton", padding=6, font=("Segoe UI", 10, "bold"))

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14, style="Panel.TFrame")
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=5)
        root.columnconfigure(1, weight=2)
        root.rowconfigure(1, weight=1)

        header = ttk.Frame(root, style="Panel.TFrame")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        header.columnconfigure(1, weight=1)

        tk.Label(header, text="ONE PIECE CARD TABLE", bg=PANEL_BG, fg=TEXT, font=("Georgia", 22, "bold")).grid(row=0, column=0, sticky="w")
        self.turn_label = tk.Label(header, text="", bg=PANEL_BG, fg=TEXT, font=("Segoe UI", 12, "bold"))
        self.turn_label.grid(row=0, column=1, sticky="e")

        mats = ttk.Frame(root, style="Panel.TFrame")
        mats.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        mats.columnconfigure(0, weight=1)
        mats.rowconfigure(0, weight=1)

        self.player_widgets["AI"] = self._build_mat(mats, "AI", 0)

        sidebar = ttk.Frame(root, style="Panel.TFrame")
        sidebar.grid(row=1, column=1, sticky="nsew")
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(1, weight=1)
        sidebar.rowconfigure(2, weight=1)

        self._build_controls(sidebar)
        self._build_info(sidebar)

        tk.Label(root, textvariable=self.status_var, bg=PANEL_BG, fg=TEXT, anchor="w", font=("Segoe UI", 10)).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0)
        )

    def _zone(self, parent: tk.Widget, title: str, font_size: int = 16) -> tk.Frame:
        frame = tk.Frame(parent, bg=ZONE_BG, bd=2, relief="solid", highlightbackground=OUTLINE, highlightthickness=1)
        tk.Label(frame, text=title, bg=ZONE_BG, fg=TEXT, font=("Impact", font_size)).place(relx=0.5, rely=0.06, anchor="n")
        return frame

    def _listbox(self, parent: tk.Widget, height: int = 6, center: bool = False) -> tk.Listbox:
        return tk.Listbox(
            parent,
            bg=ZONE_INNER,
            fg=TEXT,
            font=("Segoe UI", 10, "bold" if center else "normal"),
            selectbackground=ACCENT,
            exportselection=False,
            justify="center" if center else "left",
            height=height,
        )

    def _build_mat(self, parent: ttk.Frame, player: str, row: int) -> dict[str, tk.Widget]:
        mat = tk.Frame(parent, bg=MAT_BG, bd=3, relief="solid", highlightbackground=OUTLINE, highlightthickness=1)
        mat.grid(row=row, column=0, sticky="nsew", pady=(0, 12) if row == 0 else 0)
        mat.columnconfigure(0, weight=1)
        mat.columnconfigure(1, weight=5)
        mat.rowconfigure(0, weight=4)
        mat.rowconfigure(1, weight=3)
        mat.rowconfigure(2, weight=1)

        tk.Label(mat, text=f"{player.upper()} TABLE", bg=MAT_BG, fg=TEXT, font=("Georgia", 16, "bold")).place(x=16, y=10)

        left = tk.Frame(mat, bg=MAT_BG)
        left.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=14, pady=(46, 14))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=4)
        left.rowconfigure(1, weight=2)

        life = self._zone(left, "LIFE", 12)
        life.grid(row=0, column=0, sticky="nsew", pady=(0, 12))
        life.columnconfigure(0, weight=1)
        life.rowconfigure(0, weight=1)
        life_list = self._listbox(life, height=5, center=True)
        life_list.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)

        don = self._zone(left, "DON!! DECK", 10)
        don.grid(row=1, column=0, sticky="nsew")
        don_label = tk.Label(don, text="0 / 0", bg=ZONE_INNER, fg=TEXT, font=("Segoe UI", 16, "bold"))
        don_label.pack(expand=True, fill="both", padx=12, pady=12)

        chars = self._zone(mat, "CHARACTER AREA", 18)
        chars.grid(row=0, column=1, sticky="nsew", padx=(0, 14), pady=(46, 10))
        chars.columnconfigure(0, weight=1)
        chars.rowconfigure(0, weight=1)
        char_list = self._listbox(chars, height=7)
        char_list.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)

        lower = tk.Frame(mat, bg=MAT_BG)
        lower.grid(row=1, column=1, sticky="nsew", padx=(0, 14), pady=(0, 10))
        for index, weight in enumerate((2, 1, 1, 1)):
            lower.columnconfigure(index, weight=weight)
        lower.rowconfigure(0, weight=1)
        lower.rowconfigure(1, weight=2)

        strip = tk.Frame(lower, bg=MAT_BG)
        strip.grid(row=0, column=0, sticky="w", padx=(8, 16), pady=(0, 8))
        for label in ("Refresh", "Draw", "Main", "End"):
            tk.Label(strip, text=label, bg=ZONE_BG, fg=TEXT, font=("Segoe UI", 9, "bold"), width=9, relief="solid", bd=1).pack(side="left", padx=(0, 8))

        leader = self._zone(lower, "LEADER", 14)
        leader.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=10)
        leader_label = tk.Label(leader, text="", bg=ZONE_INNER, fg=TEXT, font=("Segoe UI", 10, "bold"), justify="center")
        leader_label.pack(expand=True, fill="both", padx=12, pady=12)

        stage = self._zone(lower, "STAGE", 14)
        stage.grid(row=0, column=2, rowspan=2, sticky="nsew", padx=10)
        stage_list = self._listbox(stage, height=3, center=True)
        stage_list.pack(expand=True, fill="both", padx=12, pady=12)

        deck = self._zone(lower, "DECK", 14)
        deck.grid(row=0, column=3, sticky="nsew", padx=(10, 0), pady=(0, 10))
        deck_label = tk.Label(deck, text="0", bg=ZONE_INNER, fg=TEXT, font=("Segoe UI", 16, "bold"))
        deck_label.pack(expand=True, fill="both", padx=12, pady=12)

        trash = self._zone(lower, "TRASH", 14)
        trash.grid(row=1, column=3, sticky="nsew", padx=(10, 0))
        trash_list = self._listbox(trash, height=4, center=True)
        trash_list.pack(expand=True, fill="both", padx=12, pady=12)

        hand = self._zone(mat, "HAND", 12)
        hand.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 14))
        hand.columnconfigure(0, weight=1)
        hand.rowconfigure(0, weight=1)
        hand_list = tk.Listbox(hand, bg=ZONE_INNER, fg=TEXT, font=("Consolas", 10), selectbackground=ACCENT, exportselection=False, height=3)
        hand_list.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)

        return {
            "life": life_list,
            "don": don_label,
            "characters": char_list,
            "leader": leader_label,
            "stage": stage_list,
            "deck": deck_label,
            "trash": trash_list,
            "hand": hand_list,
        }

    def _build_controls(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Controls", padding=12, style="Panel.TLabelframe")
        frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Target").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="AI board only").grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(frame, text="Card ID").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(frame, textvariable=self.card_id_var).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(10, 0))
        ttk.Label(frame, text="Attach DON").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(frame, textvariable=self.attach_amount_var).grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=(10, 0))
        ttk.Label(frame, text="Heal card").grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(frame, textvariable=self.heal_card_var).grid(row=3, column=1, sticky="ew", padx=(8, 0), pady=(10, 0))

        buttons = [
            ("Refresh", self.refresh_view),
            ("Play Card", self.play_card),
            ("Draw 1", self.draw_card),
            ("Shuffle Deck", self.shuffle_deck),
            ("Take Damage", self.take_damage),
            ("Heal 1", self.heal_player),
            ("Attach DON", self.attach_don),
            ("Rest Card", self.rest_card),
            ("Unrest Card", self.unrest_card),
            ("Remove From Field", self.remove_from_field),
        ]
        for row, (label, command) in enumerate(buttons, start=4):
            ttk.Button(frame, text=label, command=command, style="Table.TButton").grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _build_info(self, parent: ttk.Frame) -> None:
        feed = ttk.LabelFrame(parent, text="Match Feed", padding=12, style="Panel.TLabelframe")
        feed.grid(row=1, column=0, sticky="nsew", pady=(0, 10))
        feed.columnconfigure(0, weight=1)
        feed.rowconfigure(1, weight=1)

        self.summary_label = tk.Label(feed, text="", bg=PANEL_BG, fg=TEXT, justify="left", anchor="nw", font=("Segoe UI", 10))
        self.summary_label.grid(row=0, column=0, sticky="ew")

        self.effect_list = tk.Listbox(feed, bg="white", fg=TEXT, font=("Segoe UI", 10), selectbackground=ACCENT, exportselection=False)
        self.effect_list.grid(row=1, column=0, sticky="nsew", pady=(10, 0))

        raw = ttk.LabelFrame(parent, text="Raw State", padding=12, style="Panel.TLabelframe")
        raw.grid(row=2, column=0, sticky="nsew")
        raw.columnconfigure(0, weight=1)
        raw.rowconfigure(0, weight=1)

        self.raw_state_text = tk.Text(raw, wrap="word", bg="white", fg=TEXT, font=("Consolas", 9))
        self.raw_state_text.grid(row=0, column=0, sticky="nsew")
        self.raw_state_text.configure(state="disabled")

    def _selected_card_id(self) -> str:
        selected = self.player_widgets["AI"]["hand"].curselection()
        if selected:
            return self.player_widgets["AI"]["hand"].get(selected[0]).split(" | ")[0].strip()
        return self.card_id_var.get().strip().upper()

    def _selected_board_target(self) -> str:
        for zone_name in ("characters", "stage"):
            widget = self.player_widgets["AI"][zone_name]
            selected = widget.curselection()
            if selected:
                return widget.get(selected[0]).split(" | ")[0].strip()
        raise ValueError("Select a card in the character or stage area first")

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
        self.turn_label.config(text=f"Turn {self.state.get('turn', 0)} | Active: {self.state.get('active_player', '-')}")

        player_state = self.state["ai_player"]
        widgets = self.player_widgets["AI"]
        leader = player_state["leader"]
        widgets["leader"].config(text=f"{leader.get('name', 'Leader')}\n{leader.get('power', 0)} power\nLife {leader.get('life', 0)}")
        widgets["don"].config(text=f"{player_state.get('don_available', 0)} / {player_state.get('don_total', 0)}")
        widgets["deck"].config(text=str(len(player_state.get("deck", []))))

        widgets["life"].delete(0, tk.END)
        life_cards = player_state.get("life_cards", [])
        if life_cards:
            for index, card_id in enumerate(life_cards, start=1):
                widgets["life"].insert(tk.END, f"{index}. {card_id}")
        else:
            widgets["life"].insert(tk.END, f"Life: {leader.get('life', 0)}")

        widgets["characters"].delete(0, tk.END)
        widgets["stage"].delete(0, tk.END)
        board_cards = player_state.get("board", [])
        character_cards = [card for card in board_cards if card.get("category") != "Stage"]
        stage_cards = [card for card in board_cards if card.get("category") == "Stage"]

        if character_cards:
            for card in character_cards:
                total_power = (card.get("base_power") or 0) + card.get("power_bonus", 0)
                mode = "REST" if card.get("is_rested") else "ACTIVE"
                widgets["characters"].insert(tk.END, f"{card.get('id', '')} | {card.get('name', '')} | {total_power} | DON {card.get('attached_don', 0)} | {mode}")
        else:
            widgets["characters"].insert(tk.END, "No characters on board")

        if stage_cards:
            for card in stage_cards:
                widgets["stage"].insert(tk.END, f"{card.get('id', '')} | {card.get('name', '')}")
        else:
            widgets["stage"].insert(tk.END, "No stage")

        widgets["trash"].delete(0, tk.END)
        trash_cards = player_state.get("trash", [])
        if trash_cards:
            for card_id in trash_cards[-6:]:
                widgets["trash"].insert(tk.END, card_id)
        else:
            widgets["trash"].insert(tk.END, "Empty")

        widgets["hand"].delete(0, tk.END)
        hand_cards = player_state.get("hand", [])
        if hand_cards:
            for card_id in hand_cards:
                widgets["hand"].insert(tk.END, card_id)
        else:
            widgets["hand"].insert(tk.END, "Empty hand")

        self.effect_list.delete(0, tk.END)
        for entry in self.state.get("effect_log", [])[-12:]:
            self.effect_list.insert(tk.END, f"{entry.get('player')} | {entry.get('source')} -> {entry.get('effect')}")

        self.summary_label.config(
            text=(
                f"AI life {player_state['leader'].get('life', 0)} | hand {player_state.get('hand_count', 0)} | deck {len(player_state.get('deck', []))}\n"
                f"Board {len(character_cards)} | stage {len(stage_cards)} | trash {len(player_state.get('trash', []))}\n"
                f"Leader: {leader.get('name', 'Leader')}"
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
        self._run_action(lambda: self.referee.play_card("AI", card_id), f"AI played {card_id}")

    def draw_card(self) -> None:
        self._run_action(lambda: self.referee.draw_cards("AI", 1), "AI drew 1 card")

    def shuffle_deck(self) -> None:
        self._run_action(lambda: self.referee.shuffle_deck("AI"), "AI deck shuffled")

    def take_damage(self) -> None:
        self._run_action(lambda: self.referee.take_damage("AI"), "AI took 1 damage")

    def heal_player(self) -> None:
        card_id = self.heal_card_var.get().strip().upper()
        cards = [card_id] if card_id else None
        self._run_action(lambda: self.referee.heal(1, "AI", cards=cards), "AI healed 1 life")

    def attach_don(self) -> None:
        target = self._selected_board_target()
        amount = int(self.attach_amount_var.get())
        self._run_action(lambda: self.referee.attach_don("AI", target, amount), f"Attached {amount} DON!! to {target}")

    def remove_from_field(self) -> None:
        target = self._selected_board_target()
        self._run_action(lambda: self.referee.remove_from_field("AI", target, destination="trash"), f"Moved {target} from field to trash")

    def rest_card(self) -> None:
        target = self._selected_board_target()
        self._run_action(lambda: self.referee.rest_card("AI", target), f"Rested {target}")

    def unrest_card(self) -> None:
        target = self._selected_board_target()
        self._run_action(lambda: self.referee.unrest_card("AI", target), f"Activated {target}")


if __name__ == "__main__":
    app = TableGUI()
    app.mainloop()
