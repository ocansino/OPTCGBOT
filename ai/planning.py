from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol


class PlanningAgent(Protocol):
    def get_action(self, state: Dict[str, Any], legal_actions: List[Dict[str, Any]]) -> int:
        ...

    def get_turn_plan(self, state: Dict[str, Any], legal_actions: List[Dict[str, Any]]) -> List[int]:
        ...


@dataclass
class ScoredAction:
    index: int
    action: Dict[str, Any]
    score: int
    reasons: List[str] = field(default_factory=list)
    risk_flags: List[str] = field(default_factory=list)
    summary: str = ""
    lookahead_score: int = 0
    rollout_score: int = 0


@dataclass
class ScoredEffectChoice:
    index: int
    card: Dict[str, Any]
    score: int
    reasons: List[str] = field(default_factory=list)
    summary: str = ""


class HeuristicPlanningAgent:
    """Deterministic local policy that scores engine-generated legal actions."""

    def __init__(
        self,
        max_plan_actions: int = 6,
        lookahead_weight: float = 0.6,
        rollout_weight: float = 0.35,
    ) -> None:
        self.max_plan_actions = max_plan_actions
        self.lookahead_weight = lookahead_weight
        self.rollout_weight = rollout_weight
        self.calls = 0
        self.last_scored_actions: List[ScoredAction] = []
        self.last_scored_effect_choices: List[ScoredEffectChoice] = []

    def _player_for_action(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return state["players"][state["active_player"]]

    def _opponent_for_action(self, state: Dict[str, Any]) -> Dict[str, Any]:
        opponent_id = "P2" if state["active_player"] == "P1" else "P1"
        return state["players"][opponent_id]

    def _find_card(self, player: Dict[str, Any], instance_id: str) -> Optional[Dict[str, Any]]:
        leader = player.get("leader", {})
        if leader.get("instance_id") == instance_id:
            return leader
        return next((card for card in player.get("board", []) if card.get("instance_id") == instance_id), None)

    def _current_power(self, state: Dict[str, Any], player: Dict[str, Any], card: Dict[str, Any]) -> int:
        attached = 0
        player_id = next(
            (candidate_id for candidate_id, candidate in state["players"].items() if candidate is player),
            None,
        )
        if state.get("active_player") == player_id:
            attached = player.get("attached_don", {}).get(card.get("instance_id"), 0)
        return (
            (card.get("power") or 0)
            + (attached * 1000)
            + (card.get("battle_power_bonus") or 0)
            + (card.get("manual_power_bonus") or 0)
        )

    def describe_action(self, action: Dict[str, Any]) -> str:
        payload = action.get("payload", {})
        action_type = action.get("type")
        if action_type == "play_card":
            return f"play {payload.get('card_id') or payload.get('card_id_hint') or payload.get('card_id', 'card')}"
        if action_type == "attach_don":
            return f"attach {payload.get('amount', 0)} DON to {payload.get('card_id', 'card')}"
        if action_type == "attack":
            return f"attack {payload.get('attacker_id', 'attacker')} -> {payload.get('target', 'target')}"
        if action_type == "end_turn":
            return "end turn"
        return action_type or "unknown action"

    def describe_card(self, card: Dict[str, Any]) -> str:
        return f"{card.get('card_id', 'card')} {card.get('name', '')}".strip()

    def _printed_card_value(self, card: Dict[str, Any]) -> int:
        cost_value = int(card.get("cost") or 0) * 2
        power_value = int(card.get("power") or 0) // 1000
        counter_penalty = 2 if int(card.get("counter") or 0) >= 2000 else 0
        return cost_value + power_value - counter_penalty

    def _effect_card_value(self, card: Dict[str, Any]) -> int:
        value = max(0, self._printed_card_value(card))
        if card.get("category") == "Character":
            value += 5
        if self._is_blocker_like(card):
            value += 8
        if (card.get("power") or 0) >= 6000:
            value += 4
        if int(card.get("counter") or 0) >= 2000:
            value += 3
        return value

    def _is_leader(self, player: Dict[str, Any], card: Dict[str, Any]) -> bool:
        return card.get("instance_id") == player.get("leader", {}).get("instance_id")

    def _can_attack_now(self, state: Dict[str, Any], player: Dict[str, Any], card: Dict[str, Any]) -> bool:
        if card.get("state") != "active":
            return False
        if self._is_leader(player, card):
            return True
        return card.get("played_turn") != state.get("turn")

    def _ready_attackers(self, state: Dict[str, Any], player: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            card
            for card in [player.get("leader", {}), *player.get("board", [])]
            if self._can_attack_now(state, player, card)
        ]

    def _threshold_bonus(self, current_power: int, projected_power: int) -> int:
        bonus = 0
        for threshold, value in ((6000, 6), (7000, 10), (9000, 8)):
            if current_power < threshold <= projected_power:
                bonus += value
        return bonus

    def _board_value(self, player: Dict[str, Any]) -> int:
        return sum(max(0, self._printed_card_value(card)) for card in player.get("board", []))

    def _life_total(self, player: Dict[str, Any]) -> int:
        return int(player.get("life", len(player.get("life_cards", []))) or 0)

    def _is_blocker_like(self, card: Dict[str, Any]) -> bool:
        return card.get("card_id") in {"PRB02-014", "OP12-021", "OP12-087", "OP12-089", "EB04-058"}

    def _is_banish_like(self, card: Dict[str, Any]) -> bool:
        return card.get("card_id") == "OP09-062"

    def _best_opponent_character_value(self, opponent: Dict[str, Any], max_base_cost: Optional[int] = None) -> int:
        values = []
        for card in opponent.get("board", []):
            if card.get("category") != "Character":
                continue
            base_cost = int(card.get("base_cost", card.get("cost", 0)) or 0)
            if max_base_cost is not None and base_cost > max_base_cost:
                continue
            values.append(self._effect_card_value(card))
        return max(values, default=0)

    def _lowest_own_character_cost_value(self, player: Dict[str, Any]) -> int:
        own_characters = [
            card
            for card in player.get("board", [])
            if card.get("category") == "Character"
        ]
        return min((self._effect_card_value(card) for card in own_characters), default=0)

    def _likely_counter_bonus(self, player: Dict[str, Any]) -> int:
        visible_hand = player.get("hand", [])
        if not visible_hand:
            return 0
        bonuses = []
        for card in visible_hand:
            if card.get("card_id") == "OP12-098":
                bonuses.append(2000)
                continue
            if card.get("card_id") == "OP06-115":
                bonuses.append(3000)
                continue
            counter = int(card.get("counter") or 0)
            if counter <= 0:
                continue
            bonuses.append(counter * 1000 if counter <= 10 else counter)
        bonuses.sort(reverse=True)
        return sum(bonuses[:2])

    def _active_blockers(self, player: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            card
            for card in player.get("board", [])
            if card.get("state") == "active" and self._is_blocker_like(card)
        ]

    def _estimated_next_turn_attack_power(self, state: Dict[str, Any], opponent: Dict[str, Any]) -> int:
        attackers = [opponent.get("leader", {}), *opponent.get("board", [])]
        best_printed = max((card.get("power") or 0 for card in attackers), default=0)
        available_next_turn_don = min(
            10,
            len(opponent.get("don_area", []))
            + len(opponent.get("spent_don", []))
            + sum(opponent.get("attached_don", {}).values())
            + 2,
        )
        return best_printed + min(2, available_next_turn_don) * 1000

    def _known_trigger_risk(self, opponent: Dict[str, Any]) -> int:
        trigger_ids = {"OP12-098", "OP12-112", "OP10-109", "OP06-115", "OP12-097", "OP14-108"}
        visible_life = [
            card
            for card in opponent.get("life_cards", [])
            if card.get("face_up") or card.get("known")
        ]
        if visible_life and visible_life[0].get("card_id") in trigger_ids:
            return 16
        if self._life_total(opponent) <= 2:
            return 5
        return 0

    def _state_eval(self, state: Dict[str, Any]) -> int:
        player = self._player_for_action(state)
        opponent = self._opponent_for_action(state)
        life_score = (self._life_total(player) - self._life_total(opponent)) * 30
        board_score = self._board_value(player) - self._board_value(opponent)
        hand_score = (len(player.get("hand", [])) - len(opponent.get("hand", []))) * 3
        blocker_score = (len(self._active_blockers(player)) - len(self._active_blockers(opponent))) * 5
        ready_score = (
            len(self._ready_attackers(state, player))
            - len(self._ready_attackers(state, opponent))
        ) * 4
        return life_score + board_score + hand_score + blocker_score + ready_score

    def _simulate_action(self, state: Dict[str, Any], action: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        action_type = action.get("type")
        if action_type not in {"attach_don", "play_card", "attack", "end_turn"}:
            return None

        simulated = copy.deepcopy(state)
        player = self._player_for_action(simulated)
        opponent = self._opponent_for_action(simulated)
        payload = action.get("payload", {})
        if action_type == "end_turn":
            return simulated

        if action_type == "attach_don":
            amount = int(payload.get("amount") or 0)
            target_id = payload.get("card_id")
            if amount <= 0 or len(player.get("don_area", [])) < amount or self._find_card(player, target_id) is None:
                return None
            player["don_area"] = player.get("don_area", [])[amount:]
            player.setdefault("spent_don", []).extend(["DON"] * amount)
            attached = player.setdefault("attached_don", {})
            attached[target_id] = attached.get(target_id, 0) + amount
            return simulated

        if action_type == "play_card":
            card_id = payload.get("card_id")
            card = next((candidate for candidate in player.get("hand", []) if candidate.get("instance_id") == card_id), None)
            if card is None:
                return None
            cost = int(card.get("cost") or 0)
            if len(player.get("don_area", [])) < cost:
                return None
            player["hand"] = [candidate for candidate in player.get("hand", []) if candidate.get("instance_id") != card_id]
            player["don_area"] = player.get("don_area", [])[cost:]
            player.setdefault("spent_don", []).extend(["DON"] * cost)
            played = copy.deepcopy(card)
            played["played_turn"] = simulated.get("turn")
            if played.get("category") == "Character":
                player.setdefault("board", []).append(played)
            else:
                player.setdefault("trash", []).append(played)
            return simulated

        attacker = self._find_card(player, payload.get("attacker_id", ""))
        if attacker is None or not self._can_attack_now(simulated, player, attacker):
            return None
        attacker["state"] = "rested"
        attacker_power = self._current_power(simulated, player, attacker)
        target = payload.get("target")
        if target == "leader":
            defender_power = self._current_power(simulated, opponent, opponent.get("leader", {}))
            likely_blocked = bool(self._active_blockers(opponent)) and attacker_power < defender_power + 3000
            likely_countered = attacker_power < defender_power + self._likely_counter_bonus(opponent)
            if not likely_blocked and not likely_countered and opponent.get("life", 0) > 0:
                opponent["life"] -= 1
                if opponent.get("life_cards"):
                    card = opponent["life_cards"].pop(0)
                    opponent.setdefault("hand", []).append(card)
            return simulated

        target_card = self._find_card(opponent, str(target))
        if target_card is None or target_card.get("state") != "rested":
            return None
        if attacker_power >= self._current_power(simulated, opponent, target_card):
            opponent["board"] = [
                card
                for card in opponent.get("board", [])
                if card.get("instance_id") != target_card.get("instance_id")
            ]
            opponent.setdefault("trash", []).append(target_card)
        return simulated

    def _simulate_setup_action(self, state: Dict[str, Any], action: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if action.get("type") not in {"attach_don", "play_card"}:
            return None
        return self._simulate_action(state, action)

    def _roughly_legal_follow_up(self, state: Dict[str, Any], action: Dict[str, Any]) -> bool:
        action_type = action.get("type")
        if action_type == "end_turn":
            return True
        player = self._player_for_action(state)
        opponent = self._opponent_for_action(state)
        payload = action.get("payload", {})
        if action_type == "play_card":
            return any(card.get("instance_id") == payload.get("card_id") for card in player.get("hand", []))
        if action_type == "attach_don":
            amount = int(payload.get("amount") or 0)
            return amount > 0 and len(player.get("don_area", [])) >= amount and self._find_card(player, payload.get("card_id", "")) is not None
        if action_type == "attack":
            attacker = self._find_card(player, payload.get("attacker_id", ""))
            if attacker is None or attacker.get("state") != "active":
                return False
            if attacker.get("instance_id") != player.get("leader", {}).get("instance_id"):
                if attacker.get("played_turn") == state.get("turn"):
                    return False
            target = payload.get("target")
            if target == "leader":
                return True
            target_card = self._find_card(opponent, str(target))
            return target_card is not None and target_card.get("state") == "rested"
        return False

    def _best_follow_up(
        self,
        state: Dict[str, Any],
        legal_actions: List[Dict[str, Any]],
        attacker_id: Optional[str] = None,
    ) -> Optional[ScoredAction]:
        candidates = [
            self.score_action(state, action, index)
            for index, action in enumerate(legal_actions)
            if action.get("type") == "attack"
            and (attacker_id is None or action.get("payload", {}).get("attacker_id") == attacker_id)
            and self._roughly_legal_follow_up(state, action)
        ]
        if not candidates:
            return None
        for item in candidates:
            item.summary = self.describe_action(item.action)
        return max(candidates, key=lambda item: (item.score, -item.index))

    def _apply_lookahead_scores(
        self,
        state: Dict[str, Any],
        legal_actions: List[Dict[str, Any]],
        scored: List[ScoredAction],
    ) -> None:
        for item in scored:
            simulated = self._simulate_setup_action(state, item.action)
            if simulated is None:
                continue
            follow_up_attacker = None
            if item.action.get("type") == "attach_don":
                follow_up_attacker = item.action.get("payload", {}).get("card_id")
            follow_up = self._best_follow_up(simulated, legal_actions, follow_up_attacker)
            if follow_up is None or follow_up.score <= 0:
                continue
            bonus = int(follow_up.score * self.lookahead_weight)
            item.score += bonus
            item.lookahead_score = bonus
            item.reasons.append(f"+{bonus} lookahead: enables {follow_up.summary}")

    def _apply_rollout_scores(
        self,
        state: Dict[str, Any],
        legal_actions: List[Dict[str, Any]],
        scored: List[ScoredAction],
    ) -> None:
        baseline = self._state_eval(state)
        for item in scored:
            if item.action.get("type") == "end_turn":
                continue
            simulated = self._simulate_action(state, item.action)
            if simulated is None:
                continue

            best_follow_up_bonus = 0
            for candidate in legal_actions:
                if candidate == item.action or candidate.get("type") == "end_turn":
                    continue
                if not self._roughly_legal_follow_up(simulated, candidate):
                    continue
                follow_up_state = self._simulate_action(simulated, candidate)
                if follow_up_state is None:
                    continue
                best_follow_up_bonus = max(
                    best_follow_up_bonus,
                    self._state_eval(follow_up_state) - self._state_eval(simulated),
                )

            delta = self._state_eval(simulated) - baseline + int(best_follow_up_bonus * 0.5)
            bonus = int(delta * self.rollout_weight)
            if bonus == 0:
                continue
            item.score += bonus
            item.rollout_score = bonus
            if bonus > 0:
                item.reasons.append(f"+{bonus} rollout: improves projected state")
            else:
                item.reasons.append(f"{bonus} rollout: worsens projected state")

    def _apply_end_turn_scores(self, scored: List[ScoredAction]) -> None:
        end_turn = next((item for item in scored if item.action.get("type") == "end_turn"), None)
        if end_turn is None:
            return
        best_non_end = max(
            (item.score for item in scored if item.action.get("type") != "end_turn"),
            default=-999,
        )
        if best_non_end <= 0:
            end_turn.score = 1
            end_turn.reasons = ["+1 no positive non-end actions remain"]

    def score_action(self, state: Dict[str, Any], action: Dict[str, Any], index: int) -> ScoredAction:
        action_type = action.get("type")
        if action_type == "play_card":
            return self._score_play_card(state, action, index)
        if action_type == "attach_don":
            return self._score_attach_don(state, action, index)
        if action_type == "attack":
            return self._score_attack(state, action, index)
        if action_type == "end_turn":
            return ScoredAction(index, action, -50, ["-50 end turn is lowest priority while useful actions exist"])
        return ScoredAction(index, action, 0, ["0 unknown legal action"])

    def _score_play_card(self, state: Dict[str, Any], action: Dict[str, Any], index: int) -> ScoredAction:
        player = self._player_for_action(state)
        card = next(
            (candidate for candidate in player.get("hand", []) if candidate.get("instance_id") == action["payload"].get("card_id")),
            None,
        )
        if card is None:
            return ScoredAction(index, action, 5, ["+5 legal play"])
        score = 22
        reasons = ["+22 develops resources"]
        opponent = self._opponent_for_action(state)
        player_life = self._life_total(player)
        opponent_life = self._life_total(opponent)
        printed_value = self._printed_card_value(card)
        if printed_value:
            score += printed_value
            reasons.append(f"+{printed_value} printed card value")
        if card.get("category") == "Character":
            score += 15
            reasons.append("+15 adds a character to board")
        power = card.get("power") or 0
        if power >= 5000:
            score += 5
            reasons.append("+5 relevant printed power")
        if self._is_blocker_like(card) and player_life <= opponent_life:
            score += 12
            reasons.append("+12 develops blocker while not ahead on life")
        if card.get("card_id") == "OP07-085":
            target_value = self._best_opponent_character_value(opponent)
            cost_value = self._lowest_own_character_cost_value(player)
            if target_value and cost_value:
                payoff = max(0, target_value - cost_value) + 22
                score += payoff
                reasons.append(f"+{payoff} Stussy can convert low-value body into K.O.")
            elif target_value:
                score -= 14
                reasons.append("-14 Stussy has K.O. target but no character to trash")
        if card.get("card_id") == "EB04-058":
            if player_life <= 2 and player.get("deck"):
                score += 28
                reasons.append("+28 Borsalino stabilizes at low life")
            score += 10
            reasons.append("+10 printed blocker body")
        if card.get("card_id") == "EB03-056":
            target_value = self._best_opponent_character_value(opponent, max_base_cost=3)
            if target_value and player.get("life_cards"):
                payoff = target_value + 14
                score += payoff
                reasons.append(f"+{payoff} Belo Betty can K.O. low-cost target")
            elif target_value:
                score -= 10
                reasons.append("-10 Belo Betty has target but no life to reveal")
        if len(player.get("board", [])) >= 4:
            score -= 10
            reasons.append("-10 board is close to full")
        return ScoredAction(index, action, score, reasons)

    def _score_attach_don(self, state: Dict[str, Any], action: Dict[str, Any], index: int) -> ScoredAction:
        player = self._player_for_action(state)
        target = self._find_card(player, action["payload"].get("card_id", ""))
        amount = action["payload"].get("amount", 0)
        if target is None:
            return ScoredAction(index, action, 0, ["0 unknown attach target"])
        amount = int(amount or 0)
        available_don = len(player.get("don_area", []))
        current_power = self._current_power(state, player, target)
        projected_power = current_power + (amount * 1000)
        ready_attackers = self._ready_attackers(state, player)

        score = 8 + amount
        reasons = [f"+8 uses available DON", f"+{amount} attaches more pressure"]
        if self._is_leader(player, target):
            score += 8
            reasons.append("+8 leader is reliable attacker")
        elif target.get("played_turn") == state.get("turn"):
            score -= 20
            reasons.append("-20 target is summoning sick")
        if not self._can_attack_now(state, player, target):
            score -= 35
            reasons.append("-35 target cannot attack this turn")

        threshold_bonus = self._threshold_bonus(current_power, projected_power)
        if threshold_bonus:
            score += threshold_bonus
            reasons.append(f"+{threshold_bonus} reaches useful attack threshold")
        else:
            score -= 4
            reasons.append("-4 does not reach a new attack threshold")

        unboosted_ready_attackers = [
            attacker
            for attacker in ready_attackers
            if attacker.get("instance_id") != target.get("instance_id")
            and player.get("attached_don", {}).get(attacker.get("instance_id"), 0) == 0
        ]
        if amount == available_don and amount > 1 and unboosted_ready_attackers:
            score -= 8
            reasons.append("-8 spends all DON while another attacker could use pressure")
        if amount >= 3 and projected_power > 9000 and not self._is_leader(player, target):
            score -= 8
            reasons.append("-8 overcommits DON to a non-leader attacker")
        return ScoredAction(index, action, score, reasons)

    def _score_attack(self, state: Dict[str, Any], action: Dict[str, Any], index: int) -> ScoredAction:
        player = self._player_for_action(state)
        opponent = self._opponent_for_action(state)
        payload = action.get("payload", {})
        attacker = self._find_card(player, payload.get("attacker_id", ""))
        if attacker is None:
            return ScoredAction(index, action, 0, ["0 unknown attacker"])

        attacker_power = self._current_power(state, player, attacker)
        target = payload.get("target")
        score = 35
        reasons = ["+35 legal attack applies pressure"]
        risks: List[str] = []
        if attacker.get("instance_id") == player.get("leader", {}).get("instance_id"):
            score += 25
            reasons.append("+25 leader attack has low board risk")
        if target == "leader":
            score += 20
            reasons.append("+20 pressures opponent life")
            has_banish = self._is_banish_like(attacker)
            if has_banish:
                score += 10
                reasons.append("+10 Banish bypasses life triggers")
            opponent_life = self._life_total(opponent)
            if opponent_life <= 2:
                score += 18
                reasons.append("+18 opponent is near lethal range")
            if opponent_life <= 1:
                score += 12
                reasons.append("+12 threatens final life")
            defender_power = self._current_power(state, opponent, opponent.get("leader", {}))
            if attacker_power >= defender_power + 2000:
                score += 8
                reasons.append("+8 attacks above leader by 2000+")
            elif attacker_power <= defender_power:
                score -= 12
                reasons.append("-12 attack is at or below leader power")
                risks.append("likely countered or blocked")
            blocker_count = len(self._active_blockers(opponent))
            if blocker_count:
                score -= 8 + (blocker_count * 4)
                reasons.append(f"-{8 + (blocker_count * 4)} opponent has active blocker pressure")
                risks.append("can be redirected by blocker")
            likely_counter = self._likely_counter_bonus(opponent)
            if likely_counter and attacker_power < defender_power + likely_counter:
                score -= 8
                reasons.append("-8 visible counters can likely cover this attack")
                risks.append("visible counter risk")
            trigger_risk = 0 if has_banish else self._known_trigger_risk(opponent)
            if trigger_risk:
                score -= trigger_risk
                reasons.append(f"-{trigger_risk} trigger risk on life attack")
                risks.append("trigger risk")
        else:
            target_card = self._find_card(opponent, str(target))
            if target_card is not None:
                target_power = self._current_power(state, opponent, target_card)
                if attacker_power >= target_power:
                    target_value = max(0, self._printed_card_value(target_card))
                    score += 18 + target_value
                    reasons.append("+18 can KO rested character")
                    if target_value:
                        reasons.append(f"+{target_value} removes valuable target")
                    board_gap = self._board_value(opponent) - self._board_value(player)
                    if board_gap >= 8:
                        score += 10
                        reasons.append("+10 board control: opponent is ahead on board")
                    if self._is_blocker_like(target_card):
                        score += 14
                        reasons.append("+14 removes blocker-like defender")
                    if target_card.get("state") == "rested":
                        score += 4
                        reasons.append("+4 attacks a card that cannot block this turn")
                    if self._life_total(opponent) <= 1 and not self._is_blocker_like(target_card):
                        score -= 20
                        reasons.append("-20 final life pressure is more valuable than this KO")
                else:
                    score -= 25
                    reasons.append("-25 cannot KO target character")
                    risks.append("unfavorable character attack")
        if (
            not self._is_leader(player, attacker)
            and self._is_blocker_like(attacker)
            and self._life_total(player) <= self._life_total(opponent)
            and score < 85
        ):
            score -= 22
            reasons.append("-22 preserves blocker-like defender while not clearly ahead")
            risks.append("rests defensive piece")
        if not self._is_leader(player, attacker):
            crackback_power = self._estimated_next_turn_attack_power(state, opponent)
            if self._current_power(state, player, attacker) <= crackback_power and score < 95:
                penalty = 10
                if self._printed_card_value(attacker) >= 18:
                    penalty += 8
                score -= penalty
                reasons.append(f"-{penalty} crack-back risk after resting this character")
                risks.append("exposes character to crack-back")
        return ScoredAction(index, action, score, reasons, risks)

    def score_effect_choice(
        self,
        state: Dict[str, Any],
        player_id: str,
        prompt: str,
        card: Dict[str, Any],
        index: int,
        optional: bool = True,
    ) -> ScoredEffectChoice:
        prompt_text = prompt.lower()
        player = state["players"][player_id]
        score = self._effect_card_value(card)
        reasons = [f"+{score} card value"]

        if "trash from your hand" in prompt_text or "discard" in prompt_text:
            keep_value = self._effect_card_value(card)
            score = 100 - keep_value
            reasons = [f"+{score} low discard cost"]
            if int(card.get("counter") or 0) >= 2000:
                score -= 12
                reasons.append("-12 preserves high counter card")
            if self._is_blocker_like(card) and self._life_total(player) <= 2:
                score -= 18
                reasons.append("-18 preserves blocker at low life")
            return ScoredEffectChoice(index, card, score, reasons, self.describe_card(card))

        if "one of your characters to trash" in prompt_text or "your characters to trash" in prompt_text:
            keep_value = self._effect_card_value(card)
            score = 100 - keep_value
            reasons = [f"+{score} low board-cost body"]
            if self._is_blocker_like(card) and self._life_total(player) <= 2:
                score -= 22
                reasons.append("-22 preserves blocker at low life")
            if card.get("state") == "rested":
                score += 6
                reasons.append("+6 rested body is easier to sacrifice")
            return ScoredEffectChoice(index, card, score, reasons, self.describe_card(card))

        if "opponent character to k.o." in prompt_text or "opponent character to ko" in prompt_text:
            if self._is_blocker_like(card):
                score += 12
                reasons.append("+12 removes blocker-like target")
            if card.get("state") == "active":
                score += 5
                reasons.append("+5 removes active future attacker")
            return ScoredEffectChoice(index, card, score, reasons, self.describe_card(card))

        if "play" in prompt_text:
            if card.get("category") == "Character":
                score += 16
                reasons.append("+16 develops effect-play character")
            if len(player.get("board", [])) >= 4:
                score -= 12
                reasons.append("-12 board is close to full")
            if self._is_blocker_like(card) and self._life_total(player) <= 2:
                score += 14
                reasons.append("+14 blocker is valuable at low life")
            return ScoredEffectChoice(index, card, score, reasons, self.describe_card(card))

        if "add to hand" in prompt_text or "search" in prompt_text:
            available_don = len(player.get("don_area", []))
            cost = int(card.get("cost") or 0)
            if cost <= available_don:
                score += 8
                reasons.append("+8 playable with current DON")
            if card.get("category") == "Event" and int(card.get("counter") or 0) <= 0:
                score -= 6
                reasons.append("-6 non-counter event is lower immediate value")
            return ScoredEffectChoice(index, card, score, reasons, self.describe_card(card))

        return ScoredEffectChoice(index, card, score, reasons, self.describe_card(card))

    def score_effect_choices(
        self,
        state: Dict[str, Any],
        player_id: str,
        prompt: str,
        options: List[Dict[str, Any]],
        optional: bool = True,
    ) -> List[ScoredEffectChoice]:
        scored = [
            self.score_effect_choice(state, player_id, prompt, card, index, optional)
            for index, card in enumerate(options)
        ]
        scored.sort(key=lambda item: (-item.score, item.index))
        self.last_scored_effect_choices = scored
        return scored

    def choose_effect_card(
        self,
        state: Dict[str, Any],
        player_id: str,
        prompt: str,
        options: List[Dict[str, Any]],
        optional: bool = True,
    ) -> Optional[str]:
        if not options:
            self.last_scored_effect_choices = []
            return None
        scored = self.score_effect_choices(state, player_id, prompt, options, optional)
        best = scored[0]
        if optional and best.score <= 0:
            return None
        return best.card.get("instance_id")

    def score_actions(self, state: Dict[str, Any], legal_actions: List[Dict[str, Any]]) -> List[ScoredAction]:
        scored = [
            self.score_action(state, action, index)
            for index, action in enumerate(legal_actions)
        ]
        for item in scored:
            item.summary = self.describe_action(item.action)
        self._apply_lookahead_scores(state, legal_actions, scored)
        self._apply_rollout_scores(state, legal_actions, scored)
        self._apply_end_turn_scores(scored)
        scored.sort(key=lambda item: (-item.score, item.index))
        self.last_scored_actions = scored
        return scored

    def get_action(self, state: Dict[str, Any], legal_actions: List[Dict[str, Any]]) -> int:
        self.calls += 1
        if not legal_actions:
            return 0
        scored = self.score_actions(state, legal_actions)
        return scored[0].index

    def get_next_action(self, state: Dict[str, Any], legal_actions: List[Dict[str, Any]]) -> int:
        return self.get_action(state, legal_actions)

    def get_turn_plan(self, state: Dict[str, Any], legal_actions: List[Dict[str, Any]]) -> List[int]:
        self.calls += 1
        if not legal_actions:
            return []
        scored = self.score_actions(state, legal_actions)
        end_index = next(
            (index for index, action in enumerate(legal_actions) if action.get("type") == "end_turn"),
            len(legal_actions) - 1,
        )
        plan = [
            item.index
            for item in scored
            if item.action.get("type") != "end_turn" and item.score > 0
        ][: self.max_plan_actions - 1]
        if end_index not in plan:
            plan.append(end_index)
        return plan
