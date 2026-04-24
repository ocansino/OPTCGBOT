import copy
import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional

from ai.agent import GeminiAgent
from referee import get_legal_actions




PHASES = ("refresh", "draw", "don", "main", "end")


class InvalidActionError(ValueError):
    pass


class GLATEngine:
    def __init__(self, cards_path: str = "cards.json", agent: Optional[Any] = None) -> None:
        self.cards_path = Path(cards_path)
        self.deck_definition = self._load_deck_definition()
        self.catalog = self._build_catalog()
        self.instance_counters: Dict[str, int] = {"P1": 0, "P2": 0}
        self.agent = agent or GeminiAgent()

    def _load_deck_definition(self) -> List[Dict[str, Any]]:
        with self.cards_path.open("r", encoding="utf-8") as file:
            return json.load(file).get("cards", [])

    def _build_catalog(self) -> Dict[str, Dict[str, Any]]:
        return {
            card["id"].upper(): card
            for card in self.deck_definition
            if card.get("id")
        }

    def _leader_life(self, leader_card: Dict[str, Any]) -> int:
        return 4 if len(leader_card.get("colors", [])) > 1 else 5

    def _deck_entries(self) -> tuple[Dict[str, Any], List[str]]:
        leader: Optional[Dict[str, Any]] = None
        deck_ids: List[str] = []

        for card in self.deck_definition:
            card_id = card["id"].upper()
            amount = int(card.get("amount", 0))
            if card.get("category") == "Leader":
                leader = card
                continue
            deck_ids.extend([card_id] * amount)

        if leader is None:
            raise ValueError("cards.json must contain exactly one leader entry")
        if len(deck_ids) != 50:
            raise ValueError(f"Expected a 50-card deck, found {len(deck_ids)} cards")

        return leader, deck_ids

    def build_card_instance(self, player_id: str, card_id: str) -> Dict[str, Any]:
        normalized_id = card_id.upper()
        if normalized_id not in self.catalog:
            raise ValueError(f"Unknown card id: {normalized_id}")

        self.instance_counters[player_id] += 1
        source = self.catalog[normalized_id]
        return {
            "instance_id": f"{player_id}-CARD-{self.instance_counters[player_id]:03d}",
            "card_id": normalized_id,
            "name": source.get("name", normalized_id),
            "category": source.get("category"),
            "cost": source.get("cost") or 0,
            "power": source.get("power") or 0,
            "counter": source.get("counter"),
            "state": "active",
            "played_turn": None,
        }

    def _build_player_state(
        self, player_id: str, leader_card: Dict[str, Any], deck_ids: List[str], rng: random.Random
    ) -> Dict[str, Any]:
        shuffled_ids = list(deck_ids)
        rng.shuffle(shuffled_ids)

        deck = [self.build_card_instance(player_id, card_id) for card_id in shuffled_ids]
        hand = [deck.pop(0) for _ in range(5)]

        leader = {
            "instance_id": f"{player_id}-LEADER",
            "card_id": leader_card["id"].upper(),
            "name": leader_card.get("name", ""),
            "power": leader_card.get("power") or 5000,
            "state": "active",
        }

        return {
            "life": self._leader_life(leader_card),
            "deck": deck,
            "hand": hand,
            "board": [],
            "trash": [],
            "life_cards": [],
            "don_deck": [f"{player_id}-DON-{index:02d}" for index in range(1, 11)],
            "don_area": [],
            "spent_don": [],
            "attached_don": {},
            "leader": leader,
        }

    def create_initial_state(self, seed: int = 7) -> Dict[str, Any]:
        self.instance_counters = {"P1": 0, "P2": 0}
        leader_card, deck_ids = self._deck_entries()
        rng = random.Random(seed)

        state = {
            "turn": 1,
            "active_player": "P1",
            "first_player": "P1",
            "phase": "refresh",
            "winner": None,
            "logs": [],
            "players": {
                "P1": self._build_player_state("P1", leader_card, deck_ids, rng),
                "P2": self._build_player_state("P2", leader_card, deck_ids, rng),
            },
        }
        self.validate_state(state)
        return state

    def save_state(self, state: Dict[str, Any], path: str = "phase1_game_state.json") -> None:
        with Path(path).open("w", encoding="utf-8") as file:
            json.dump(state, file, indent=2)
            file.write("\n")

    def get_active_player(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return state["players"][state["active_player"]]

    def get_inactive_player(self, state: Dict[str, Any]) -> Dict[str, Any]:
        inactive_id = "P2" if state["active_player"] == "P1" else "P1"
        return state["players"][inactive_id]

    def is_first_turn_first_player(self, state: Dict[str, Any]) -> bool:
        return state["turn"] == 1 and state["active_player"] == state["first_player"]

    def draw_card(self, player: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not player["deck"]:
            return None
        card = player["deck"].pop(0)
        player["hand"].append(card)
        return card

    def draw_don(self, player: Dict[str, Any]) -> str:
        if not player["don_deck"]:
            raise ValueError("No DON!! cards left in DON!! deck")
        return player["don_deck"].pop(0)

    def extract_all_attached_don(self, player: Dict[str, Any]) -> List[str]:
        returned: List[str] = []
        for owner_id, count in player["attached_don"].items():
            for index in range(count):
                returned.append(f"RETURNED-{owner_id}-{index + 1}")
        return returned

    def _pay_don(self, player: Dict[str, Any], amount: int) -> List[str]:
        spent = []
        for _ in range(amount):
            token = player["don_area"].pop(0)
            player["spent_don"].append(token)
            spent.append(token)
        return spent

    def _attach_don_tokens(self, player: Dict[str, Any], amount: int) -> List[str]:
        attached = []
        for _ in range(amount):
            attached.append(player["don_area"].pop(0))
        return attached

    def _return_attached_don_on_leave(self, player: Dict[str, Any], instance_id: str) -> None:
        amount = player["attached_don"].pop(instance_id, 0)
        for index in range(amount):
            player["spent_don"].append(f"DETACHED-{instance_id}-{index + 1}")

    def _find_card_by_instance(self, player: Dict[str, Any], instance_id: str) -> Optional[Dict[str, Any]]:
        if player["leader"]["instance_id"] == instance_id:
            return player["leader"]
        return next((card for card in player["board"] if card["instance_id"] == instance_id), None)

    def _zone_cards(self, player: Dict[str, Any], zone: str) -> List[Dict[str, Any]]:
        if zone not in ("deck", "hand", "board", "trash", "life_cards"):
            raise ValueError(f"Unsupported card zone: {zone}")
        return player[zone]

    def _remove_card_from_zone(
        self, player: Dict[str, Any], instance_id: str, zone: str
    ) -> Dict[str, Any]:
        cards = self._zone_cards(player, zone)
        for index, card in enumerate(cards):
            if card["instance_id"] == instance_id:
                if zone == "board":
                    self._return_attached_don_on_leave(player, instance_id)
                return cards.pop(index)
        raise ValueError(f"Card {instance_id} not found in {zone}")

    def _add_card_to_zone(
        self, player: Dict[str, Any], card: Dict[str, Any], zone: str, position: str = "bottom"
    ) -> None:
        cards = self._zone_cards(player, zone)
        if zone == "board":
            card["state"] = "active"
            cards.append(card)
            return
        if position == "top":
            cards.insert(0, card)
        else:
            cards.append(card)

    def _current_power(self, player: Dict[str, Any], card: Dict[str, Any]) -> int:
        attached = player["attached_don"].get(card["instance_id"], 0)
        return (card.get("power") or 0) + (attached * 1000)

    def _has_summoning_sickness(self, state: Dict[str, Any], card: Dict[str, Any]) -> bool:
        if card["instance_id"].endswith("LEADER"):
            return False
        return card.get("played_turn") == state["turn"]

    def _opponent_id(self, player_id: str) -> str:
        return "P2" if player_id == "P1" else "P1"

    def manual_draw(self, state: Dict[str, Any], player_id: str, amount: int = 1) -> List[str]:
        if amount <= 0:
            raise ValueError("amount must be greater than 0")
        player = state["players"][player_id]
        drawn = []
        for _ in range(amount):
            card = self.draw_card(player)
            if card is None:
                break
            drawn.append(card["instance_id"])
        self.log_action(
            state,
            player_id,
            {"type": "manual_draw", "payload": {"amount": amount}},
            {"drawn": drawn},
        )
        self.validate_state(state)
        return drawn

    def manual_trash_top(self, state: Dict[str, Any], player_id: str, amount: int = 1) -> List[str]:
        if amount <= 0:
            raise ValueError("amount must be greater than 0")
        player = state["players"][player_id]
        trashed = []
        for _ in range(amount):
            if not player["deck"]:
                break
            card = player["deck"].pop(0)
            player["trash"].append(card)
            trashed.append(card["instance_id"])
        self.log_action(
            state,
            player_id,
            {"type": "manual_trash_top", "payload": {"amount": amount}},
            {"trashed": trashed},
        )
        self.validate_state(state)
        return trashed

    def manual_discard(self, state: Dict[str, Any], player_id: str, instance_id: str) -> Dict[str, Any]:
        player = state["players"][player_id]
        card = self._remove_card_from_zone(player, instance_id, "hand")
        player["trash"].append(card)
        result = {"discarded": card["instance_id"], "card_id": card["card_id"]}
        self.log_action(
            state,
            player_id,
            {"type": "manual_discard", "payload": {"card_id": instance_id}},
            result,
        )
        self.validate_state(state)
        return result

    def manual_ko(self, state: Dict[str, Any], player_id: str, instance_id: str) -> Dict[str, Any]:
        player = state["players"][player_id]
        card = self._remove_card_from_zone(player, instance_id, "board")
        player["trash"].append(card)
        result = {"ko": card["instance_id"], "card_id": card["card_id"]}
        self.log_action(
            state,
            player_id,
            {"type": "manual_ko", "payload": {"card_id": instance_id}},
            result,
        )
        self.validate_state(state)
        return result

    def manual_move_card(
        self,
        state: Dict[str, Any],
        player_id: str,
        instance_id: str,
        source_zone: str,
        destination_zone: str,
        position: str = "bottom",
    ) -> Dict[str, Any]:
        player = state["players"][player_id]
        card = self._remove_card_from_zone(player, instance_id, source_zone)
        self._add_card_to_zone(player, card, destination_zone, position)
        result = {
            "moved": card["instance_id"],
            "card_id": card["card_id"],
            "from": source_zone,
            "to": destination_zone,
            "position": position,
        }
        self.log_action(
            state,
            player_id,
            {
                "type": "manual_move",
                "payload": {
                    "card_id": instance_id,
                    "from": source_zone,
                    "to": destination_zone,
                    "position": position,
                },
            },
            result,
        )
        self.validate_state(state)
        return result

    def manual_add_life(self, state: Dict[str, Any], player_id: str, amount: int = 1) -> List[str]:
        if amount <= 0:
            raise ValueError("amount must be greater than 0")
        player = state["players"][player_id]
        added = []
        for _ in range(amount):
            if not player["deck"]:
                break
            card = player["deck"].pop(0)
            player["life_cards"].append(card)
            player["life"] += 1
            added.append(card["instance_id"])
        self.log_action(
            state,
            player_id,
            {"type": "manual_add_life", "payload": {"amount": amount}},
            {"added_life": added, "life": player["life"]},
        )
        self.validate_state(state)
        return added

    def manual_reveal_top(self, state: Dict[str, Any], player_id: str, amount: int = 1) -> List[Dict[str, Any]]:
        if amount <= 0:
            raise ValueError("amount must be greater than 0")
        player = state["players"][player_id]
        revealed = copy.deepcopy(player["deck"][:amount])
        self.log_action(
            state,
            player_id,
            {"type": "manual_reveal_top", "payload": {"amount": amount}},
            {"revealed": [card["instance_id"] for card in revealed]},
        )
        self.validate_state(state)
        return revealed

    def validate_state(self, state: Dict[str, Any]) -> None:
        if state["active_player"] not in ("P1", "P2"):
            raise ValueError("active_player must be P1 or P2")
        if state["phase"] not in PHASES:
            raise ValueError(f"Invalid phase: {state['phase']}")

        for player_id in ("P1", "P2"):
            player = state["players"][player_id]
            if player["life"] < 0:
                raise ValueError(f"{player_id} has negative life")
            total_don = (
                len(player["don_deck"])
                + len(player["don_area"])
                + len(player.get("spent_don", []))
                + sum(player["attached_don"].values())
            )
            if total_don != 10:
                raise ValueError(f"{player_id} has invalid DON!! total: {total_don}")

            instance_ids = {player["leader"]["instance_id"]}
            for card in (
                player["board"]
                + player["hand"]
                + player["deck"]
                + player.get("trash", [])
                + player.get("life_cards", [])
            ):
                if card["instance_id"] in instance_ids:
                    raise ValueError(f"Duplicate instance id detected: {card['instance_id']}")
                instance_ids.add(card["instance_id"])

            for attached_to in player["attached_don"]:
                if attached_to not in instance_ids:
                    raise ValueError(f"Attached DON!! references missing card: {attached_to}")

    def log_action(
        self,
        state: Dict[str, Any],
        player_id: str,
        action: Dict[str, Any],
        result: Dict[str, Any],
    ) -> None:
        state["logs"].append(
            {
                "turn": state["turn"],
                "phase": state["phase"],
                "player": player_id,
                "action": copy.deepcopy(action),
                "result": copy.deepcopy(result),
            }
        )

    def refresh_phase(self, state: Dict[str, Any]) -> None:
        state["phase"] = "refresh"
        player = self.get_active_player(state)
        for card in player["board"]:
            card["state"] = "active"
        player["leader"]["state"] = "active"
        player["don_area"].extend(player.get("spent_don", []))
        player["spent_don"] = []
        player["don_area"].extend(self.extract_all_attached_don(player))
        player["attached_don"] = {}
        self.validate_state(state)

    def draw_phase(self, state: Dict[str, Any]) -> None:
        state["phase"] = "draw"
        if self.is_first_turn_first_player(state):
            self.validate_state(state)
            return
        self.draw_card(self.get_active_player(state))
        self.validate_state(state)

    def don_phase(self, state: Dict[str, Any]) -> None:
        state["phase"] = "don"
        player = self.get_active_player(state)
        amount = 1 if self.is_first_turn_first_player(state) else 2

        for _ in range(amount):
            if len(player["don_area"]) + sum(player["attached_don"].values()) >= 10:
                break
            if not player["don_deck"]:
                break
            player["don_area"].append(self.draw_don(player))

        self.validate_state(state)

    def ai_main_phase(self, state: Dict[str, Any]) -> None:
        state["phase"] = "main"
        legal_actions = get_legal_actions(state, self)
        if not legal_actions:
            return

        planned_indices = self.agent.get_turn_plan(state, legal_actions)
        if not isinstance(planned_indices, list) or not planned_indices:
            planned_indices = [len(legal_actions) - 1]

        max_actions_per_turn = 6
        actions_taken = 0

        for raw_index in planned_indices[:max_actions_per_turn]:
            if state["winner"]:
                break

            if not isinstance(raw_index, int) or not (0 <= raw_index < len(legal_actions)):
                break

            action = copy.deepcopy(legal_actions[raw_index])
            if not self.is_valid_action(state, action):
                break

            self.apply_action(state, action)
            actions_taken += 1
            if action["type"] == "end_turn":
                return

        fallback_end_turn = {"type": "end_turn", "payload": {}}
        if self.is_valid_action(state, fallback_end_turn):
            self.apply_action(state, fallback_end_turn)

    def end_phase(self, state: Dict[str, Any]) -> None:
        state["phase"] = "end"
        if state["winner"]:
            self.validate_state(state)
            return

        state["active_player"] = self._opponent_id(state["active_player"])
        state["turn"] += 1
        self.validate_state(state)

    def is_valid_action(self, state: Dict[str, Any], action: Dict[str, Any]) -> bool:
        try:
            self._validate_action(state, action)
            return True
        except InvalidActionError:
            return False

    def _validate_action(self, state: Dict[str, Any], action: Dict[str, Any]) -> None:
        if state["winner"]:
            raise InvalidActionError("Game is already over")
        if state["phase"] != "main":
            raise InvalidActionError("Actions can only be taken during main phase")

        player = self.get_active_player(state)
        opponent = self.get_inactive_player(state)
        action_type = action["type"]
        payload = action.get("payload", {})

        if action_type == "play_card":
            instance_id = payload.get("card_id")
            card = next((item for item in player["hand"] if item["instance_id"] == instance_id), None)
            if card is None:
                raise InvalidActionError("Card must be in hand")
            if card["category"] not in ("Character", "Event"):
                raise InvalidActionError("Only Character and Event cards are playable right now")
            if len(player["don_area"]) < card["cost"]:
                raise InvalidActionError("Not enough DON!! to play card")
            if card["category"] == "Character" and len(player["board"]) >= 5:
                raise InvalidActionError("Board is full")
            return

        if action_type == "attach_don":
            instance_id = payload.get("card_id")
            amount = payload.get("amount", 0)
            if amount <= 0:
                raise InvalidActionError("Attach amount must be greater than 0")
            if amount > len(player["don_area"]):
                raise InvalidActionError("Not enough DON!! in area")
            if self._find_card_by_instance(player, instance_id) is None:
                raise InvalidActionError("Target card must be on board or be the leader")
            return

        if action_type == "attack":
            attacker_id = payload.get("attacker_id")
            attacker = self._find_card_by_instance(player, attacker_id)
            if attacker is None:
                raise InvalidActionError("Attacker not found")
            if attacker["state"] != "active":
                raise InvalidActionError("Attacker must be active")
            if self.is_first_turn_first_player(state):
                raise InvalidActionError("First player cannot attack on turn 1")
            if self._has_summoning_sickness(state, attacker):
                raise InvalidActionError("Characters cannot attack the turn they are played")

            target = payload.get("target")
            if target == "leader":
                return

            defender = self._find_card_by_instance(opponent, target)
            if defender is None:
                raise InvalidActionError("Target character not found")
            if defender["state"] != "rested":
                raise InvalidActionError("Can only attack rested characters")
            return

        if action_type == "end_turn":
            return

        raise InvalidActionError(f"Unsupported action type: {action_type}")

    def apply_action(self, state: Dict[str, Any], action: Dict[str, Any]) -> Dict[str, Any]:
        self._validate_action(state, action)

        player_id = state["active_player"]
        player = self.get_active_player(state)
        opponent = self.get_inactive_player(state)
        payload = action.get("payload", {})
        result: Dict[str, Any]

        if action["type"] == "play_card":
            instance_id = payload["card_id"]
            hand_index = next(index for index, card in enumerate(player["hand"]) if card["instance_id"] == instance_id)
            card = player["hand"].pop(hand_index)
            self._pay_don(player, card["cost"])
            card["played_turn"] = state["turn"]
            card["state"] = "active"
            if card["category"] == "Character":
                player["board"].append(card)
                result = {"played": card["card_id"], "destination": "board", "board_count": len(player["board"])}
            else:
                player["trash"].append(card)
                result = {"played": card["card_id"], "destination": "trash", "effect_resolved": False}

        elif action["type"] == "attach_don":
            instance_id = payload["card_id"]
            amount = payload["amount"]
            self._attach_don_tokens(player, amount)
            player["attached_don"][instance_id] = player["attached_don"].get(instance_id, 0) + amount
            result = {"attached_to": instance_id, "amount": amount}

        elif action["type"] == "attack":
            attacker = self._find_card_by_instance(player, payload["attacker_id"])
            assert attacker is not None
            attacker["state"] = "rested"
            result = self.resolve_attack(state, attacker, payload["target"])

        else:
            result = {"ended_turn": True}

        self.log_action(state, player_id, action, result)
        self.validate_state(state)
        return result

    def resolve_attack(self, state: Dict[str, Any], attacker: Dict[str, Any], target: str) -> Dict[str, Any]:
        player = self.get_active_player(state)
        opponent = self.get_inactive_player(state)
        attacker_power = self._current_power(player, attacker)

        if target == "leader":
            if opponent["life"] > 0:
                opponent["life"] -= 1
                return {
                    "target": "leader",
                    "attacker_power": attacker_power,
                    "life_after": opponent["life"],
                    "won_game": False,
                }

            state["winner"] = state["active_player"]
            return {
                "target": "leader",
                "attacker_power": attacker_power,
                "life_after": 0,
                "won_game": True,
            }

        defender = self._find_card_by_instance(opponent, target)
        if defender is None:
            raise InvalidActionError("Defender not found")

        defender_power = self._current_power(opponent, defender)
        ko = attacker_power >= defender_power
        if ko:
            self._return_attached_don_on_leave(opponent, defender["instance_id"])
            opponent["board"] = [card for card in opponent["board"] if card["instance_id"] != defender["instance_id"]]

        return {
            "target": target,
            "attacker_power": attacker_power,
            "defender_power": defender_power,
            "ko": ko,
        }

    def run_turn(self, state: Dict[str, Any]) -> Dict[str, Any]:
        if state["winner"]:
            return state

        self.refresh_phase(state)
        self.draw_phase(state)
        self.don_phase(state)
        self.ai_main_phase(state)
        self.end_phase(state)
        return state

    def run_game(
        self,
        max_turns: int = 10,
        seed: int = 7,
        state: Optional[Dict[str, Any]] = None,
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        current_state = state or self.create_initial_state(seed=seed)

        turns_completed = 0
        while not current_state["winner"] and turns_completed < max_turns:
            self.run_turn(current_state)
            turns_completed += 1

        if output_path:
            self.save_state(current_state, output_path)

        return current_state


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    engine = GLATEngine()
    state = engine.create_initial_state()

    # Fake legal actions (for testing only)
    legal_actions = [
        {"type": "play_card", "payload": {"card_id": "P1-CARD-001"}},
        {"type": "end_turn", "payload": {}}
    ]

    agent = GeminiAgent()

    print("=== TESTING GEMINI CALL ===")

    action_idx = agent.get_action(state, legal_actions)

    print("Selected action index:", action_idx)
