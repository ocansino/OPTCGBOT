from __future__ import annotations

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


class HeuristicPlanningAgent:
    """Deterministic local policy that scores engine-generated legal actions."""

    def __init__(self, max_plan_actions: int = 6) -> None:
        self.max_plan_actions = max_plan_actions
        self.calls = 0
        self.last_scored_actions: List[ScoredAction] = []

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
        score = 20
        reasons = ["+20 develops resources"]
        if card.get("category") == "Character":
            score += 15
            reasons.append("+15 adds a character to board")
        power = card.get("power") or 0
        if power >= 5000:
            score += 5
            reasons.append("+5 relevant printed power")
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
        score = 12 + int(amount or 0)
        reasons = [f"+12 uses available DON", f"+{amount or 0} attaches more pressure"]
        if target.get("instance_id") == player.get("leader", {}).get("instance_id"):
            score += 8
            reasons.append("+8 leader is reliable attacker")
        elif target.get("played_turn") == state.get("turn"):
            score -= 20
            reasons.append("-20 target is summoning sick")
        current_power = self._current_power(state, player, target)
        projected_power = current_power + (int(amount or 0) * 1000)
        if projected_power >= 7000 and current_power < 7000:
            score += 10
            reasons.append("+10 reaches 7000 pressure threshold")
        elif projected_power >= 6000 and current_power < 6000:
            score += 6
            reasons.append("+6 reaches 6000 pressure threshold")
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
            defender_power = self._current_power(state, opponent, opponent.get("leader", {}))
            if attacker_power >= defender_power + 2000:
                score += 8
                reasons.append("+8 attacks above leader by 2000+")
            elif attacker_power <= defender_power:
                score -= 12
                reasons.append("-12 attack is at or below leader power")
                risks.append("likely countered or blocked")
        else:
            target_card = self._find_card(opponent, str(target))
            if target_card is not None:
                target_power = self._current_power(state, opponent, target_card)
                if attacker_power >= target_power:
                    score += 18
                    reasons.append("+18 can KO rested character")
                else:
                    score -= 25
                    reasons.append("-25 cannot KO target character")
                    risks.append("unfavorable character attack")
        return ScoredAction(index, action, score, reasons, risks)

    def score_actions(self, state: Dict[str, Any], legal_actions: List[Dict[str, Any]]) -> List[ScoredAction]:
        scored = [
            self.score_action(state, action, index)
            for index, action in enumerate(legal_actions)
        ]
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
