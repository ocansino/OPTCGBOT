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
  end                          End your turn
  save                         Save current state
  quit                         Exit the CLI

Tip: Use "actions" first if you are unsure. It only lists engine-valid moves.
"""
    )


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
    except Exception as exc:
        print(f"Manual command failed: {exc}")
        return True

    return False


def run_human_turn(engine: GLATEngine, state: Dict[str, Any], state_out: str) -> bool:
    start_manual_turn(engine, state)
    print_summary(state)
    print_help()

    while not state["winner"]:
        command = input("P2> ").strip()
        if not command:
            continue

        lowered = command.lower()
        if lowered == "quit":
            save_state(engine, state, state_out)
            return False
        if lowered == "help":
            print_help()
            continue
        if lowered == "state":
            print_summary(state)
            continue
        if lowered == "hand":
            print_zone(state, HUMAN_PLAYER, "hand")
            continue
        if lowered == "board":
            print_zone(state, AI_PLAYER, "board")
            print_zone(state, HUMAN_PLAYER, "board")
            continue
        if lowered == "trash":
            print_zone(state, AI_PLAYER, "trash")
            print_zone(state, HUMAN_PLAYER, "trash")
            continue
        if lowered == "life_cards":
            print_zone(state, AI_PLAYER, "life_cards")
            print_zone(state, HUMAN_PLAYER, "life_cards")
            continue
        if lowered == "logs":
            print_recent_logs(state)
            continue
        if lowered == "save":
            save_state(engine, state, state_out)
            print(f"Saved to {state_out}")
            continue
        if lowered == "actions":
            actions = get_legal_actions(state, engine)
            for index, action in enumerate(actions):
                print(f"{index}: {action_label(action)}")
            continue
        if lowered.startswith("do "):
            actions = get_legal_actions(state, engine)
            try:
                index = int(lowered.split()[1])
                action = actions[index]
            except (IndexError, ValueError):
                print("Usage: do <legal_action_index>")
                continue
            if apply_human_action(engine, state, action) and action["type"] == "end_turn":
                engine.end_phase(state)
                save_state(engine, state, state_out)
                return True
            save_state(engine, state, state_out)
            continue

        if run_manual_command(engine, state, command):
            save_state(engine, state, state_out)
            continue

        try:
            action = parse_command_to_action(command)
        except ValueError:
            print("Could not parse command. Try 'help' or 'actions'.")
            continue

        if action is None:
            print("Unknown command. Try 'help' or 'actions'.")
            continue

        if apply_human_action(engine, state, action) and action["type"] == "end_turn":
            engine.end_phase(state)
            save_state(engine, state, state_out)
            return True

        save_state(engine, state, state_out)

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
    engine = GLATEngine(agent=agent)
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
