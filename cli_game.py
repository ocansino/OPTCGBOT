import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

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
  attach <target_id> <amount>  Attach DON to your leader or board card
  attack <attacker_id> <target_id|leader>
  draw <amount> [P1|P2]        Manually draw cards
  trash_top <amount> [P1|P2]   Move cards from top deck to trash
  reveal_top <amount> [P1|P2]  Show top cards without moving them
  discard <instance_id>        Move a hand card to trash
  ko <instance_id>             Move a board character to trash
  move <id> <from> <to> [pos]  Move card between deck/hand/board/trash/life_cards
  add_life <amount> [P1|P2]    Add top deck card(s) to life
  counter <card_id> <target>   Use a counter card from hand
  trigger <card_id> [P1|P2]    Activate a trigger from life cards
  end                          End your turn
  save                         Save current state
  quit                         Exit the CLI

Tip: Use "actions" first if you are unsure. It only lists engine-valid moves.
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

        if verb == "counter" and len(parts) == 3:
            player_id = owner_from_instance(parts[1])
            print("Counter:", engine.manual_use_counter(state, player_id, parts[1], parts[2]))
            return True

        if verb == "trigger" and len(parts) in (2, 3):
            player_id = player_arg(parts, 2, owner_from_instance(parts[1]))
            print("Trigger:", engine.manual_activate_trigger(state, player_id, parts[1]))
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
    return apply_human_action(engine, state, actions[selection])


def guided_attach_don(engine: GLATEngine, state: Dict[str, Any]) -> bool:
    actions = [a for a in get_legal_actions(state, engine) if a["type"] == "attach_don"]
    options = [action_label(action) for action in actions]
    selection = choose_from_menu("Attach DON options", options)
    if selection is None:
        return False
    return apply_human_action(engine, state, actions[selection])


def guided_attack(engine: GLATEngine, state: Dict[str, Any]) -> bool:
    actions = [a for a in get_legal_actions(state, engine) if a["type"] == "attack"]
    options = [action_label(action) for action in actions]
    selection = choose_from_menu("Attack options", options)
    if selection is None:
        return False
    return apply_human_action(engine, state, actions[selection])


def guided_effect_resolution(engine: GLATEngine, state: Dict[str, Any]) -> bool:
    choice = choose_from_menu(
        "Resolve effect",
        [
            "Draw cards",
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
        amount = prompt_for_amount("How many cards to trash from top of deck", 1)
        if amount is None:
            return False
        player_id = prompt_for_player()
        print("Trashed:", engine.manual_trash_top(state, player_id, amount))
        return True

    if choice == 2:
        amount = prompt_for_amount("How many cards to reveal from top of deck", 1)
        if amount is None:
            return False
        player_id = prompt_for_player()
        revealed = engine.manual_reveal_top(state, player_id, amount)
        for card in revealed:
            print(card_label(card))
        return True

    if choice == 3:
        player_id = prompt_for_player()
        hand = state["players"][player_id]["hand"]
        selection = choose_from_menu("Choose card to discard", [card_label(card) for card in hand])
        if selection is None:
            return False
        print("Discarded:", engine.manual_discard(state, player_id, hand[selection]["instance_id"]))
        return True

    if choice == 4:
        player_id = prompt_for_player()
        board = state["players"][player_id]["board"]
        selection = choose_from_menu("Choose board card to KO", [card_label(card) for card in board])
        if selection is None:
            return False
        print("KO:", engine.manual_ko(state, player_id, board[selection]["instance_id"]))
        return True

    if choice == 5:
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

    if choice == 6:
        amount = prompt_for_amount("How many life cards to add from top of deck", 1)
        if amount is None:
            return False
        player_id = prompt_for_player()
        print("Added life cards:", engine.manual_add_life(state, player_id, amount))
        return True

    if choice == 7:
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


def guided_human_step(engine: GLATEngine, state: Dict[str, Any], state_out: str) -> Optional[bool]:
    print("\nWhat did you do?")
    choice = choose_from_menu(
        "Choose an action",
        [
            "Play a card from hand",
            "Attach DON",
            "Declare an attack",
            "Resolve an effect / move cards",
            "Show state",
            "Show hand",
            "Show board",
            "Show recent logs",
            "Enter a raw command",
            "End turn",
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
        guided_attack(engine, state)
        save_state(engine, state, state_out)
        return None
    if choice == 3:
        guided_effect_resolution(engine, state)
        save_state(engine, state, state_out)
        return None
    if choice == 4:
        print_summary(state)
        return None
    if choice == 5:
        print_zone(state, HUMAN_PLAYER, "hand")
        return None
    if choice == 6:
        print_zone(state, AI_PLAYER, "board")
        print_zone(state, HUMAN_PLAYER, "board")
        return None
    if choice == 7:
        print_recent_logs(state)
        return None
    if choice == 8:
        raw = input("Raw command> ").strip()
        if raw:
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
                print("Unknown command. Try help-style commands like play/attach/attack/end.")
        return None
    if choice == 9:
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
    print_summary(state)
    print("Guided turn mode is active. Use the menu prompts below.")
    print("You can still choose 'Enter a raw command' if needed.")

    while not state["winner"]:
        step_result = guided_human_step(engine, state, state_out)
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
    state = engine.create_initial_state(seed=args.seed)
    save_state(engine, state, args.state_out)

    print("GLAT CLI started.")
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
