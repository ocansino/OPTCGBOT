import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from cli_intake import (
    begin_opponent_intake_session as intake_begin_opponent_intake_session,
    format_battle_context_lines as intake_format_battle_context_lines,
    finish_opponent_intake_session as intake_finish_opponent_intake_session,
    get_active_opponent_intake as intake_get_active_opponent_intake,
    get_last_battle_context as intake_get_last_battle_context,
    handle_shorthand_report as intake_handle_shorthand_report,
    log_battle_context_event as intake_log_battle_context_event,
    log_opponent_intake_event as intake_log_opponent_intake_event,
    print_opponent_intake_log as intake_print_opponent_intake_log,
    run_logged_human_action as intake_run_logged_human_action,
)
from glat_engine import GLATEngine, InvalidActionError
from referee import get_legal_actions


AI_PLAYER = "P1"
HUMAN_PLAYER = "P2"


class FakePlanningAgent:
    """Deterministic local agent for CLI smoke tests without API calls."""

    def __init__(self) -> None:
        self.calls = 0

    def get_turn_plan(self, state: Dict[str, Any], legal_actions: List[Dict[str, Any]]) -> List[int]:
        self.calls += 1
        non_end = [
            index for index, action in enumerate(legal_actions) if action["type"] != "end_turn"
        ]
        if not non_end:
            return [len(legal_actions) - 1]
        return non_end[:2] + [len(legal_actions) - 1]


def card_label(card: Dict[str, Any]) -> str:
    details = [
        card.get("instance_id", ""),
        card.get("card_id", ""),
        card.get("name", ""),
        f"cost {card.get('cost', 0)}",
        f"power {card.get('power', 0)}",
        card.get("state", "active"),
    ]
    return " | ".join(str(item) for item in details if item != "")


def action_label(action: Dict[str, Any]) -> str:
    action_type = action["type"]
    payload = action.get("payload", {})
    if action_type == "play_card":
        return f"Play {payload['card_id']}"
    if action_type == "attach_don":
        return f"Attach {payload['amount']} DON to {payload['card_id']}"
    if action_type == "attack":
        return f"Attack with {payload['attacker_id']} -> {payload['target']}"
    return "End turn"


def save_state(engine: GLATEngine, state: Dict[str, Any], path: str) -> None:
    engine.save_state(state, path)


def load_state(engine: GLATEngine, path: str) -> Dict[str, Any]:
    return engine.load_state(path)


def cli_effect_choice(
    state: Dict[str, Any],
    player_id: str,
    prompt: str,
    options: List[Dict[str, Any]],
    optional: bool,
) -> Optional[str]:
    if player_id != HUMAN_PLAYER:
        return "__default__"

    labels = [card_label(card) for card in options]
    print(f"\n{prompt}")
    if optional:
        print("Choose a card, or 0 to skip.")
    selection = choose_from_menu(prompt, labels, allow_back=optional)
    if selection is None:
        return None
    return options[selection]["instance_id"]


