import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional


class Referee:
    def __init__(
        self,
        state_path: str = "game_state.json",
        cards_path: str = "cards.json",
        cards_root: str = "cards",
    ) -> None:
        self.state_path = Path(state_path)
        self.cards_path = Path(cards_path)
        self.cards_root = Path(cards_root)
        self.cards_index_path = self.cards_root / "index" / "cards_by_id.json"
        self.card_lookup = self._load_card_lookup()

    def _load_json(self, path: Path) -> Dict[str, Any]:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def _write_json(self, path: Path, data: Dict[str, Any]) -> None:
        with path.open("w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
            file.write("\n")

    def _load_card_lookup(self) -> Dict[str, Dict[str, Any]]:
        lookup: Dict[str, Dict[str, Any]] = {}

        if self.cards_path.exists():
            cards_data = self._load_json(self.cards_path)
            for card in cards_data.get("cards", []):
                card_id = card.get("id")
                if card_id:
                    lookup[card_id.upper()] = card

        return lookup

    def _load_deck_cards(self) -> List[Dict[str, Any]]:
        if not self.cards_path.exists():
            return []
        cards_data = self._load_json(self.cards_path)
        return cards_data.get("cards", [])

    def _leader_life_from_card(self, leader_card: Dict[str, Any]) -> int:
        colors = leader_card.get("colors", [])
        return 4 if len(colors) > 1 else 5

    def _expand_deck_from_cards_json(self) -> tuple[Dict[str, Any], List[str]]:
        leader_card: Optional[Dict[str, Any]] = None
        deck_cards: List[str] = []

        for card in self._load_deck_cards():
            amount = int(card.get("amount", 0))
            if amount <= 0:
                continue

            if card.get("category") == "Leader":
                leader_card = card
                continue

            deck_cards.extend([card["id"].upper()] * amount)

        if leader_card is None:
            raise ValueError("No leader card found in cards.json")

        return leader_card, deck_cards

    def _resolve_card_file(self, card_id: str, pack_id: str) -> Path:
        return self.cards_root / "cards" / pack_id / f"{card_id}.json"

    def load_state(self) -> Dict[str, Any]:
        state = self._load_json(self.state_path)
        self._normalize_state(state)
        return state

    def save_state(self, state: Dict[str, Any]) -> None:
        self._write_json(self.state_path, state)

    def _normalize_state(self, state: Dict[str, Any]) -> None:
        for player_key in ("ai_player", "human_player"):
            if player_key not in state:
                continue

            player_state = state[player_key]
            player_state.setdefault("board", [])
            player_state.setdefault("hand", [])
            player_state.setdefault("deck", [])
            player_state.setdefault("trash", [])
            player_state.setdefault("life_cards", [])
            player_state.setdefault("don_total", 0)
            player_state.setdefault("don_available", 0)
            player_state["hand_count"] = len(player_state["hand"])

            leader = player_state.setdefault("leader", {})
            leader.setdefault("name", "")
            leader.setdefault("power", 5000)
            leader.setdefault("life", len(player_state["life_cards"]))
            leader.setdefault("is_rested", False)

    def initialize_ai_from_cards_json(self) -> Dict[str, Any]:
        state = self.load_state()
        leader_card, full_deck = self._expand_deck_from_cards_json()
        life_count = self._leader_life_from_card(leader_card)

        ai_player = state.setdefault("ai_player", {})
        ai_player["leader"] = {
            "name": leader_card.get("name", ""),
            "power": leader_card.get("power", 5000),
            "life": life_count,
            "is_rested": False,
            "id": leader_card.get("id", "").upper(),
            "colors": leader_card.get("colors", []),
        }
        ai_player["board"] = []
        ai_player["trash"] = []
        ai_player["don_total"] = ai_player.get("don_total", 0)
        ai_player["don_available"] = ai_player.get("don_available", 0)
        ai_player["life_cards"] = full_deck[:life_count]
        ai_player["hand"] = full_deck[life_count : life_count + 5]
        ai_player["deck"] = full_deck[life_count + 5 :]
        self._update_hand_count(ai_player)

        self.save_state(state)
        return state

    def _player_key(self, player: str) -> str:
        normalized = player.strip().lower()
        aliases = {
            "ai": "ai_player",
            "ai_player": "ai_player",
            "bot": "ai_player",
            "human": "human_player",
            "human_player": "human_player",
            "player": "human_player",
        }

        if normalized not in aliases:
            raise ValueError(f"Unknown player: {player}")

        return aliases[normalized]

    def _get_player_state(self, state: Dict[str, Any], player: str) -> Dict[str, Any]:
        return state[self._player_key(player)]

    def _update_hand_count(self, player_state: Dict[str, Any]) -> None:
        player_state["hand_count"] = len(player_state["hand"])

    def _get_card_data(self, card_id: str) -> Dict[str, Any]:
        normalized_id = card_id.upper()
        card = self.card_lookup.get(normalized_id)
        if card is None and self.cards_index_path.exists():
            cards_index = self._load_json(self.cards_index_path)
            index_entry = cards_index.get(normalized_id)
            if index_entry:
                card_file = self._resolve_card_file(normalized_id, index_entry["pack_id"])
                if card_file.exists():
                    card = self._load_json(card_file)
                    self.card_lookup[normalized_id] = card

        if card is None:
            raise ValueError(f"Card {normalized_id} could not be found in card data")

        return card

    def _find_board_card(
        self, board: List[Dict[str, Any]], target: str
    ) -> Dict[str, Any]:
        for card in board:
            if card.get("instance_id") == target or card.get("id") == target.upper():
                return card
        raise ValueError(f"Board target not found: {target}")

    def play_card(self, player: str, card_id: str) -> Dict[str, Any]:
        state = self.load_state()
        player_state = self._get_player_state(state, player)
        normalized_id = card_id.upper()

        if normalized_id not in [card.upper() for card in player_state["hand"]]:
            raise ValueError(f"Card {normalized_id} is not in {player}'s hand")

        card_data = self._get_card_data(normalized_id)
        cost = card_data.get("cost") or 0
        if player_state["don_available"] < cost:
            raise ValueError(
                f"{player} does not have enough DON!! to play {normalized_id}"
            )

        hand_index = next(
            index
            for index, hand_card in enumerate(player_state["hand"])
            if hand_card.upper() == normalized_id
        )
        player_state["hand"].pop(hand_index)
        player_state["don_available"] -= cost

        board_card = {
            "id": normalized_id,
            "name": card_data.get("name"),
            "category": card_data.get("category"),
            "cost": cost,
            "base_power": card_data.get("power"),
            "attached_don": 0,
            "power_bonus": 0,
            "is_rested": False,
            "status": "active",
        }
        player_state["board"].append(board_card)

        self._update_hand_count(player_state)
        self.save_state(state)
        return board_card

    def attach_don(self, player: str, target: str, amount: int) -> Dict[str, Any]:
        if amount <= 0:
            raise ValueError("amount must be greater than 0")

        state = self.load_state()
        player_state = self._get_player_state(state, player)
        if player_state["don_available"] < amount:
            raise ValueError(f"{player} does not have enough DON!! to attach")

        board_card = self._find_board_card(player_state["board"], target)
        player_state["don_available"] -= amount
        board_card["attached_don"] = board_card.get("attached_don", 0) + amount
        board_card["power_bonus"] = board_card.get("power_bonus", 0) + (amount * 1000)

        self.save_state(state)
        return board_card

    def take_damage(self, player: str) -> Optional[str]:
        state = self.load_state()
        player_state = self._get_player_state(state, player)
        leader = player_state["leader"]

        if leader["life"] <= 0:
            raise ValueError(f"{player} has no life left")

        leader["life"] -= 1
        taken_life = None
        if player_state["life_cards"]:
            taken_life = player_state["life_cards"].pop(0)
            player_state["hand"].append(taken_life)
            self._update_hand_count(player_state)

        self.save_state(state)
        return taken_life

    def heal(self, amount: int, player: str, cards: Optional[List[str]] = None) -> int:
        if amount <= 0:
            raise ValueError("amount must be greater than 0")

        state = self.load_state()
        player_state = self._get_player_state(state, player)
        cards = cards or []

        for card_id in cards[:amount]:
            player_state["life_cards"].append(card_id.upper())

        cards_added = min(amount, len(cards))
        player_state["leader"]["life"] += amount

        self.save_state(state)
        return cards_added

    def draw_cards(self, player: str, amount: int = 1) -> List[str]:
        if amount <= 0:
            raise ValueError("amount must be greater than 0")

        state = self.load_state()
        player_state = self._get_player_state(state, player)
        drawn: List[str] = []

        for _ in range(amount):
            if not player_state["deck"]:
                break
            drawn_card = player_state["deck"].pop(0)
            player_state["hand"].append(drawn_card)
            drawn.append(drawn_card)

        self._update_hand_count(player_state)
        self.save_state(state)
        return drawn

    def shuffle_deck(self, player: str) -> None:
        state = self.load_state()
        player_state = self._get_player_state(state, player)
        random.shuffle(player_state["deck"])
        self.save_state(state)

    def trash_from_deck(self, player: str, amount: int = 1) -> List[str]:
        if amount <= 0:
            raise ValueError("amount must be greater than 0")

        state = self.load_state()
        player_state = self._get_player_state(state, player)
        trashed: List[str] = []

        for _ in range(amount):
            if not player_state["deck"]:
                break
            card_id = player_state["deck"].pop(0)
            player_state["trash"].append(card_id)
            trashed.append(card_id)

        self.save_state(state)
        return trashed

    def remove_from_field(
        self, player: str, target: str, destination: str = "trash"
    ) -> Dict[str, Any]:
        state = self.load_state()
        player_state = self._get_player_state(state, player)
        board_card = self._find_board_card(player_state["board"], target)
        player_state["board"].remove(board_card)

        if destination == "trash":
            player_state["trash"].append(board_card["id"])
        elif destination == "hand":
            player_state["hand"].append(board_card["id"])
            self._update_hand_count(player_state)
        elif destination == "deck_top":
            player_state["deck"].insert(0, board_card["id"])
        elif destination == "deck_bottom":
            player_state["deck"].append(board_card["id"])
        else:
            raise ValueError(f"Unsupported destination: {destination}")

        self.save_state(state)
        return board_card

    def activate_effect(
        self, player: str, source: str, effect_text: str, target: Optional[str] = None
    ) -> Dict[str, Any]:
        state = self.load_state()
        state.setdefault("effect_log", [])
        effect_entry = {
            "player": player,
            "source": source,
            "target": target,
            "effect": effect_text,
        }
        state["effect_log"].append(effect_entry)
        self.save_state(state)
        return effect_entry

    def rest_card(self, player: str, target: str) -> Dict[str, Any]:
        state = self.load_state()
        player_state = self._get_player_state(state, player)
        board_card = self._find_board_card(player_state["board"], target)
        board_card["is_rested"] = True
        self.save_state(state)
        return board_card

    def unrest_card(self, player: str, target: str) -> Dict[str, Any]:
        state = self.load_state()
        player_state = self._get_player_state(state, player)
        board_card = self._find_board_card(player_state["board"], target)
        board_card["is_rested"] = False
        self.save_state(state)
        return board_card

    def add_card_to_hand(self, player: str, card_id: str) -> None:
        state = self.load_state()
        player_state = self._get_player_state(state, player)
        player_state["hand"].append(card_id.upper())
        self._update_hand_count(player_state)
        self.save_state(state)


if __name__ == "__main__":
    referee = Referee()
    current_state = referee.load_state()
    print(json.dumps(current_state, indent=2))
