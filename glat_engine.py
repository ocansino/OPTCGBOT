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
    def __init__(
        self,
        cards_path: str = "cards.json",
        agent: Optional[Any] = None,
        effect_choice_provider: Optional[Any] = None,
        defense_choice_provider: Optional[Any] = None,
        trigger_choice_provider: Optional[Any] = None,
    ) -> None:
        self.cards_path = Path(cards_path)
        self.deck_definition = self._load_deck_definition()
        self.catalog = self._build_catalog()
        self.instance_counters: Dict[str, int] = {"P1": 0, "P2": 0}
        self.agent = agent or GeminiAgent()
        self.effect_choice_provider = effect_choice_provider
        self.defense_choice_provider = defense_choice_provider
        self.trigger_choice_provider = trigger_choice_provider

    def _load_deck_definition(self) -> List[Dict[str, Any]]:
        with self.cards_path.open("r", encoding="utf-8") as file:
            return json.load(file).get("cards", [])

    def _build_catalog(self) -> Dict[str, Dict[str, Any]]:
        return {
            card["id"].upper(): card
            for card in self.deck_definition
            if card.get("id")
        }

    def _card_types(self, card_or_id: Any) -> List[str]:
        if isinstance(card_or_id, str):
            return self.catalog[card_or_id.upper()].get("types", [])
        return self.catalog[card_or_id["card_id"].upper()].get("types", [])

    def _leader_has_type(self, player: Dict[str, Any], type_name: str) -> bool:
        return type_name in self._card_types(player["leader"]["card_id"])

    def _leader_is_multicolored(self, player: Dict[str, Any]) -> bool:
        colors = self.catalog[player["leader"]["card_id"].upper()].get("colors", [])
        return len(colors) > 1

    def _has_blocker(self, player: Dict[str, Any], card: Dict[str, Any]) -> bool:
        card_id = card["card_id"]
        if card_id == "PRB02-014":
            return True
        if card_id == "OP12-089":
            return self._leader_has_type(player, "Revolutionary Army")
        if card_id == "OP12-087":
            return player["leader"]["card_id"] in {"OP12-081"}
        return False

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
        printed_cost = source.get("cost") or 0
        return {
            "instance_id": f"{player_id}-CARD-{self.instance_counters[player_id]:03d}",
            "card_id": normalized_id,
            "name": source.get("name", normalized_id),
            "category": source.get("category"),
            "cost": printed_cost,
            "base_cost": printed_cost,
            "power": source.get("power") or 0,
            "counter": source.get("counter"),
            "state": "active",
            "played_turn": None,
            "battle_power_bonus": 0,
            "temporary_cost_bonus": 0,
            "temporary_cost_bonus_expires": None,
        }

    def _build_player_state(
        self, player_id: str, leader_card: Dict[str, Any], deck_ids: List[str], rng: random.Random
    ) -> Dict[str, Any]:
        shuffled_ids = list(deck_ids)
        rng.shuffle(shuffled_ids)

        deck = [self.build_card_instance(player_id, card_id) for card_id in shuffled_ids]

        leader = {
            "instance_id": f"{player_id}-LEADER",
            "card_id": leader_card["id"].upper(),
            "name": leader_card.get("name", ""),
            "power": leader_card.get("power") or 5000,
            "state": "active",
        }
        life = self._leader_life(leader_card)
        life_cards = [deck.pop(0) for _ in range(life)]
        hand = [deck.pop(0) for _ in range(5)]

        return {
            "life": life,
            "deck": deck,
            "hand": hand,
            "board": [],
            "trash": [],
            "life_cards": life_cards,
            "don_deck": [f"{player_id}-DON-{index:02d}" for index in range(1, 11)],
            "don_area": [],
            "spent_don": [],
            "attached_don": {},
            "leader": leader,
            "turn_flags": {},
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
                if zone == "life_cards":
                    player["life"] -= 1
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
        if zone == "life_cards":
            player["life"] += 1

    def _current_power(self, player: Dict[str, Any], card: Dict[str, Any]) -> int:
        attached = player["attached_don"].get(card["instance_id"], 0)
        return (card.get("power") or 0) + (attached * 1000) + (card.get("battle_power_bonus") or 0)

    def _effective_play_cost(self, player: Dict[str, Any], card: Dict[str, Any]) -> int:
        cost = card.get("base_cost", card.get("cost", 0)) or 0
        if card["card_id"] == "PRB02-014" and len(player.get("trash", [])) >= 15:
            return max(0, cost - 3)
        return cost

    def _effective_character_cost(self, player: Dict[str, Any], card: Dict[str, Any]) -> int:
        cost = card.get("base_cost", card.get("cost", 0)) or 0
        if self._leader_has_type(player, "Revolutionary Army") and card["card_id"] in {"EB03-042", "OP12-089", "OP12-093"}:
            cost += 4
        if player["leader"]["card_id"] in {"OP12-081"} and card["card_id"] == "OP12-087":
            cost += 3
        cost += card.get("temporary_cost_bonus", 0) or 0
        return cost

    def _has_summoning_sickness(self, state: Dict[str, Any], card: Dict[str, Any]) -> bool:
        if card["instance_id"].endswith("LEADER"):
            return False
        return card.get("played_turn") == state["turn"]

    def _opponent_id(self, player_id: str) -> str:
        return "P2" if player_id == "P1" else "P1"

    def _choose_best_card(self, cards: List[Dict[str, Any]]) -> Dict[str, Any]:
        return sorted(
            cards,
            key=lambda card: (card.get("cost", 0), card.get("power", 0), card.get("name", "")),
            reverse=True,
        )[0]

    def _choose_one_effect_card(
        self,
        state: Dict[str, Any],
        player_id: str,
        prompt: str,
        cards: List[Dict[str, Any]],
        strategy: str = "best",
        optional: bool = True,
    ) -> Optional[Dict[str, Any]]:
        if not cards:
            return None

        chosen_id = None
        if self.effect_choice_provider is not None:
            chosen_id = self.effect_choice_provider(
                state=state,
                player_id=player_id,
                prompt=prompt,
                options=copy.deepcopy(cards),
                optional=optional,
            )
        if chosen_id == "__default__":
            chosen_id = "__default__"
        elif chosen_id is not None:
            return next((card for card in cards if card["instance_id"] == chosen_id), None)
        if optional and chosen_id is None and self.effect_choice_provider is not None:
            return None
        if strategy == "lowest":
            return self._choose_lowest_value_cards(cards, 1)[0]
        return self._choose_best_card(cards)

    def _choose_lowest_value_cards(self, cards: List[Dict[str, Any]], amount: int) -> List[Dict[str, Any]]:
        return sorted(
            cards,
            key=lambda card: (
                card.get("cost", 0),
                card.get("power", 0),
                card.get("counter") or 0,
                card.get("name", ""),
            ),
        )[:amount]

    def _counter_bonus_preview(self, player: Dict[str, Any], card: Dict[str, Any]) -> int:
        if card["card_id"] == "OP12-098":
            bonus = 2000
            has_big_revolutionary = any(
                "Revolutionary Army" in self._card_types(board_card)
                and self._effective_character_cost(player, board_card) >= 8
                for board_card in player["board"]
            )
            if has_big_revolutionary:
                bonus += 2000
            return bonus
        if card["card_id"] == "OP06-115":
            remaining_hand = [item for item in player["hand"] if item["instance_id"] != card["instance_id"]]
            return 3000 if remaining_hand else 0
        if card.get("counter"):
            return int(card["counter"]) * 1000
        return 0

    def _available_counter_cards(self, player: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            card for card in player["hand"]
            if self._counter_bonus_preview(player, card) > 0
        ]

    def _discard_from_hand(
        self,
        state: Dict[str, Any],
        player_id: str,
        player: Dict[str, Any],
        amount: int,
        predicate=None,
        prompt: str = "Choose a card to discard",
    ) -> List[Dict[str, Any]]:
        eligible = list(player["hand"])
        if predicate is not None:
            eligible = [card for card in eligible if predicate(card)]
        if not eligible:
            return []

        chosen = []
        remaining_choices = list(eligible)
        for _ in range(min(amount, len(remaining_choices))):
            picked = self._choose_one_effect_card(
                state,
                player_id,
                prompt,
                remaining_choices,
                strategy="lowest",
                optional=False,
            )
            if picked is None:
                break
            chosen.append(picked)
            remaining_choices = [card for card in remaining_choices if card["instance_id"] != picked["instance_id"]]
        chosen_ids = {card["instance_id"] for card in chosen}
        discarded: List[Dict[str, Any]] = []
        remaining = []
        for card in player["hand"]:
            if card["instance_id"] in chosen_ids and len(discarded) < len(chosen):
                discarded.append(card)
                player["trash"].append(card)
            else:
                remaining.append(card)
        player["hand"] = remaining
        return discarded

    def _take_life_to_hand(self, player: Dict[str, Any], amount: int = 1) -> List[Dict[str, Any]]:
        moved = []
        for _ in range(amount):
            if not player["life_cards"]:
                break
            card = player["life_cards"].pop(0)
            player["life"] -= 1
            player["hand"].append(card)
            moved.append(card)
        return moved

    def _trash_life_cards(self, player: Dict[str, Any], amount: int = 1) -> List[Dict[str, Any]]:
        trashed = []
        for _ in range(amount):
            if not player["life_cards"]:
                break
            card = player["life_cards"].pop(0)
            player["life"] -= 1
            player["trash"].append(card)
            trashed.append(card)
        return trashed

    def _add_top_deck_to_life(self, player: Dict[str, Any], amount: int = 1) -> List[Dict[str, Any]]:
        added = []
        for _ in range(amount):
            if not player["deck"]:
                break
            card = player["deck"].pop(0)
            player["life_cards"].insert(0, card)
            player["life"] += 1
            added.append(card)
        return added

    def _search_top_and_add(
        self,
        state: Dict[str, Any],
        player_id: str,
        player: Dict[str, Any],
        amount: int,
        predicate,
        prompt: str,
    ) -> Dict[str, Any]:
        looked_at = player["deck"][:amount]
        if not looked_at:
            return {"looked_at": [], "added": None, "trashed": []}

        eligible = [card for card in looked_at if predicate(card)]
        chosen = self._choose_one_effect_card(
            state,
            player_id,
            prompt,
            eligible,
            strategy="best",
            optional=True,
        )

        looked_up_ids = {card["instance_id"] for card in looked_at}
        player["deck"] = player["deck"][amount:]

        if chosen is not None:
            player["hand"].append(chosen)

        trashed = [card for card in looked_at if chosen is None or card["instance_id"] != chosen["instance_id"]]
        player["trash"].extend(trashed)

        return {
            "looked_at": [card["instance_id"] for card in looked_at],
            "added": chosen["instance_id"] if chosen is not None else None,
            "trashed": [card["instance_id"] for card in trashed],
        }

    def _recycle_trash_to_deck_bottom(
        self,
        player: Dict[str, Any],
        amount: int,
        predicate,
    ) -> List[str]:
        eligible = [card for card in player["trash"] if predicate(card)]
        if len(eligible) < amount:
            return []

        chosen = sorted(
            eligible,
            key=lambda card: (card.get("cost", 0), card.get("name", "")),
            reverse=True,
        )[:amount]
        chosen_ids = {card["instance_id"] for card in chosen}
        player["trash"] = [card for card in player["trash"] if card["instance_id"] not in chosen_ids]
        player["deck"].extend(chosen)
        return [card["instance_id"] for card in chosen]

    def _play_from_trash(
        self,
        state: Dict[str, Any],
        player_id: str,
        player: Dict[str, Any],
        predicate,
        prompt: str = "Choose a card to play from trash",
    ) -> Optional[Dict[str, Any]]:
        eligible = [card for card in player["trash"] if predicate(card)]
        if not eligible or len(player["board"]) >= 5:
            return None

        chosen = self._choose_one_effect_card(
            state,
            player_id,
            prompt,
            eligible,
            strategy="best",
            optional=True,
        )
        if chosen is None:
            return None
        player["trash"] = [card for card in player["trash"] if card["instance_id"] != chosen["instance_id"]]
        chosen["played_turn"] = state["turn"]
        chosen["state"] = "active"
        player["board"].append(chosen)
        return chosen

    def _ko_character(self, state: Dict[str, Any], owner_id: str, instance_id: str) -> Dict[str, Any]:
        player = state["players"][owner_id]
        card = self._remove_card_from_zone(player, instance_id, "board")
        player["trash"].append(card)
        effect_result = self.resolve_card_effect(state, owner_id, card, "on_ko")
        result = {"ko": card["instance_id"], "card_id": card["card_id"]}
        if effect_result is not None:
            result["effect_result"] = effect_result
        return result

    def _expire_end_phase_bonuses(self, state: Dict[str, Any]) -> None:
        active_player_id = state["active_player"]
        current_turn = state["turn"]
        for player in state["players"].values():
            for card in player["board"]:
                expires = card.get("temporary_cost_bonus_expires")
                if not expires:
                    continue
                if expires.get("turn") == current_turn and expires.get("player") == active_player_id:
                    card["temporary_cost_bonus"] = 0
                    card["temporary_cost_bonus_expires"] = None

    def _clear_battle_power_bonuses(self, state: Dict[str, Any]) -> None:
        for player in state["players"].values():
            player["leader"]["battle_power_bonus"] = 0
            for card in player["board"]:
                card["battle_power_bonus"] = 0

    def _choose_blocker_default(
        self,
        defender: Dict[str, Any],
        blocker_options: List[Dict[str, Any]],
        target: str,
        attacker_power: int,
        current_target_card: Dict[str, Any],
    ) -> Optional[str]:
        if not blocker_options:
            return None
        current_target_power = self._current_power(defender, current_target_card)
        if attacker_power < current_target_power:
            return None
        if target != "leader":
            return None
        blocker = self._choose_lowest_value_cards(blocker_options, 1)[0]
        return blocker["instance_id"]

    def _choose_counters_default(
        self,
        defender: Dict[str, Any],
        current_target_card: Dict[str, Any],
        attacker_power: int,
    ) -> List[str]:
        target_power = self._current_power(defender, current_target_card)
        needed = attacker_power - target_power
        if needed < 0:
            return []
        counter_cards = self._available_counter_cards(defender)
        if current_target_card["instance_id"] != defender["leader"]["instance_id"] and self._effective_character_cost(defender, current_target_card) < 6:
            return []
        ordered = sorted(
            counter_cards,
            key=lambda card: (self._counter_bonus_preview(defender, card), card.get("cost", 0), card.get("name", "")),
        )
        chosen_ids = []
        running = 0
        for card in ordered:
            chosen_ids.append(card["instance_id"])
            running += self._counter_bonus_preview(defender, card)
            if running > needed:
                return chosen_ids
        return []

    def _choose_defense_plan(
        self,
        state: Dict[str, Any],
        defender_id: str,
        attacker: Dict[str, Any],
        original_target: str,
        attacker_power: int,
        blocker_options: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        defender = state["players"][defender_id]
        current_target_card = defender["leader"] if original_target == "leader" else self._find_card_by_instance(defender, original_target)
        assert current_target_card is not None

        if self.defense_choice_provider is not None:
            response = self.defense_choice_provider(
                state=state,
                defender_id=defender_id,
                attacker=copy.deepcopy(attacker),
                target=original_target,
                blocker_options=copy.deepcopy(blocker_options),
                counter_options=copy.deepcopy(self._available_counter_cards(defender)),
            )
            if isinstance(response, dict) and response.get("mode") != "default":
                return {
                    "blocker_id": response.get("blocker_id"),
                    "counter_ids": list(response.get("counter_ids", [])),
                }

        blocker_id = self._choose_blocker_default(
            defender,
            blocker_options,
            original_target,
            attacker_power,
            current_target_card,
        )
        if blocker_id is not None:
            current_target_card = self._find_card_by_instance(defender, blocker_id)
            assert current_target_card is not None

        return {
            "blocker_id": blocker_id,
            "counter_ids": self._choose_counters_default(defender, current_target_card, attacker_power),
        }

    def _apply_counter_from_instance(
        self,
        state: Dict[str, Any],
        player_id: str,
        counter_instance_id: str,
        target_instance_id: str,
    ) -> Dict[str, Any]:
        player = state["players"][player_id]
        counter_card = self._remove_card_from_zone(player, counter_instance_id, "hand")
        player["trash"].append(counter_card)

        target = self._find_card_by_instance(player, target_instance_id)
        if target is None:
            raise ValueError("Counter target must be your leader or a card on your board")

        bonus = self._counter_bonus_preview(player, counter_card)
        extra_cost_paid = []
        if counter_card["card_id"] == "OP06-115":
            discarded = self._discard_from_hand(
                state,
                player_id,
                player,
                1,
                prompt="Choose a card to trash for You're the One Who Should Disappear.",
            )
            if not discarded:
                raise ValueError("This counter requires trashing 1 card from your hand")
            extra_cost_paid = [item["instance_id"] for item in discarded]
        elif bonus <= 0:
            raise ValueError(f"Card {counter_card['card_id']} does not have supported counter text")

        target["battle_power_bonus"] = (target.get("battle_power_bonus") or 0) + bonus
        result = {
            "used_counter": counter_card["instance_id"],
            "card_id": counter_card["card_id"],
            "target": target_instance_id,
            "power_bonus": bonus,
        }
        if extra_cost_paid:
            result["extra_cost_paid"] = extra_cost_paid
        return result

    def _choose_trigger_default(self, player: Dict[str, Any], card: Dict[str, Any]) -> bool:
        if card["card_id"] == "OP12-112":
            return self._leader_is_multicolored(player)
        if card["card_id"] == "OP12-098":
            return True
        if card["card_id"] == "OP06-115":
            return player["life"] == 0
        return False

    def _should_activate_trigger(self, state: Dict[str, Any], player_id: str, card: Dict[str, Any]) -> bool:
        player = state["players"][player_id]
        if self.trigger_choice_provider is not None:
            choice = self.trigger_choice_provider(
                state=state,
                player_id=player_id,
                card=copy.deepcopy(card),
            )
            if isinstance(choice, bool):
                return choice
        return self._choose_trigger_default(player, card)

    def _resolve_trigger_card(
        self,
        state: Dict[str, Any],
        player_id: str,
        trigger_card: Dict[str, Any],
    ) -> Dict[str, Any]:
        player = state["players"][player_id]
        player["trash"].append(trigger_card)
        result: Dict[str, Any] = {
            "triggered": trigger_card["instance_id"],
            "card_id": trigger_card["card_id"],
        }

        if trigger_card["card_id"] == "OP12-098":
            drawn = self.draw_card(player)
            trashed = []
            if player["deck"]:
                trashed_card = player["deck"].pop(0)
                player["trash"].append(trashed_card)
                trashed.append(trashed_card["instance_id"])
            result["effect_result"] = {
                "effect": "OP12-098",
                "drawn": drawn["instance_id"] if drawn is not None else None,
                "trashed": trashed,
            }
            return result

        if trigger_card["card_id"] == "OP06-115":
            added = []
            if player["life"] == 0:
                added = [card["instance_id"] for card in self._add_top_deck_to_life(player, 1)]
            discarded = self._discard_from_hand(
                state,
                player_id,
                player,
                1,
                prompt="Choose a card to trash for You're the One Who Should Disappear. trigger",
            )
            result["effect_result"] = {
                "effect": "OP06-115",
                "added_to_life": added,
                "discarded": [item["instance_id"] for item in discarded],
            }
            return result

        if trigger_card["card_id"] == "OP12-112":
            drawn = []
            if self._leader_is_multicolored(player):
                for _ in range(2):
                    card = self.draw_card(player)
                    if card is None:
                        break
                    drawn.append(card["instance_id"])
            result["effect_result"] = {
                "effect": "OP12-112",
                "drawn": drawn if self._leader_is_multicolored(player) else [],
            }
            if not self._leader_is_multicolored(player):
                result["effect_result"]["skipped"] = "leader_not_multicolored"
            return result

        result["effect_result"] = {
            "effect": trigger_card["card_id"],
            "skipped": "unsupported_trigger",
        }
        return result

    def _resolve_opponent_leader_reaction_to_play(
        self,
        state: Dict[str, Any],
        played_by: str,
        card: Dict[str, Any],
        source: str = "normal",
    ) -> Optional[Dict[str, Any]]:
        reaction_player_id = self._opponent_id(played_by)
        reaction_player = state["players"][reaction_player_id]
        if reaction_player["leader"]["card_id"] != "OP12-081":
            return None
        if reaction_player["turn_flags"].get("koala_once_per_turn_used"):
            return {"effect": "OP12-081", "skipped": "already_used_this_turn"}
        if card.get("category") != "Character":
            return None

        is_big_character = (card.get("base_cost", card.get("cost", 0)) or 0) >= 8
        if not is_big_character and source != "effect":
            return None

        played_player = state["players"][played_by]
        moved = self._take_life_to_hand(played_player, 1)
        if not moved:
            return {"effect": "OP12-081", "skipped": "no_life_cards"}

        reaction_player["turn_flags"]["koala_once_per_turn_used"] = True
        return {
            "effect": "OP12-081",
            "life_to_hand": [item["instance_id"] for item in moved],
            "target_player": played_by,
        }

    def resolve_card_effect(
        self,
        state: Dict[str, Any],
        player_id: str,
        card: Dict[str, Any],
        hook: str,
    ) -> Optional[Dict[str, Any]]:
        card_id = card["card_id"].upper()
        player = state["players"][player_id]
        opponent_id = self._opponent_id(player_id)
        opponent = state["players"][opponent_id]

        if hook == "on_ko":
            if card_id == "OP12-089":
                if not self._leader_has_type(player, "Revolutionary Army"):
                    return {"effect": "OP12-089", "skipped": "leader_missing_type"}
                eligible = [
                    target for target in opponent["board"]
                    if (target.get("base_cost", target.get("cost", 0)) or 0) <= 4
                ]
                if not eligible:
                    return {"effect": "OP12-089", "skipped": "no_valid_target"}
                chosen = self._choose_one_effect_card(
                    state,
                    player_id,
                    "Choose an opponent character to K.O. with Hack",
                    eligible,
                    strategy="best",
                    optional=True,
                )
                if chosen is None:
                    return {"effect": "OP12-089", "skipped": "player_declined_choice"}
                ko_result = self._ko_character(state, opponent_id, chosen["instance_id"])
                return {
                    "effect": "OP12-089",
                    "ko_target": chosen["instance_id"],
                    "ko_result": ko_result,
                }

            if card_id == "OP10-109":
                trashed = self._trash_life_cards(opponent, 1)
                if not trashed:
                    return {"effect": "OP10-109", "skipped": "no_life_cards"}
                return {
                    "effect": "OP10-109",
                    "trashed_life": [item["instance_id"] for item in trashed],
                }

            if card_id == "EB03-042":
                eligible_hand = [
                    candidate
                    for candidate in player["hand"]
                    if (
                        candidate.get("category") == "Character"
                        and (candidate.get("cost") or 0) <= 6
                        and (
                            (
                                "Revolutionary Army" in self._card_types(candidate)
                                and candidate["name"] != "Koala"
                            )
                            or candidate["name"] == "Nico Robin"
                        )
                    )
                ]
                eligible_trash = [
                    candidate
                    for candidate in player["trash"]
                    if (
                        candidate["instance_id"] != card["instance_id"]
                        and candidate.get("category") == "Character"
                        and (candidate.get("cost") or 0) <= 6
                        and (
                            (
                                "Revolutionary Army" in self._card_types(candidate)
                                and candidate["name"] != "Koala"
                            )
                            or candidate["name"] == "Nico Robin"
                        )
                    )
                ]
                if not eligible_hand and not eligible_trash:
                    return {"effect": "EB03-042", "skipped": "no_valid_target"}

                pool = eligible_hand + eligible_trash
                chosen = self._choose_one_effect_card(
                    state,
                    player_id,
                    "Choose a character to play with Koala",
                    pool,
                    strategy="best",
                    optional=True,
                )
                if chosen is None:
                    return {"effect": "EB03-042", "skipped": "player_declined_choice"}
                source_zone = "hand" if any(item["instance_id"] == chosen["instance_id"] for item in eligible_hand) else "trash"
                if source_zone == "hand":
                    player["hand"] = [item for item in player["hand"] if item["instance_id"] != chosen["instance_id"]]
                else:
                    player["trash"] = [item for item in player["trash"] if item["instance_id"] != chosen["instance_id"]]
                chosen["played_turn"] = state["turn"]
                chosen["state"] = "active"
                player["board"].append(chosen)
                leader_reaction = self._resolve_opponent_leader_reaction_to_play(
                    state,
                    player_id,
                    chosen,
                    source="effect",
                )
                result = {
                    "effect": "EB03-042",
                    "played_from": source_zone,
                    "played": chosen["instance_id"],
                }
                if leader_reaction is not None:
                    result["leader_reaction"] = leader_reaction
                return result

            return None

        if hook != "on_play":
            return None

        if card_id == "OP12-097":
            result = self._search_top_and_add(
                state,
                player_id,
                player,
                3,
                lambda candidate: (
                    "Revolutionary Army" in self._card_types(candidate)
                    and candidate["card_id"] != "OP12-097"
                ),
                "Choose a card to add to hand from Captains Assembled",
            )
            result["effect"] = "OP12-097"
            return result

        if card_id == "OP12-086":
            if not self._leader_has_type(player, "Revolutionary Army"):
                return {"effect": "OP12-086", "skipped": "leader_missing_type"}
            result = self._search_top_and_add(
                state,
                player_id,
                player,
                3,
                lambda candidate: (
                    (
                        "Revolutionary Army" in self._card_types(candidate)
                        and candidate["name"] != "Koala"
                    )
                    or candidate["name"] == "Nico Robin"
                ),
                "Choose a card to add to hand from Koala",
            )
            result["effect"] = "OP12-086"
            return result

        if card_id == "OP12-094":
            if not self._leader_has_type(player, "Revolutionary Army"):
                return {"effect": "OP12-094", "skipped": "leader_missing_type"}

            recycled = self._recycle_trash_to_deck_bottom(
                player,
                3,
                lambda candidate: "Revolutionary Army" in self._card_types(candidate),
            )
            if len(recycled) < 3:
                return {"effect": "OP12-094", "skipped": "not_enough_revolutionary_cards_in_trash"}

            played = self._play_from_trash(
                state,
                player_id,
                player,
                lambda candidate: candidate.get("category") == "Character" and (candidate.get("cost") or 0) <= 6,
                "Choose a character to play from trash with Monkey.D.Dragon",
            )
            return {
                "effect": "OP12-094",
                "recycled": recycled,
                "played_from_trash": played["instance_id"] if played is not None else None,
            }

        if card_id == "OP12-087":
            if len(opponent["hand"]) < 5:
                return {"effect": "OP12-087", "skipped": "opponent_hand_below_five"}
            discarded = self._discard_from_hand(
                state,
                player_id,
                player,
                1,
                prompt="Choose a card to trash from your hand for Nico Robin",
            )
            if not discarded:
                return {"effect": "OP12-087", "skipped": "no_card_to_trash_for_cost"}
            opponent_discarded = self._discard_from_hand(
                state,
                opponent_id,
                opponent,
                2,
                prompt="Choose cards to trash from hand for Nico Robin",
            )
            return {
                "effect": "OP12-087",
                "discarded_for_cost": [item["instance_id"] for item in discarded],
                "opponent_discarded": [item["instance_id"] for item in opponent_discarded],
            }

        if card_id == "OP12-119":
            discarded = self._discard_from_hand(
                state,
                player_id,
                player,
                1,
                prompt="Choose a card to trash from your hand for Bartholomew Kuma",
            )
            if not discarded:
                return {"effect": "OP12-119", "skipped": "no_card_to_trash_for_cost"}
            added = self._add_top_deck_to_life(player, 1)
            if not added:
                return {"effect": "OP12-119", "skipped": "deck_empty"}
            card["temporary_cost_bonus"] = 2
            card["temporary_cost_bonus_expires"] = {
                "turn": state["turn"] + 1,
                "player": self._opponent_id(player_id),
            }
            return {
                "effect": "OP12-119",
                "discarded_for_cost": [item["instance_id"] for item in discarded],
                "added_to_life": [item["instance_id"] for item in added],
                "temporary_cost_bonus": 2,
            }

        if card_id == "OP14-108":
            if not self._leader_is_multicolored(player):
                return {"effect": "OP14-108", "skipped": "leader_not_multicolored"}
            if opponent["life"] > 3:
                return {"effect": "OP14-108", "skipped": "opponent_life_above_three"}
            eligible = [target for target in opponent["board"] if (target.get("power") or 0) <= 7000]
            if not eligible:
                return {"effect": "OP14-108", "skipped": "no_valid_target"}
            chosen = self._choose_one_effect_card(
                state,
                player_id,
                "Choose an opponent character to K.O. with Silvers Rayleigh",
                eligible,
                strategy="best",
                optional=True,
            )
            if chosen is None:
                return {"effect": "OP14-108", "skipped": "player_declined_choice"}
            ko_result = self._ko_character(state, opponent_id, chosen["instance_id"])
            return {
                "effect": "OP14-108",
                "ko_target": chosen["instance_id"],
                "ko_result": ko_result,
            }

        if card_id == "EB03-053":
            attached = None
            if player["spent_don"]:
                player["spent_don"].pop(0)
                player["attached_don"][player["leader"]["instance_id"]] = (
                    player["attached_don"].get(player["leader"]["instance_id"], 0) + 1
                )
                attached = player["leader"]["instance_id"]
            life_to_hand = []
            if opponent["life"] >= 3:
                life_to_hand = self._take_life_to_hand(opponent, 1)
            return {
                "effect": "EB03-053",
                "attached_to": attached,
                "opponent_life_to_hand": [item["instance_id"] for item in life_to_hand],
            }

        return None

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
        result = self._ko_character(state, player_id, instance_id)
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
            self._add_card_to_zone(player, card, "life_cards")
            added.append(card["instance_id"])
        self.log_action(
            state,
            player_id,
            {"type": "manual_add_life", "payload": {"amount": amount}},
            {"added_life": added, "life": player["life"]},
        )
        self.validate_state(state)
        return added

    def manual_use_counter(
        self,
        state: Dict[str, Any],
        player_id: str,
        counter_instance_id: str,
        target_instance_id: str,
    ) -> Dict[str, Any]:
        result = self._apply_counter_from_instance(state, player_id, counter_instance_id, target_instance_id)

        self.log_action(
            state,
            player_id,
            {
                "type": "manual_use_counter",
                "payload": {"card_id": counter_instance_id, "target": target_instance_id},
            },
            result,
        )
        self.validate_state(state)
        return result

    def manual_activate_trigger(
        self,
        state: Dict[str, Any],
        player_id: str,
        trigger_instance_id: str,
    ) -> Dict[str, Any]:
        player = state["players"][player_id]
        trigger_card = self._remove_card_from_zone(player, trigger_instance_id, "life_cards")
        result = self._resolve_trigger_card(state, player_id, trigger_card)

        self.log_action(
            state,
            player_id,
            {"type": "manual_activate_trigger", "payload": {"card_id": trigger_instance_id}},
            result,
        )
        self.validate_state(state)
        return result

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
            if player["life"] != len(player.get("life_cards", [])):
                raise ValueError(
                    f"{player_id} has invalid life tracking: {player['life']} != {len(player.get('life_cards', []))}"
                )
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
        player["turn_flags"] = {}
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
        self._expire_end_phase_bonuses(state)
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
            if len(player["don_area"]) < self._effective_play_cost(player, card):
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
            paid_cost = self._effective_play_cost(player, card)
            self._pay_don(player, paid_cost)
            card["played_turn"] = state["turn"]
            card["state"] = "active"
            if card["category"] == "Character":
                player["board"].append(card)
                result = {
                    "played": card["card_id"],
                    "destination": "board",
                    "board_count": len(player["board"]),
                    "paid_cost": paid_cost,
                }
            else:
                player["trash"].append(card)
                result = {
                    "played": card["card_id"],
                    "destination": "trash",
                    "paid_cost": paid_cost,
                    "effect_resolved": False,
                }

            effect_result = self.resolve_card_effect(state, player_id, card, "on_play")
            if effect_result is not None:
                result["effect_result"] = effect_result
                result["effect_resolved"] = True
            leader_reaction = self._resolve_opponent_leader_reaction_to_play(state, player_id, card)
            if leader_reaction is not None:
                result["leader_reaction"] = leader_reaction

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
        defender_id = self._opponent_id(state["active_player"])
        blocker_options = [
            card for card in opponent["board"]
            if card["state"] == "active" and self._has_blocker(opponent, card)
        ]
        defense_plan = self._choose_defense_plan(
            state,
            defender_id,
            attacker,
            target,
            attacker_power,
            blocker_options,
        )
        current_target = target
        defense_result: Dict[str, Any] = {"blocker_id": None, "counters": []}

        blocker_id = defense_plan.get("blocker_id")
        if blocker_id is not None:
            blocker = self._find_card_by_instance(opponent, blocker_id)
            if blocker is not None and blocker["state"] == "active" and self._has_blocker(opponent, blocker):
                blocker["state"] = "rested"
                current_target = blocker_id
                defense_result["blocker_id"] = blocker_id

        target_card = opponent["leader"] if current_target == "leader" else self._find_card_by_instance(opponent, current_target)
        assert target_card is not None
        for counter_id in defense_plan.get("counter_ids", []):
            if attacker_power < self._current_power(opponent, target_card):
                break
            if any(card["instance_id"] == counter_id for card in opponent["hand"]):
                counter_result = self._apply_counter_from_instance(state, defender_id, counter_id, target_card["instance_id"])
                defense_result["counters"].append(counter_result)

        if current_target == "leader":
            leader_effect = None
            if attacker["instance_id"] == player["leader"]["instance_id"] and player["leader"]["card_id"] == "OP12-081":
                eight_cost_characters = [
                    card for card in player["board"]
                    if self._effective_character_cost(player, card) >= 8
                ]
                if len(eight_cost_characters) >= 2:
                    drawn = self.draw_card(player)
                    leader_effect = {
                        "effect": "OP12-081",
                        "drawn": drawn["instance_id"] if drawn is not None else None,
                    }
            defender_power = self._current_power(opponent, opponent["leader"])
            if attacker_power < defender_power:
                result = {
                    "target": "leader",
                    "final_target": current_target,
                    "attacker_power": attacker_power,
                    "defender_power": defender_power,
                    "blocked_or_countered": True,
                    "won_game": False,
                    "defense": defense_result,
                }
                if leader_effect is not None:
                    result["leader_effect"] = leader_effect
                self._clear_battle_power_bonuses(state)
                return result
            if opponent["life"] > 0:
                revealed = self._remove_card_from_zone(opponent, opponent["life_cards"][0]["instance_id"], "life_cards")
                trigger_result = None
                if self._should_activate_trigger(state, defender_id, revealed):
                    trigger_result = self._resolve_trigger_card(state, defender_id, revealed)
                else:
                    opponent["hand"].append(revealed)
                result = {
                    "target": "leader",
                    "final_target": current_target,
                    "attacker_power": attacker_power,
                    "life_after": opponent["life"],
                    "won_game": False,
                    "life_to_hand": [] if trigger_result is not None else [revealed["instance_id"]],
                    "trigger_result": trigger_result,
                    "defender_power": defender_power,
                    "defense": defense_result,
                }
                if leader_effect is not None:
                    result["leader_effect"] = leader_effect
                self._clear_battle_power_bonuses(state)
                return result

            state["winner"] = state["active_player"]
            result = {
                "target": "leader",
                "final_target": current_target,
                "attacker_power": attacker_power,
                "defender_power": self._current_power(opponent, opponent["leader"]),
                "life_after": 0,
                "won_game": True,
                "defense": defense_result,
            }
            if leader_effect is not None:
                result["leader_effect"] = leader_effect
            self._clear_battle_power_bonuses(state)
            return result

        defender = self._find_card_by_instance(opponent, current_target)
        if defender is None:
            raise InvalidActionError("Defender not found")

        defender_power = self._current_power(opponent, defender)
        ko = attacker_power >= defender_power
        ko_result = None
        if ko:
            ko_result = self._ko_character(state, defender_id, defender["instance_id"])

        result = {
            "target": target,
            "final_target": current_target,
            "attacker_power": attacker_power,
            "defender_power": defender_power,
            "ko": ko,
            "defense": defense_result,
        }
        if ko_result is not None:
            result["ko_result"] = ko_result
        self._clear_battle_power_bonuses(state)
        return result

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