def cli_defense_choice(
    state: Dict[str, Any],
    defender_id: str,
    attacker: Dict[str, Any],
    target: str,
    blocker_options: List[Dict[str, Any]],
    counter_options: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if defender_id != HUMAN_PLAYER:
        return {"mode": "default"}

    print("\nDefense window")
    print(f"Attacker: {card_label(attacker)}")
    if target == "leader":
        print("Target: leader")
    else:
        defender = state["players"][defender_id]
        target_card = next((card for card in defender["board"] if card["instance_id"] == target), None)
        print(f"Target: {card_label(target_card) if target_card else target}")

    blocker_id = None
    if blocker_options:
        print("Choose a blocker if you want to redirect the attack.")
        blocker_index = choose_from_menu(
            "Available blockers",
            [card_label(card) for card in blocker_options],
            allow_back=True,
        )
        if blocker_index is not None:
            blocker_id = blocker_options[blocker_index]["instance_id"]

    chosen_counter_ids: List[str] = []
    while True:
        defender = state["players"][defender_id]
        live_counter_options = [
            card for card in defender["hand"]
            if card.get("counter") or card["card_id"] in {"OP12-098", "OP06-115"}
        ]
        if not live_counter_options:
            break
        counter_index = choose_from_menu(
            "Use a counter card? Choose one or go back to stop.",
            [card_label(card) for card in live_counter_options],
            allow_back=True,
        )
        if counter_index is None:
            break
        chosen_counter_ids.append(live_counter_options[counter_index]["instance_id"])
        print(f"Queued counter: {card_label(live_counter_options[counter_index])}")

    return {"blocker_id": blocker_id, "counter_ids": chosen_counter_ids}


def cli_trigger_choice(
    state: Dict[str, Any],
    player_id: str,
    card: Dict[str, Any],
) -> Optional[bool]:
    if player_id != HUMAN_PLAYER:
        return None
    print("\nTrigger window")
    print(f"Revealed life card: {card_label(card)}")
    choice = choose_from_menu("Activate this trigger?", ["Yes", "No"], allow_back=False)
    return choice == 0


def print_summary(state: Dict[str, Any]) -> None:
    print("\n" + "=" * 72)
    print(
        f"Turn {state['turn']} | Active: {state['active_player']} | "
        f"Phase: {state['phase']} | Winner: {state['winner'] or '-'}"
    )
    for player_id in ("P1", "P2"):
        player = state["players"][player_id]
        label = "AI" if player_id == AI_PLAYER else "Human"
        attached = sum(player["attached_don"].values())
        print(
            f"{player_id} ({label}) | Life {player['life']} | "
            f"Hand {len(player['hand'])} | Board {len(player['board'])} | "
            f"Deck {len(player['deck'])} | Trash {len(player.get('trash', []))} | "
            f"DON area {len(player['don_area'])} | "
            f"Attached DON {attached}"
        )
    human_hand = state["players"][HUMAN_PLAYER]["hand"]
    if human_hand:
        print("Your hand:")
        for index, card in enumerate(human_hand):
            print(f"  {index}: {card_label(card)}")
    else:
        print("Your hand: empty")
    print("=" * 72)


def print_zone(state: Dict[str, Any], player_id: str, zone: str) -> None:
    player = state["players"][player_id]
    if zone == "leader":
        print(card_label(player["leader"]))
        return

    cards = player.get(zone, [])
    if not cards:
        print(f"{player_id} {zone}: empty")
        return

    print(f"{player_id} {zone}:")
    for index, card in enumerate(cards):
        print(f"  {index}: {card_label(card)}")


def print_don_summary(state: Dict[str, Any], player_id: str) -> None:
    player = state["players"][player_id]
    print(
        f"{player_id} DON | deck {len(player['don_deck'])} | area {len(player['don_area'])} | "
        f"spent {len(player.get('spent_don', []))} | attached {sum(player['attached_don'].values())}"
    )
    if player["attached_don"]:
        print(f"  attached map: {player['attached_don']}")


def print_recent_logs(state: Dict[str, Any], count: int = 8) -> None:
    logs = state.get("logs", [])[-count:]
    if not logs:
        print("No actions logged yet.")
        return
    for log in logs:
        print(
            f"Turn {log['turn']} {log['player']} "
            f"{log['action']['type']}: {log['result']}"
        )


def print_help() -> None:
    print(
        """
Commands:
  help                         Show this help
  state                        Show compact game summary
  hand                         Show your hand
  board                        Show both boards
  trash                        Show both trash piles
  life_cards                   Show life-card zones tracked by manual effects
  logs                         Show recent action logs
  actions                      Show all legal actions
  do <index>                   Execute a legal action by index
  play <instance_id>           Play a card from your hand
  played <card_id>             Shorthand report for a played card
  attach <target_id> <amount>  Attach DON to your leader or board card
  attach <amount> <target>     Shorthand DON report also works
  attack <attacker_id> <target_id|leader>
  attack <card_id> [target]    Shorthand attack report; target defaults to leader
  draw <amount> [P1|P2]        Manually draw cards
  trash_top <amount> [P1|P2]   Move cards from top deck to trash
  reveal_top <amount> [P1|P2]  Show top cards without moving them
  discard <instance_id>        Move a hand card to trash
  ko <instance_id>             Move a board character to trash
  move <id> <from> <to> [pos]  Move card between deck/hand/board/trash/life_cards
  add_life <amount> [P1|P2]    Add top deck card(s) to life
  take_life <amount> [P1|P2]   Resolve life damage and move/trigger top life card(s)
  counter <card_id> <target>   Use a counter card from hand
  trigger <card_id> [P1|P2]    Activate a trigger from life cards
  set_state <id> <state>       Set a leader/character to active or rested
  move_don <from> <to> <n> [P1|P2] [target]
                               Move DON between don_deck/don_area/spent_don/attached
  save                         Save current state
  end                          End your turn
  quit                         Exit the CLI

Tip: Use "actions" first if you are unsure. It only lists engine-valid moves.
You can also use shorthand like "I played OP12-086", "swing OP12-119 at your leader",
"use counter OP12-098 on leader", or "your leader took 1 life".
"""
    )


def choose_from_menu(title: str, options: List[str], allow_back: bool = True) -> Optional[int]:
    if not options:
        print(f"{title}: no options")
        return None

    print(title)
    for index, option in enumerate(options, start=1):
        print(f"  {index}. {option}")
    if allow_back:
        print("  0. Back")

    while True:
        raw = input("> ").strip()
        if raw == "" and allow_back:
            return None
        try:
            selected = int(raw)
        except ValueError:
            print("Enter a number from the list.")
            continue
        if allow_back and selected == 0:
            return None
        if 1 <= selected <= len(options):
            return selected - 1
        print("Choice out of range.")


def prompt_for_amount(prompt: str, minimum: int = 1, maximum: Optional[int] = None) -> Optional[int]:
    while True:
        raw = input(f"{prompt}: ").strip()
        if raw == "":
            return None
        try:
            value = int(raw)
        except ValueError:
            print("Enter a whole number.")
            continue
        if value < minimum:
            print(f"Enter a number >= {minimum}.")
            continue
        if maximum is not None and value > maximum:
            print(f"Enter a number <= {maximum}.")
            continue
        return value


def prompt_for_player(default_player: str = HUMAN_PLAYER) -> str:
    raw = input(f"Player [default {default_player}]: ").strip().upper()
    if raw in ("P1", "P2"):
        return raw
    return default_player


def prompt_for_text(prompt: str) -> Optional[str]:
    raw = input(f"{prompt}: ").strip()
    if not raw:
        return None
    return raw


def start_manual_turn(engine: GLATEngine, state: Dict[str, Any]) -> None:
    engine.refresh_phase(state)
    engine.draw_phase(state)
    engine.don_phase(state)
    state["phase"] = "main"
    engine.validate_state(state)


def apply_human_action(
    engine: GLATEngine,
    state: Dict[str, Any],
    action: Dict[str, Any],
) -> bool:
    if not engine.is_valid_action(state, action):
        print(f"Illegal action: {action}")
        return False

    try:
        result = engine.apply_action(state, action)
    except InvalidActionError as exc:
        print(f"Illegal action: {exc}")
        return False

    print(f"Applied: {action_label(action)}")
    print(f"Result: {result}")
    return True


def ensure_cli_context(state: Dict[str, Any]) -> Dict[str, Any]:
    return state.setdefault("cli_context", {})


def begin_opponent_intake_session(state: Dict[str, Any]) -> Dict[str, Any]:
    return intake_begin_opponent_intake_session(state)


def get_active_opponent_intake(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return intake_get_active_opponent_intake(state)


def log_opponent_intake_event(
    state: Dict[str, Any],
    stage: str,
    summary: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    intake_log_opponent_intake_event(state, stage, summary, details)


def finish_opponent_intake_session(state: Dict[str, Any], status: str) -> None:
    intake_finish_opponent_intake_session(state, status)


def print_opponent_intake_log(state: Dict[str, Any], include_details: bool = False) -> None:
    intake_print_opponent_intake_log(state, include_details)


def format_battle_context_lines(battle_context: Optional[Dict[str, Any]]) -> List[str]:
    return intake_format_battle_context_lines(battle_context)


def get_last_battle_context(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return intake_get_last_battle_context(state)


def log_battle_context_event(
    state: Dict[str, Any],
    summary: str,
    battle_context: Optional[Dict[str, Any]],
    details: Optional[Dict[str, Any]] = None,
) -> None:
    intake_log_battle_context_event(state, summary, battle_context, details)


def run_logged_human_action(
    engine: GLATEngine,
    state: Dict[str, Any],
    action: Dict[str, Any],
    stage: str,
    summary: str,
) -> bool:
    return intake_run_logged_human_action(
        engine,
        state,
        action,
        stage,
        summary,
        apply_human_action,
    )


def normalize_card_reference(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def build_card_reference_lookup(engine: GLATEngine) -> Dict[str, str]:
    cached_lookup = getattr(engine, "_cli_card_reference_lookup", None)
    if cached_lookup is not None:
        return cached_lookup

    lookup: Dict[str, str] = {}
    for card_id, card in engine.catalog.items():
        lookup[normalize_card_reference(card_id)] = card_id
        lookup[normalize_card_reference(card.get("name", card_id))] = card_id
    engine._cli_card_reference_lookup = lookup
    return lookup


def resolve_card_reference(engine: GLATEngine, reference: str) -> Optional[str]:
    normalized = normalize_card_reference(reference)
    if not normalized:
        return None
    return build_card_reference_lookup(engine).get(normalized)


def choose_card_from_matches(title: str, cards: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not cards:
        return None
    if len(cards) == 1:
        return cards[0]
    selection = choose_from_menu(title, [card_label(card) for card in cards])
    if selection is None:
        return None
    return cards[selection]


def is_leader_reference(reference: str) -> bool:
    normalized = normalize_card_reference(reference)
    return normalized in {
        "LEADER",
        "MYLEADER",
        "YOURLEADER",
        "THEIRLEADER",
        "AILEADER",
        "OPPONENTLEADER",
        "HUMANLEADER",
        "P1LEADER",
        "P2LEADER",
    }


def parse_shorthand_player_reference(reference: str, default_player: str) -> str:
    normalized = normalize_card_reference(reference)
    if normalized in {"P1", "AI", "OPPONENT", "YOUR", "THEIR", "YOURLEADER", "THEIRLEADER", "AILEADER", "OPPONENTLEADER"}:
        return AI_PLAYER
    if normalized in {"P2", "HUMAN", "ME", "MY", "MINE", "MYLEADER", "HUMANLEADER"}:
        return HUMAN_PLAYER
    return default_player


def parse_target_phrase(text: str, default: str = "leader") -> str:
    cleaned = text.strip()
    if not cleaned:
        return default
    lowered = cleaned.lower()
    for prefix in ("at ", "into ", "to ", "targeting "):
        if lowered.startswith(prefix):
            return cleaned[len(prefix):].strip()
    return cleaned


def parse_natural_shorthand(command: str) -> Optional[Dict[str, Any]]:
    text = command.strip()
    if not text:
        return None

    patterns = [
        ("play", r"^(?:i\s+)?(?:play|played)\s+(.+)$"),
        ("attach", r"^(?:i\s+)?(?:attach|attached)\s+(?:(\d+)\s+)?(?:don\s+)?(?:to\s+)?(.+)$"),
        ("attack", r"^(?:i\s+)?(?:attack|attacked|swing|swung)\s+(.+?)(?:\s+(?:at|into|targeting)\s+(.+))?$"),
        ("counter", r"^(?:i\s+)?(?:counter|use\s+counter|used\s+counter)\s+(.+?)\s+(?:on|to)\s+(.+)$"),
        ("trigger", r"^(?:i\s+)?(?:trigger|activate\s+trigger|activated\s+trigger|use\s+trigger|used\s+trigger)\s+(.+)$"),
        ("ko", r"^(?:i\s+)?(?:ko|k\.?o\.?|ko[' ]?d)\s+(.+)$"),
        ("life", r"^(?:(.+?)\s+)?(?:leader\s+)?(?:took|takes|lost|lose|resolved|resolve)\s+(\d+)\s+life$"),
    ]
    lowered = text.lower()
    for kind, pattern in patterns:
        match = re.match(pattern, lowered, flags=re.IGNORECASE)
        if not match:
            continue
        groups = [group.strip() if isinstance(group, str) else group for group in match.groups()]
        if kind == "play":
            return {"kind": "play", "card_ref": text[match.start(1):match.end(1)].strip()}
        if kind == "attach":
            amount = int(groups[0]) if groups[0] else 1
            target_ref = text[match.start(2):match.end(2)].strip()
            return {"kind": "attach", "amount": amount, "target_ref": target_ref}
        if kind == "attack":
            attacker_ref = text[match.start(1):match.end(1)].strip()
            target_ref = text[match.start(2):match.end(2)].strip() if match.lastindex and match.group(2) else "leader"
            return {"kind": "attack", "attacker_ref": attacker_ref, "target_ref": target_ref}
        if kind == "counter":
            counter_ref = text[match.start(1):match.end(1)].strip()
            target_ref = text[match.start(2):match.end(2)].strip()
            return {"kind": "counter", "counter_ref": counter_ref, "target_ref": target_ref}
        if kind == "trigger":
            return {"kind": "trigger", "card_ref": text[match.start(1):match.end(1)].strip()}
        if kind == "ko":
            return {"kind": "ko", "target_ref": text[match.start(1):match.end(1)].strip()}
        player_ref = text[match.start(1):match.end(1)].strip() if match.lastindex and match.group(1) else ""
        return {"kind": "life", "player_ref": player_ref, "amount": int(groups[1])}
    return None


def resolve_card_on_player(
    engine: GLATEngine,
    state: Dict[str, Any],
    player_id: str,
    reference: str,
    zones: List[str],
    include_leader: bool = False,
) -> Optional[Dict[str, Any]]:
    player = state["players"][player_id]
    candidates: List[Dict[str, Any]] = []
    resolved_card_id = resolve_card_reference(engine, reference)
    normalized_reference = normalize_card_reference(reference)

    if include_leader:
        leader = player["leader"]
        if (
            is_leader_reference(reference)
            or normalized_reference == normalize_card_reference(leader["instance_id"])
            or (resolved_card_id is not None and leader["card_id"] == resolved_card_id)
        ):
            candidates.append(leader)

    for zone in zones:
        for card in player.get(zone, []):
            if normalize_card_reference(card["instance_id"]) == normalized_reference:
                candidates.append(card)
                continue
            if resolved_card_id is not None and card["card_id"] == resolved_card_id:
                candidates.append(card)

    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for card in candidates:
        if card["instance_id"] in seen:
            continue
        seen.add(card["instance_id"])
        deduped.append(card)
    return choose_card_from_matches(f"Choose matching card for '{reference}'", deduped)


def find_legal_play_action(engine: GLATEngine, state: Dict[str, Any], card_ref: str) -> Optional[Dict[str, Any]]:
    resolved_card = resolve_card_on_player(engine, state, HUMAN_PLAYER, card_ref, ["hand"])
    if resolved_card is None:
        return None
    for action in get_legal_actions(state, engine):
        if action["type"] == "play_card" and action["payload"]["card_id"] == resolved_card["instance_id"]:
            return action
    return None


def find_legal_attach_action(
    engine: GLATEngine,
    state: Dict[str, Any],
    target_ref: str,
    amount: int,
) -> Optional[Dict[str, Any]]:
    target_card = resolve_card_on_player(
        engine,
        state,
        HUMAN_PLAYER,
        target_ref,
        ["board"],
        include_leader=True,
    )
    if target_card is None:
        return None
    for action in get_legal_actions(state, engine):
        if (
            action["type"] == "attach_don"
            and action["payload"]["card_id"] == target_card["instance_id"]
            and int(action["payload"]["amount"]) == amount
        ):
            return action
    return None


def find_legal_attack_action(
    engine: GLATEngine,
    state: Dict[str, Any],
    attacker_ref: str,
    target_ref: str,
) -> Optional[Dict[str, Any]]:
    attacker = resolve_card_on_player(
        engine,
        state,
        HUMAN_PLAYER,
        attacker_ref,
        ["board"],
        include_leader=True,
    )
    if attacker is None:
        return None

    target = "leader"
    if not is_leader_reference(target_ref):
        target_card = resolve_card_on_player(engine, state, AI_PLAYER, target_ref, ["board"])
        if target_card is None:
            return None
        target = target_card["instance_id"]

    for action in get_legal_actions(state, engine):
        if (
            action["type"] == "attack"
            and action["payload"]["attacker_id"] == attacker["instance_id"]
            and action["payload"]["target"] == target
        ):
            return action
    return None


def parse_attach_shorthand(parts: List[str]) -> Optional[tuple[str, int]]:
    if len(parts) < 2:
        return None
    amount = 1
    if len(parts) >= 3 and parts[1].isdigit():
        amount = int(parts[1])
        target_ref = parts[2]
    elif len(parts) >= 3 and parts[2].isdigit():
        target_ref = parts[1]
        amount = int(parts[2])
    else:
        target_ref = parts[1]
    return target_ref, amount


def handle_shorthand_report(engine: GLATEngine, state: Dict[str, Any], command: str) -> bool:
    return intake_handle_shorthand_report(
        engine,
        state,
        command,
        choose_from_menu,
        apply_human_action,
        card_label,
        action_label,
        print,
    )


def parse_command_to_action(command: str) -> Optional[Dict[str, Any]]:
    parts = command.strip().split()
    if not parts:
        return None

    verb = parts[0].lower()
    if verb == "play" and len(parts) == 2:
        return {"type": "play_card", "payload": {"card_id": parts[1]}}

    if verb == "attach" and len(parts) == 3:
        return {
            "type": "attach_don",
            "payload": {"card_id": parts[1], "amount": int(parts[2])},
        }

    if verb == "attack" and len(parts) == 3:
        return {
            "type": "attack",
            "payload": {"attacker_id": parts[1], "target": parts[2]},
        }

    if verb == "end":
        return {"type": "end_turn", "payload": {}}

    return None


def owner_from_instance(instance_id: str, default_player: str = HUMAN_PLAYER) -> str:
    upper = instance_id.upper()
    if upper.startswith("P1-"):
        return "P1"
    if upper.startswith("P2-"):
        return "P2"
    return default_player


def player_arg(parts: List[str], index: int, default_player: str = HUMAN_PLAYER) -> str:
    if len(parts) > index and parts[index].upper() in ("P1", "P2"):
        return parts[index].upper()
    return default_player


def run_manual_command(engine: GLATEngine, state: Dict[str, Any], command: str) -> bool:
    parts = command.strip().split()
    if not parts:
        return False

    verb = parts[0].lower()
    try:
        if verb == "draw" and len(parts) in (2, 3):
            amount = int(parts[1])
            player_id = player_arg(parts, 2)
            print("Drawn:", engine.manual_draw(state, player_id, amount))
            return True

        if verb == "trash_top" and len(parts) in (2, 3):
            amount = int(parts[1])
            player_id = player_arg(parts, 2)
            print("Trashed:", engine.manual_trash_top(state, player_id, amount))
            return True

        if verb == "reveal_top" and len(parts) in (2, 3):
            amount = int(parts[1])
            player_id = player_arg(parts, 2)
            revealed = engine.manual_reveal_top(state, player_id, amount)
            for card in revealed:
                print(card_label(card))
            return True

        if verb == "discard" and len(parts) == 2:
            player_id = owner_from_instance(parts[1])
            print("Discarded:", engine.manual_discard(state, player_id, parts[1]))
            return True

        if verb == "ko" and len(parts) == 2:
            player_id = owner_from_instance(parts[1])
            print("KO:", engine.manual_ko(state, player_id, parts[1]))
            return True

        if verb == "move" and len(parts) in (4, 5, 6):
            instance_id = parts[1]
            source_zone = parts[2]
            destination_zone = parts[3]
            position = "bottom"
            player_id = owner_from_instance(instance_id)
            if len(parts) >= 5:
                if parts[4].lower() in ("top", "bottom"):
                    position = parts[4].lower()
                elif parts[4].upper() in ("P1", "P2"):
                    player_id = parts[4].upper()
            if len(parts) == 6:
                player_id = parts[5].upper()
            print(
                "Moved:",
                engine.manual_move_card(
                    state,
                    player_id,
                    instance_id,
                    source_zone,
                    destination_zone,
                    position,
                ),
            )
            return True

        if verb == "add_life" and len(parts) in (2, 3):
            amount = int(parts[1])
            player_id = player_arg(parts, 2)
            print("Added life cards:", engine.manual_add_life(state, player_id, amount))
            return True

        if verb == "take_life" and len(parts) in (2, 3):
            amount = int(parts[1])
            player_id = player_arg(parts, 2)
            print("Life resolved:", engine.manual_resolve_life_damage(state, player_id, amount))
            return True

        if verb == "counter" and len(parts) == 3:
            player_id = owner_from_instance(parts[1])
            print("Counter:", engine.manual_use_counter(state, player_id, parts[1], parts[2]))
            return True

        if verb == "trigger" and len(parts) in (2, 3):
            player_id = player_arg(parts, 2, owner_from_instance(parts[1]))
            print("Trigger:", engine.manual_activate_trigger(state, player_id, parts[1]))
            return True

        if verb == "set_state" and len(parts) == 3:
            player_id = owner_from_instance(parts[1])
            print("Card state:", engine.manual_set_card_state(state, player_id, parts[1], parts[2].lower()))
            return True

        if verb == "move_don" and len(parts) in (4, 6):
            source_zone = parts[1]
            destination_zone = parts[2]
            amount = int(parts[3])
            player_id = HUMAN_PLAYER
            attach_target = None
            if len(parts) >= 5:
                if parts[4].upper() in ("P1", "P2"):
                    player_id = parts[4].upper()
                else:
                    attach_target = parts[4]
            if len(parts) == 6:
                attach_target = parts[5]
            print(
                "DON moved:",
                engine.manual_move_don(
                    state,
                    player_id,
                    source_zone,
                    destination_zone,
                    amount,
                    attach_target=attach_target,
                ),
            )
            return True
    except Exception as exc:
        print(f"Manual command failed: {exc}")
        return True

    return False


def guided_play_card(engine: GLATEngine, state: Dict[str, Any]) -> bool:
    actions = [a for a in get_legal_actions(state, engine) if a["type"] == "play_card"]
    options = [action_label(action) for action in actions]
    selection = choose_from_menu("Playable cards", options)
    if selection is None:
        return False
    selected_action = actions[selection]
    return run_logged_human_action(
        engine,
        state,
        selected_action,
        "main",
        f"Played {selected_action['payload']['card_id']}",
    )


def guided_attach_don(engine: GLATEngine, state: Dict[str, Any]) -> bool:
    actions = [a for a in get_legal_actions(state, engine) if a["type"] == "attach_don"]
    options = [action_label(action) for action in actions]
    selection = choose_from_menu("Attach DON options", options)
    if selection is None:
        return False
    selected_action = actions[selection]
    return run_logged_human_action(
        engine,
        state,
        selected_action,
        "main",
        f"Attached {selected_action['payload']['amount']} DON to {selected_action['payload']['card_id']}",
    )


def guided_attack(engine: GLATEngine, state: Dict[str, Any]) -> bool:
    actions = [a for a in get_legal_actions(state, engine) if a["type"] == "attack"]
    options = [action_label(action) for action in actions]
    selection = choose_from_menu("Attack options", options)
    if selection is None:
        return False
    selected_action = actions[selection]
    if not apply_human_action(engine, state, selected_action):
        return False
    battle_context = get_last_battle_context(state)
    log_opponent_intake_event(
        state,
        "attack",
        f"Attack declared with {selected_action['payload']['attacker_id']} into {selected_action['payload']['target']}",
        {"action": selected_action, "battle_context": battle_context},
    )
    log_battle_context_event(
        state,
        f"Battle trace for {selected_action['payload']['attacker_id']} into {selected_action['payload']['target']}",
        battle_context,
        {"action": selected_action},
    )
    return True


def guided_effect_resolution(engine: GLATEngine, state: Dict[str, Any]) -> bool:
    choice = choose_from_menu(
        "Resolve effect",
        [
            "Draw cards",
            "Resolve life damage",
            "Trash top cards",
            "Reveal top cards",
            "Discard a card from hand",
            "KO a board card",
            "Move a card between zones",
            "Add life from deck",
            "Use a counter card",
            "Activate a trigger from life",
        ],
    )
    if choice is None:
        return False

    if choice == 0:
        amount = prompt_for_amount("How many cards to draw", 1)
        if amount is None:
            return False
        player_id = prompt_for_player()
        print("Drawn:", engine.manual_draw(state, player_id, amount))
        return True

    if choice == 1:
        amount = prompt_for_amount("How many life cards to resolve", 1)
        if amount is None:
            return False
        player_id = prompt_for_player()
        print("Life resolved:", engine.manual_resolve_life_damage(state, player_id, amount))
        return True

    if choice == 2:
        amount = prompt_for_amount("How many cards to trash from top of deck", 1)
        if amount is None:
            return False
        player_id = prompt_for_player()
        print("Trashed:", engine.manual_trash_top(state, player_id, amount))
        return True

    if choice == 3:
        amount = prompt_for_amount("How many cards to reveal from top of deck", 1)
        if amount is None:
            return False
        player_id = prompt_for_player()
        revealed = engine.manual_reveal_top(state, player_id, amount)
        for card in revealed:
            print(card_label(card))
        return True

    if choice == 4:
        player_id = prompt_for_player()
        hand = state["players"][player_id]["hand"]
        selection = choose_from_menu("Choose card to discard", [card_label(card) for card in hand])
        if selection is None:
            return False
        print("Discarded:", engine.manual_discard(state, player_id, hand[selection]["instance_id"]))
        return True

    if choice == 5:
        player_id = prompt_for_player()
        board = state["players"][player_id]["board"]
        selection = choose_from_menu("Choose board card to KO", [card_label(card) for card in board])
        if selection is None:
            return False
        print("KO:", engine.manual_ko(state, player_id, board[selection]["instance_id"]))
        return True

    if choice == 6:
        player_id = prompt_for_player()
        source_zone_index = choose_from_menu("From zone", ["deck", "hand", "board", "trash", "life_cards"])
        if source_zone_index is None:
            return False
        source_zone = ["deck", "hand", "board", "trash", "life_cards"][source_zone_index]
        cards = state["players"][player_id][source_zone]
        selection = choose_from_menu("Choose card to move", [card_label(card) for card in cards])
        if selection is None:
            return False
        destination_zone_index = choose_from_menu("To zone", ["deck", "hand", "board", "trash", "life_cards"])
        if destination_zone_index is None:
            return False
        destination_zone = ["deck", "hand", "board", "trash", "life_cards"][destination_zone_index]
        position = "bottom"
        if destination_zone in ("deck", "hand", "trash", "life_cards"):
            pos_choice = choose_from_menu("Position", ["top", "bottom"])
            if pos_choice is None:
                return False
            position = ["top", "bottom"][pos_choice]
        print(
            "Moved:",
            engine.manual_move_card(
                state,
                player_id,
                cards[selection]["instance_id"],
                source_zone,
                destination_zone,
                position,
            ),
        )
        return True

    if choice == 7:
        amount = prompt_for_amount("How many life cards to add from top of deck", 1)
        if amount is None:
            return False
        player_id = prompt_for_player()
        print("Added life cards:", engine.manual_add_life(state, player_id, amount))
        return True

    if choice == 8:
        player_id = prompt_for_player()
        hand = state["players"][player_id]["hand"]
        counter_options = [
            card for card in hand
            if card.get("counter") or card["card_id"] in {"OP12-098", "OP06-115"}
        ]
        selection = choose_from_menu("Choose a counter card", [card_label(card) for card in counter_options])
        if selection is None:
            return False
        targets = [state["players"][player_id]["leader"]] + state["players"][player_id]["board"]
        target_index = choose_from_menu("Choose a counter target", [card_label(card) for card in targets])
        if target_index is None:
            return False
        print(
            "Counter:",
            engine.manual_use_counter(
                state,
                player_id,
                counter_options[selection]["instance_id"],
                targets[target_index]["instance_id"],
            ),
        )
        return True

    if choice == 9:
        player_id = prompt_for_player()
        life_cards = state["players"][player_id]["life_cards"]
        selection = choose_from_menu("Choose a life card trigger to activate", [card_label(card) for card in life_cards])
        if selection is None:
            return False
        print(
            "Trigger:",
            engine.manual_activate_trigger(
                state,
                player_id,
                life_cards[selection]["instance_id"],
            ),
        )
        return True

    return False


def guided_physical_report(engine: GLATEngine, state: Dict[str, Any]) -> bool:
    choice = choose_from_menu(
        "What happened on the table?",
        [
            "I played a card",
            "I attached DON",
            "I declared an attack",
            "A leader took damage",
            "A character was K.O.'d",
            "A trigger was activated from life",
            "A counter was used",
            "I need an effect / zone helper",
        ],
    )
    if choice is None:
        return False

    if choice == 0:
        return guided_play_card(engine, state)

    if choice == 1:
        return guided_attach_don(engine, state)

    if choice == 2:
        return guided_attack(engine, state)

    if choice == 3:
        amount = prompt_for_amount("How many life cards should be resolved", 1)
        if amount is None:
            return False
        damaged_player = prompt_for_player()
        result = engine.manual_resolve_life_damage(state, damaged_player, amount)
        print("Life resolved:", result)
        log_opponent_intake_event(
            state,
            "battle",
            f"Resolved {amount} life damage for {damaged_player}",
            {"player": damaged_player, "amount": amount, "result": result},
        )
        return True

    if choice == 4:
        player_id = prompt_for_player()
        board = state["players"][player_id]["board"]
        selection = choose_from_menu("Which character was K.O.'d?", [card_label(card) for card in board])
        if selection is None:
            return False
        result = engine.manual_ko(state, player_id, board[selection]["instance_id"])
        print("KO:", result)
        log_opponent_intake_event(
            state,
            "battle",
            f"K.O.'d {board[selection]['instance_id']}",
            {"player": player_id, "instance_id": board[selection]["instance_id"], "result": result},
        )
        return True

    if choice == 5:
        player_id = prompt_for_player()
        life_cards = state["players"][player_id]["life_cards"]
        selection = choose_from_menu("Which life card trigger was activated?", [card_label(card) for card in life_cards])
        if selection is None:
            return False
        result = engine.manual_activate_trigger(
            state,
            player_id,
            life_cards[selection]["instance_id"],
        )
        print("Trigger:", result)
        log_opponent_intake_event(
            state,
            "battle",
            f"Activated trigger from {life_cards[selection]['instance_id']}",
            {"player": player_id, "instance_id": life_cards[selection]["instance_id"], "result": result},
        )
        return True

    if choice == 6:
        player_id = prompt_for_player()
        hand = state["players"][player_id]["hand"]
        counter_options = [
            card for card in hand
            if card.get("counter") or card["card_id"] in {"OP12-098", "OP06-115"}
        ]
        selection = choose_from_menu("Which counter card was used?", [card_label(card) for card in counter_options])
        if selection is None:
            return False
        targets = [state["players"][player_id]["leader"]] + state["players"][player_id]["board"]
        target_index = choose_from_menu("Which card received the counter bonus?", [card_label(card) for card in targets])
        if target_index is None:
            return False
        result = engine.manual_use_counter(
            state,
            player_id,
            counter_options[selection]["instance_id"],
            targets[target_index]["instance_id"],
        )
        print("Counter:", result)
        log_opponent_intake_event(
            state,
            "battle",
            f"Used counter {counter_options[selection]['instance_id']} on {targets[target_index]['instance_id']}",
            {
                "player": player_id,
                "counter_id": counter_options[selection]["instance_id"],
                "target_id": targets[target_index]["instance_id"],
                "result": result,
            },
        )
        return True

    return guided_effect_resolution(engine, state)


def guided_attack_follow_up(engine: GLATEngine, state: Dict[str, Any], state_out: str) -> bool:
    while True:
        choice = choose_from_menu(
            "Attack follow-up",
            [
                "State matches, continue opponent turn",
                "Resolve another attack result",
                "Use effect / zone helper",
                "Review / correct state",
                "Record hidden information note",
                "Show state",
                "Show intake log",
            ],
            allow_back=False,
        )
        if choice == 0:
            log_opponent_intake_event(state, "attack", "Attack sequence confirmed with no extra corrections.")
            return True
        if choice == 1:
            if guided_physical_report(engine, state):
                save_state(engine, state, state_out)
            continue
        if choice == 2:
            if guided_effect_resolution(engine, state):
                save_state(engine, state, state_out)
            continue
        if choice == 3:
            guided_state_review(engine, state)
            save_state(engine, state, state_out)
            continue
        if choice == 4:
            note = prompt_for_text("What hidden information should be noted")
            if note:
                log_opponent_intake_event(state, "hidden_info", note)
                save_state(engine, state, state_out)
            continue
        if choice == 5:
            print_summary(state)
            continue
        print_opponent_intake_log(state, include_details=True)
        battle_context = get_last_battle_context(state)
        if battle_context:
            print("Latest battle trace:")
            for line in format_battle_context_lines(battle_context):
                print(f"  {line}")


def guided_opponent_turn_step(engine: GLATEngine, state: Dict[str, Any], state_out: str) -> Optional[bool]:
    print("\nOpponent turn intake")
    choice = choose_from_menu(
        "Choose the next reported event",
        [
            "Report a played card",
            "Report a DON attachment",
            "Report an attack sequence",
            "Resolve an effect or board change",
            "Review / correct state",
            "Record hidden information note",
            "Show state",
            "Show intake log",
            "Enter a raw or shorthand command",
            "Finish opponent turn",
            "Quit",
        ],
        allow_back=False,
    )
    if choice is None:
        return None

    if choice == 0:
        guided_play_card(engine, state)
        save_state(engine, state, state_out)
        return None
    if choice == 1:
        guided_attach_don(engine, state)
        save_state(engine, state, state_out)
        return None
    if choice == 2:
        if guided_attack(engine, state):
            save_state(engine, state, state_out)
            guided_attack_follow_up(engine, state, state_out)
            save_state(engine, state, state_out)
        return None
    if choice == 3:
        if guided_physical_report(engine, state):
            save_state(engine, state, state_out)
        return None
    if choice == 4:
        guided_state_review(engine, state)
        log_opponent_intake_event(state, "reconcile", "Opened review/correction flow.")
        save_state(engine, state, state_out)
        return None
    if choice == 5:
        note = prompt_for_text("What hidden information should be noted")
        if note:
            log_opponent_intake_event(state, "hidden_info", note)
            save_state(engine, state, state_out)
        return None
    if choice == 6:
        print_summary(state)
        return None
    if choice == 7:
        print_opponent_intake_log(state, include_details=True)
        return None
    if choice == 8:
        raw = input("Command> ").strip()
        if raw:
            if handle_shorthand_report(engine, state, raw):
                save_state(engine, state, state_out)
                if raw.strip().split()[0].lower() in {"attack", "attacked", "swing", "swung"}:
                    guided_attack_follow_up(engine, state, state_out)
                    save_state(engine, state, state_out)
                return None
            if run_manual_command(engine, state, raw):
                log_opponent_intake_event(state, "manual", f"Manual command: {raw}")
                save_state(engine, state, state_out)
                return None
            action = parse_command_to_action(raw)
            if action and apply_human_action(engine, state, action):
                log_opponent_intake_event(state, "manual", f"Manual action: {raw}", {"action": action})
                save_state(engine, state, state_out)
                if action["type"] == "end_turn":
                    engine.end_phase(state)
                    finish_opponent_intake_session(state, "completed")
                    save_state(engine, state, state_out)
                    return True
            else:
                print("Unknown command. Try shorthand like 'played OP12-012' or help-style commands like play/attach/attack/end.")
        return None
    if choice == 9:
        end_action = {"type": "end_turn", "payload": {}}
        if run_logged_human_action(engine, state, end_action, "end", "Opponent turn ended."):
            engine.end_phase(state)
            finish_opponent_intake_session(state, "completed")
            save_state(engine, state, state_out)
            return True
        return None

    finish_opponent_intake_session(state, "abandoned")
    save_state(engine, state, state_out)
    return False


def guided_state_review(engine: GLATEngine, state: Dict[str, Any]) -> bool:
    choice = choose_from_menu(
        "Review / correct state",
        [
            "Show full summary",
            "Show zones for a player",
            "Show DON summary",
            "Move a card between zones",
            "Resolve life damage",
            "Add life from deck",
            "Set card state to active/rested",
            "Move DON between zones",
        ],
    )
    if choice is None:
        return False

    if choice == 0:
        print_summary(state)
        return False

    if choice == 1:
        player_id = prompt_for_player()
        zone_index = choose_from_menu("Which zone?", ["hand", "board", "deck", "trash", "life_cards", "leader"])
        if zone_index is None:
            return False
        zone = ["hand", "board", "deck", "trash", "life_cards", "leader"][zone_index]
        print_zone(state, player_id, zone)
        return False

    if choice == 2:
        player_id = prompt_for_player()
        print_don_summary(state, player_id)
        return False

    if choice == 3:
        player_id = prompt_for_player()
        source_zone_index = choose_from_menu("From zone", ["deck", "hand", "board", "trash", "life_cards"])
        if source_zone_index is None:
            return False
        source_zone = ["deck", "hand", "board", "trash", "life_cards"][source_zone_index]
        cards = state["players"][player_id][source_zone]
        selection = choose_from_menu("Choose card to move", [card_label(card) for card in cards])
        if selection is None:
            return False
        destination_zone_index = choose_from_menu("To zone", ["deck", "hand", "board", "trash", "life_cards"])
        if destination_zone_index is None:
            return False
        destination_zone = ["deck", "hand", "board", "trash", "life_cards"][destination_zone_index]
        position = "bottom"
        if destination_zone in ("deck", "hand", "trash", "life_cards"):
            pos_choice = choose_from_menu("Position", ["top", "bottom"])
            if pos_choice is None:
                return False
            position = ["top", "bottom"][pos_choice]
        print(
            "Moved:",
            engine.manual_move_card(
                state,
                player_id,
                cards[selection]["instance_id"],
                source_zone,
                destination_zone,
                position,
            ),
        )
        return True

    if choice == 4:
        amount = prompt_for_amount("How many life cards to resolve", 1)
        if amount is None:
            return False
        player_id = prompt_for_player()
        print("Life resolved:", engine.manual_resolve_life_damage(state, player_id, amount))
        return True

    if choice == 5:
        amount = prompt_for_amount("How many life cards to add from top of deck", 1)
        if amount is None:
            return False
        player_id = prompt_for_player()
        print("Added life cards:", engine.manual_add_life(state, player_id, amount))
        return True

    if choice == 6:
        player_id = prompt_for_player()
        targets = [state["players"][player_id]["leader"]] + state["players"][player_id]["board"]
        selection = choose_from_menu("Choose card", [card_label(card) for card in targets])
        if selection is None:
            return False
        state_choice = choose_from_menu("Set state to", ["active", "rested"])
        if state_choice is None:
            return False
        print(
            "Card state:",
            engine.manual_set_card_state(
                state,
                player_id,
                targets[selection]["instance_id"],
                ["active", "rested"][state_choice],
            ),
        )
        return True

    player_id = prompt_for_player()
    print_don_summary(state, player_id)
    source_index = choose_from_menu("Move DON from", ["don_deck", "don_area", "spent_don", "attached"])
    if source_index is None:
        return False
    destination_index = choose_from_menu("Move DON to", ["don_deck", "don_area", "spent_don", "attached"])
    if destination_index is None:
        return False
    amount = prompt_for_amount("How many DON", 1)
    if amount is None:
        return False
    source_zone = ["don_deck", "don_area", "spent_don", "attached"][source_index]
    destination_zone = ["don_deck", "don_area", "spent_don", "attached"][destination_index]
    attach_target = None
    if source_zone == "attached" or destination_zone == "attached":
        targets = [state["players"][player_id]["leader"]] + state["players"][player_id]["board"]
        selection = choose_from_menu("Attach target", [card_label(card) for card in targets])
        if selection is None:
            return False
        attach_target = targets[selection]["instance_id"]
    print(
        "DON moved:",
        engine.manual_move_don(
            state,
            player_id,
            source_zone,
            destination_zone,
            amount,
            attach_target=attach_target,
        ),
    )
    return True


def guided_human_step(engine: GLATEngine, state: Dict[str, Any], state_out: str) -> Optional[bool]:
    print("\nWhat did you do?")
    choice = choose_from_menu(
        "Choose an action",
        [
            "Report a table action",
            "Review / correct state",
            "Show state",
            "Show hand",
            "Show board",
            "Show recent logs",
            "Enter a raw or shorthand command",
            "End turn",
            "Quit",
        ],
        allow_back=False,
    )
    if choice is None:
        return None

    if choice == 0:
        guided_physical_report(engine, state)
        save_state(engine, state, state_out)
        return None
    if choice == 1:
        guided_state_review(engine, state)
        save_state(engine, state, state_out)
        return None
    if choice == 2:
        print_summary(state)
        return None
    if choice == 3:
        print_zone(state, HUMAN_PLAYER, "hand")
        return None
    if choice == 4:
        print_zone(state, AI_PLAYER, "board")
        print_zone(state, HUMAN_PLAYER, "board")
        return None
    if choice == 5:
        print_recent_logs(state)
        return None
    if choice == 6:
        raw = input("Command> ").strip()
        if raw:
            if handle_shorthand_report(engine, state, raw):
                save_state(engine, state, state_out)
                return None
            if run_manual_command(engine, state, raw):
                save_state(engine, state, state_out)
                return None
            action = parse_command_to_action(raw)
            if action and apply_human_action(engine, state, action):
                save_state(engine, state, state_out)
                if action["type"] == "end_turn":
                    engine.end_phase(state)
                    save_state(engine, state, state_out)
                    return True
            else:
                print("Unknown command. Try shorthand like 'played OP12-012' or help-style commands like play/attach/attack/end.")
        return None
    if choice == 7:
        end_action = {"type": "end_turn", "payload": {}}
        if apply_human_action(engine, state, end_action):
            engine.end_phase(state)
            save_state(engine, state, state_out)
            return True
        return None

    save_state(engine, state, state_out)
    return False


def run_human_turn(engine: GLATEngine, state: Dict[str, Any], state_out: str) -> bool:
    start_manual_turn(engine, state)
    begin_opponent_intake_session(state)
    log_opponent_intake_event(
        state,
        "turn_start",
        "Started guided opponent turn intake after refresh/draw/DON.",
        {
            "turn": state["turn"],
            "active_player": state["active_player"],
            "phase": state["phase"],
        },
    )
    print_summary(state)
    print("Guided opponent turn intake is active.")
    print("Report the real-life actions the opponent made, and use review helpers whenever the table drifts.")
    print("You can still choose 'Enter a raw command' if needed.")

    while not state["winner"]:
        step_result = guided_opponent_turn_step(engine, state, state_out)
        if step_result is True:
            return True
        if step_result is False:
            return False

    save_state(engine, state, state_out)
    return False


def run_ai_turn(engine: GLATEngine, state: Dict[str, Any], state_out: str) -> None:
    print_summary(state)
    print("AI turn running...")
    start_log_count = len(state["logs"])
    engine.run_turn(state)
    save_state(engine, state, state_out)
    print("AI turn complete.")
    for log in state["logs"][start_log_count:]:
        print(f"  {log['action']['type']}: {log['result']}")


def run_demo(engine: GLATEngine, state: Dict[str, Any], turns: int, state_out: str) -> None:
    for _ in range(turns):
        if state["winner"]:
            break
        if state["active_player"] == AI_PLAYER:
            run_ai_turn(engine, state, state_out)
        else:
            start_manual_turn(engine, state)
            end_action = {"type": "end_turn", "payload": {}}
            apply_human_action(engine, state, end_action)
            engine.end_phase(state)
            save_state(engine, state, state_out)
    print_summary(state)
    print(f"Saved to {state_out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="GLAT CLI game loop")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--state-out", default="cli_game_state.json")
    parser.add_argument("--load-state", default=None, help="Resume from an existing saved game state JSON")
    parser.add_argument("--fake-ai", action="store_true", help="Use deterministic fake AI instead of Gemini")
    parser.add_argument("--demo-turns", type=int, default=0, help="Run N non-interactive smoke-test turns")
    args = parser.parse_args()

    agent = FakePlanningAgent() if args.fake_ai else None
    engine = GLATEngine(
        agent=agent,
        effect_choice_provider=cli_effect_choice,
        defense_choice_provider=cli_defense_choice,
        trigger_choice_provider=cli_trigger_choice,
    )
    if args.load_state:
        state = load_state(engine, args.load_state)
        save_state(engine, state, args.state_out)
    else:
        state = engine.create_initial_state(seed=args.seed)
        save_state(engine, state, args.state_out)

    print("GLAT CLI started.")
    if args.load_state:
        print(f"Resumed game from: {Path(args.load_state).resolve()}")
    else:
        print("Both P1 and P2 decks were built from cards.json.")
    print(f"State file: {Path(args.state_out).resolve()}")

    if args.demo_turns > 0:
        run_demo(engine, state, args.demo_turns, args.state_out)
        return

    while not state["winner"]:
        if state["active_player"] == AI_PLAYER:
            run_ai_turn(engine, state, args.state_out)
        else:
            should_continue = run_human_turn(engine, state, args.state_out)
            if not should_continue:
                print("Exiting CLI.")
                return

    print_summary(state)
    print(f"Game over. Winner: {state['winner']}")


if __name__ == "__main__":
    main()
