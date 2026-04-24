import re
from typing import Any, Callable, Dict, List, Optional

from referee import get_legal_actions


AI_PLAYER = "P1"
HUMAN_PLAYER = "P2"


ChooseMenu = Callable[[str, List[str], bool], Optional[int]]
ApplyAction = Callable[[Any, Dict[str, Any], Dict[str, Any]], bool]
CardLabel = Callable[[Dict[str, Any]], str]
ActionLabel = Callable[[Dict[str, Any]], str]
Printer = Callable[[str], None]


def ensure_cli_context(state: Dict[str, Any]) -> Dict[str, Any]:
    return state.setdefault("cli_context", {})


def begin_opponent_intake_session(state: Dict[str, Any]) -> Dict[str, Any]:
    context = ensure_cli_context(state)
    history = context.setdefault("opponent_turn_history", [])
    session = {
        "turn": state["turn"],
        "player": HUMAN_PLAYER,
        "phase": state["phase"],
        "status": "active",
        "events": [],
        "memory": {},
    }
    history.append(session)
    context["active_opponent_intake"] = session
    return session


def get_active_opponent_intake(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return ensure_cli_context(state).get("active_opponent_intake")


def log_opponent_intake_event(
    state: Dict[str, Any],
    stage: str,
    summary: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    session = get_active_opponent_intake(state)
    if session is None:
        session = begin_opponent_intake_session(state)
    session["events"].append(
        {
            "index": len(session["events"]) + 1,
            "stage": stage,
            "summary": summary,
            "details": details or {},
        }
    )


def finish_opponent_intake_session(state: Dict[str, Any], status: str) -> None:
    session = get_active_opponent_intake(state)
    if session is None:
        return
    session["status"] = status
    session["final_phase"] = state["phase"]
    ensure_cli_context(state)["active_opponent_intake"] = None


def print_opponent_intake_log(state: Dict[str, Any], include_details: bool = False) -> None:
    session = get_active_opponent_intake(state)
    if session is None:
        history = ensure_cli_context(state).get("opponent_turn_history", [])
        if not history:
            print("No opponent intake session recorded yet.")
            return
        session = history[-1]

    print(
        f"Opponent intake | turn {session['turn']} | "
        f"phase {session.get('phase', '-')} | status {session.get('status', '-')}"
    )
    if not session["events"]:
        print("  No intake events recorded yet.")
        return
    for event in session["events"]:
        print(f"  {event['index']}. [{event['stage']}] {event['summary']}")
        if include_details and event["details"]:
            print(f"     details: {event['details']}")


def run_logged_human_action(
    engine: Any,
    state: Dict[str, Any],
    action: Dict[str, Any],
    stage: str,
    summary: str,
    apply_human_action: ApplyAction,
) -> bool:
    if not apply_human_action(engine, state, action):
        return False
    log_opponent_intake_event(state, stage, summary, {"action": action})
    remember_action(state, action)
    return True


def normalize_card_reference(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def build_card_reference_lookup(engine: Any) -> Dict[str, str]:
    cached_lookup = getattr(engine, "_cli_card_reference_lookup", None)
    if cached_lookup is not None:
        return cached_lookup

    lookup: Dict[str, str] = {}
    for card_id, card in engine.catalog.items():
        lookup[normalize_card_reference(card_id)] = card_id
        lookup[normalize_card_reference(card.get("name", card_id))] = card_id
    engine._cli_card_reference_lookup = lookup
    return lookup


def resolve_card_reference(engine: Any, reference: str) -> Optional[str]:
    normalized = normalize_card_reference(reference)
    if not normalized:
        return None
    return build_card_reference_lookup(engine).get(normalized)


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
    if normalized in {
        "P1",
        "AI",
        "OPPONENT",
        "YOUR",
        "THEIR",
        "YOURLEADER",
        "THEIRLEADER",
        "AILEADER",
        "OPPONENTLEADER",
    }:
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
        ("discard", r"^(?:i\s+)?(?:discard|discarded)\s+(.+)$"),
        ("draw", r"^(?:(.+?)\s+)?(?:draw|drew)\s+(\d+)$"),
        ("trash_top", r"^(?:(.+?)\s+)?trash(?:ed)?\s+top\s+(\d+)$"),
        ("reveal_top", r"^(?:(.+?)\s+)?reveal(?:ed)?\s+top\s+(\d+)$"),
        ("add_life", r"^(?:(.+?)\s+)?add(?:ed)?\s+(\d+)\s+life$"),
        ("set_state", r"^(?:set|made)\s+(.+?)\s+(active|rested)$"),
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
            return {"kind": "attach", "amount": amount, "target_ref": text[match.start(2):match.end(2)].strip()}
        if kind == "attack":
            target_ref = text[match.start(2):match.end(2)].strip() if match.lastindex and match.group(2) else "leader"
            return {"kind": "attack", "attacker_ref": text[match.start(1):match.end(1)].strip(), "target_ref": target_ref}
        if kind == "counter":
            return {
                "kind": "counter",
                "counter_ref": text[match.start(1):match.end(1)].strip(),
                "target_ref": text[match.start(2):match.end(2)].strip(),
            }
        if kind == "trigger":
            return {"kind": "trigger", "card_ref": text[match.start(1):match.end(1)].strip()}
        if kind == "ko":
            return {"kind": "ko", "target_ref": text[match.start(1):match.end(1)].strip()}
        if kind == "discard":
            return {"kind": "discard", "card_ref": text[match.start(1):match.end(1)].strip()}
        if kind in {"draw", "trash_top", "reveal_top", "add_life", "life"}:
            player_ref = text[match.start(1):match.end(1)].strip() if match.lastindex and match.group(1) else ""
            return {"kind": kind, "player_ref": player_ref, "amount": int(groups[1])}
        return {
            "kind": "set_state",
            "card_ref": text[match.start(1):match.end(1)].strip(),
            "new_state": groups[1],
        }
    return None


def _memory(state: Dict[str, Any]) -> Dict[str, Any]:
    session = get_active_opponent_intake(state)
    if session is None:
        session = begin_opponent_intake_session(state)
    return session.setdefault("memory", {})


def remember_action(state: Dict[str, Any], action: Dict[str, Any]) -> None:
    memory = _memory(state)
    payload = action.get("payload", {})
    if action["type"] == "attack":
        memory["last_attack"] = {
            "attacker_id": payload.get("attacker_id"),
            "target": payload.get("target"),
        }
        memory["last_attacker_id"] = payload.get("attacker_id")
        memory["last_target_id"] = payload.get("target")
        memory["last_target_was_leader"] = payload.get("target") == "leader"
    elif action["type"] == "play_card":
        memory["last_played_id"] = payload.get("card_id")
    elif action["type"] == "attach_don":
        memory["last_target_id"] = payload.get("card_id")


def remember_card_reference(
    state: Dict[str, Any],
    role: str,
    card: Optional[Dict[str, Any]],
) -> None:
    if card is None:
        return
    memory = _memory(state)
    memory["last_card_id"] = card["instance_id"]
    memory["last_card_name"] = card.get("name")
    memory[f"last_{role}_id"] = card["instance_id"]
    memory[f"last_{role}_card_name"] = card.get("name")


def _find_card_by_instance(state: Dict[str, Any], instance_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not instance_id:
        return None
    if instance_id == "leader":
        return None
    for player_id in (AI_PLAYER, HUMAN_PLAYER):
        player = state["players"][player_id]
        if player["leader"]["instance_id"] == instance_id:
            return player["leader"]
        for zone in ("board", "hand", "trash", "life_cards", "deck"):
            for card in player.get(zone, []):
                if card["instance_id"] == instance_id:
                    return card
    return None


def _split_chained_commands(command: str) -> List[str]:
    parts = re.split(r"\s*(?:;|,?\s+and then\s+|,?\s+then\s+)\s*", command.strip(), flags=re.IGNORECASE)
    return [part.strip() for part in parts if part.strip()]


def _resolve_memory_reference(state: Dict[str, Any], reference: str) -> Optional[Dict[str, Any]]:
    normalized = normalize_card_reference(reference)
    memory = _memory(state)
    alias_map = {
        "SAMEATTACKER": "last_attacker_id",
        "SAMECARD": "last_card_id",
        "SAMETARGET": "last_target_id",
        "SAMECOUNTER": "last_counter_id",
        "LASTATTACKER": "last_attacker_id",
        "LASTTARGET": "last_target_id",
        "LASTCARD": "last_card_id",
        "LASTPLAYED": "last_played_id",
        "THATONE": "last_card_id",
        "THISONE": "last_card_id",
        "THATCARD": "last_card_id",
        "THATTARGET": "last_target_id",
        "THATATTACKER": "last_attacker_id",
    }
    key = alias_map.get(normalized)
    if key is not None:
        return _find_card_by_instance(state, memory.get(key))
    return None


def _memory_points_to_leader(state: Dict[str, Any], reference: str) -> bool:
    normalized = normalize_card_reference(reference)
    memory = _memory(state)
    return normalized in {"SAMETARGET", "LASTTARGET", "THATTARGET"} and memory.get("last_target_was_leader", False)


def choose_card_from_matches(
    title: str,
    cards: List[Dict[str, Any]],
    choose_from_menu: ChooseMenu,
    card_label: CardLabel,
) -> Optional[Dict[str, Any]]:
    if not cards:
        return None
    if len(cards) == 1:
        return cards[0]
    options = [card_label(card) for card in cards]
    if len(cards) <= 3:
        title = f"{title} ({len(cards)} likely matches)"
    selection = choose_from_menu(title, options, True)
    if selection is None:
        return None
    return cards[selection]


def resolve_card_on_player(
    engine: Any,
    state: Dict[str, Any],
    player_id: str,
    reference: str,
    zones: List[str],
    choose_from_menu: ChooseMenu,
    card_label: CardLabel,
    include_leader: bool = False,
) -> Optional[Dict[str, Any]]:
    if include_leader and _memory_points_to_leader(state, reference):
        return state["players"][player_id]["leader"]

    memory_card = _resolve_memory_reference(state, reference)
    if memory_card is not None:
        return memory_card

    player = state["players"][player_id]
    memory = _memory(state)
    candidates: List[Dict[str, Any]] = []
    resolved_card_id = resolve_card_reference(engine, reference)
    normalized_reference = normalize_card_reference(reference)
    wants_other = normalized_reference.startswith("OTHER") or normalized_reference in {"OTHERONE", "THEOTHERONE"}
    used_fallback_name = False
    if normalized_reference in {"OTHERONE", "THEOTHERONE"}:
        fallback_name = (
            memory.get("last_card_name")
            or memory.get("last_target_card_name")
            or memory.get("last_attacker_card_name")
        )
        if fallback_name:
            normalized_reference = normalize_card_reference(fallback_name)
            used_fallback_name = True
    elif wants_other:
        normalized_reference = normalized_reference[5:]

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
                continue
            if normalized_reference and normalize_card_reference(card.get("name", "")) == normalized_reference:
                candidates.append(card)

    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for card in candidates:
        if card["instance_id"] in seen:
            continue
        seen.add(card["instance_id"])
        deduped.append(card)

    if wants_other:
        excluded = {
            memory.get("last_card_id"),
            memory.get("last_attacker_id"),
            memory.get("last_target_id"),
        }
        deduped = [card for card in deduped if card["instance_id"] not in excluded]

    return choose_card_from_matches(
        f"Choose matching card for '{reference}'",
        deduped,
        choose_from_menu,
        card_label,
    )


def find_legal_play_action(
    engine: Any,
    state: Dict[str, Any],
    card_ref: str,
    choose_from_menu: ChooseMenu,
    card_label: CardLabel,
) -> Optional[Dict[str, Any]]:
    resolved_card = resolve_card_on_player(
        engine,
        state,
        HUMAN_PLAYER,
        card_ref,
        ["hand"],
        choose_from_menu,
        card_label,
    )
    if resolved_card is None:
        return None
    for action in get_legal_actions(state, engine):
        if action["type"] == "play_card" and action["payload"]["card_id"] == resolved_card["instance_id"]:
            return action
    return None


def find_legal_attach_action(
    engine: Any,
    state: Dict[str, Any],
    target_ref: str,
    amount: int,
    choose_from_menu: ChooseMenu,
    card_label: CardLabel,
) -> Optional[Dict[str, Any]]:
    target_card = resolve_card_on_player(
        engine,
        state,
        HUMAN_PLAYER,
        target_ref,
        ["board"],
        choose_from_menu,
        card_label,
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
    engine: Any,
    state: Dict[str, Any],
    attacker_ref: str,
    target_ref: str,
    choose_from_menu: ChooseMenu,
    card_label: CardLabel,
) -> Optional[Dict[str, Any]]:
    attacker = resolve_card_on_player(
        engine,
        state,
        HUMAN_PLAYER,
        attacker_ref,
        ["board"],
        choose_from_menu,
        card_label,
        include_leader=True,
    )
    if attacker is None:
        return None

    target = "leader"
    if not is_leader_reference(target_ref):
        target_card = resolve_card_on_player(
            engine,
            state,
            AI_PLAYER,
            target_ref,
            ["board"],
            choose_from_menu,
            card_label,
        )
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
        target_ref = " ".join(parts[2:])
    elif len(parts) >= 3 and parts[-1].isdigit():
        target_ref = " ".join(parts[1:-1])
        amount = int(parts[-1])
    else:
        target_ref = " ".join(parts[1:])
    return target_ref, amount


def _run_manual_state_adjustment(
    engine: Any,
    state: Dict[str, Any],
    parsed: Dict[str, Any],
    command: str,
    choose_from_menu: ChooseMenu,
    card_label: CardLabel,
    printer: Printer,
) -> bool:
    kind = parsed["kind"]
    if kind == "discard":
        card = resolve_card_on_player(
            engine,
            state,
            HUMAN_PLAYER,
            parsed["card_ref"],
            ["hand"],
            choose_from_menu,
            card_label,
        )
        if card is None:
            printer(f"Could not find a hand card matching '{parsed['card_ref']}'.")
            return False
        result = engine.manual_discard(state, HUMAN_PLAYER, card["instance_id"])
        printer(f"Discarded: {result}")
        remember_card_reference(state, "card", card)
        log_opponent_intake_event(state, "manual", f"Shorthand discard report: {parsed['card_ref']}", {"result": result, "source": command})
        return True

    if kind in {"draw", "trash_top", "reveal_top", "add_life", "life"}:
        player_id = parse_shorthand_player_reference(parsed.get("player_ref", ""), AI_PLAYER if kind == "life" else HUMAN_PLAYER)
        amount = parsed["amount"]
        if kind == "draw":
            result = engine.manual_draw(state, player_id, amount)
            printer(f"Drawn: {result}")
            log_opponent_intake_event(state, "manual", f"Shorthand draw report: {player_id} drew {amount}", {"result": result, "source": command})
            return True
        if kind == "trash_top":
            result = engine.manual_trash_top(state, player_id, amount)
            printer(f"Trashed: {result}")
            log_opponent_intake_event(state, "manual", f"Shorthand trash-top report: {player_id} trashed {amount}", {"result": result, "source": command})
            return True
        if kind == "reveal_top":
            result = engine.manual_reveal_top(state, player_id, amount)
            printer(f"Revealed: {[card['instance_id'] for card in result]}")
            log_opponent_intake_event(state, "manual", f"Shorthand reveal-top report: {player_id} revealed {amount}", {"result": result, "source": command})
            return True
        if kind == "add_life":
            result = engine.manual_add_life(state, player_id, amount)
            printer(f"Added life cards: {result}")
            log_opponent_intake_event(state, "manual", f"Shorthand add-life report: {player_id} added {amount} life", {"result": result, "source": command})
            return True
        result = engine.manual_resolve_life_damage(state, player_id, amount)
        printer(f"Life resolved: {result}")
        log_opponent_intake_event(state, "battle", f"Shorthand life report: {player_id} resolved {amount} life damage", {"result": result, "source": command})
        return True

    if kind == "set_state":
        target = resolve_card_on_player(
            engine,
            state,
            HUMAN_PLAYER,
            parsed["card_ref"],
            ["board"],
            choose_from_menu,
            card_label,
            include_leader=True,
        )
        if target is None:
            target = resolve_card_on_player(
                engine,
                state,
                AI_PLAYER,
                parsed["card_ref"],
                ["board"],
                choose_from_menu,
                card_label,
                include_leader=True,
            )
        if target is None:
            printer(f"Could not find a card matching '{parsed['card_ref']}' to set state.")
            return False
        owner = AI_PLAYER if target["instance_id"].startswith("P1-") else HUMAN_PLAYER
        result = engine.manual_set_card_state(state, owner, target["instance_id"], parsed["new_state"])
        printer(f"Card state: {result}")
        remember_card_reference(state, "card", target)
        log_opponent_intake_event(state, "manual", f"Shorthand set-state report: {parsed['card_ref']} -> {parsed['new_state']}", {"result": result, "source": command})
        return True

    return False


def handle_shorthand_report(
    engine: Any,
    state: Dict[str, Any],
    command: str,
    choose_from_menu: ChooseMenu,
    apply_human_action: ApplyAction,
    card_label: CardLabel,
    action_label: ActionLabel,
    printer: Printer,
) -> bool:
    commands = _split_chained_commands(command)
    if len(commands) > 1:
        handled_any = False
        for item in commands:
            if not handle_shorthand_report(
                engine,
                state,
                item,
                choose_from_menu,
                apply_human_action,
                card_label,
                action_label,
                printer,
            ):
                return handled_any
            handled_any = True
        return handled_any

    parts = command.strip().split()
    if not parts:
        return False

    verb = parts[0].lower()
    parsed = parse_natural_shorthand(command)
    if parsed is not None:
        verb = parsed["kind"]

    if verb in {"play", "played"} and (len(parts) >= 2 or parsed is not None):
        card_ref = parsed["card_ref"] if parsed is not None and parsed["kind"] == "play" else " ".join(parts[1:])
        action = find_legal_play_action(engine, state, card_ref, choose_from_menu, card_label)
        if action is None:
            printer(f"Could not find a legal play for '{card_ref}'.")
            return False
        handled = run_logged_human_action(engine, state, action, "main", f"Shorthand play report: {card_ref.upper()}", apply_human_action)
        if handled:
            remember_card_reference(state, "card", _find_card_by_instance(state, action["payload"]["card_id"]))
        return handled

    if verb in {"attach", "attached", "don"}:
        attach_details = (
            (parsed["target_ref"], parsed["amount"])
            if parsed is not None and parsed["kind"] == "attach"
            else parse_attach_shorthand(parts)
        )
        if attach_details is None:
            return False
        target_ref, amount = attach_details
        action = find_legal_attach_action(engine, state, target_ref, amount, choose_from_menu, card_label)
        if action is None:
            printer(f"Could not find a legal DON attach for '{target_ref}' with amount {amount}.")
            return False
        handled = run_logged_human_action(engine, state, action, "main", f"Shorthand DON report: attached {amount} to {target_ref}", apply_human_action)
        if handled:
            remember_card_reference(state, "target", _find_card_by_instance(state, action["payload"]["card_id"]))
        return handled

    if verb in {"attack", "attacked", "swing", "swung"} and (len(parts) >= 2 or parsed is not None):
        if parsed is not None and parsed["kind"] == "attack" and re.search(r"\b(at|into|targeting)\b", command, flags=re.IGNORECASE):
            attacker_ref = parsed["attacker_ref"]
            target_ref = parse_target_phrase(parsed["target_ref"])
        else:
            attacker_ref = parts[1]
            target_ref = " ".join(parts[2:]) if len(parts) >= 3 else "leader"
        target_ref = target_ref or "leader"
        action = find_legal_attack_action(engine, state, attacker_ref, target_ref, choose_from_menu, card_label)
        if action is None:
            printer(f"Could not find a legal attack for '{attacker_ref}' into '{target_ref}'.")
            return False
        if not apply_human_action(engine, state, action):
            return False
        log_opponent_intake_event(state, "attack", f"Shorthand attack report: {attacker_ref} into {target_ref}", {"action": action, "source": command, "label": action_label(action)})
        remember_action(state, action)
        remember_card_reference(state, "attacker", _find_card_by_instance(state, action["payload"]["attacker_id"]))
        if action["payload"]["target"] != "leader":
            remember_card_reference(state, "target", _find_card_by_instance(state, action["payload"]["target"]))
        return True

    if verb == "counter" and (len(parts) >= 3 or parsed is not None):
        counter_ref = parsed["counter_ref"] if parsed is not None and parsed["kind"] == "counter" else parts[1]
        target_ref = parsed["target_ref"] if parsed is not None and parsed["kind"] == "counter" else " ".join(parts[2:])
        counter_card = resolve_card_on_player(engine, state, HUMAN_PLAYER, counter_ref, ["hand"], choose_from_menu, card_label)
        if counter_card is None:
            printer(f"Could not find a counter card matching '{counter_ref}' in hand.")
            return False
        target_card = resolve_card_on_player(engine, state, HUMAN_PLAYER, target_ref, ["board"], choose_from_menu, card_label, include_leader=True)
        if target_card is None:
            printer(f"Could not find a counter target matching '{target_ref}'.")
            return False
        result = engine.manual_use_counter(state, HUMAN_PLAYER, counter_card["instance_id"], target_card["instance_id"])
        printer(f"Counter: {result}")
        remember_card_reference(state, "counter", counter_card)
        remember_card_reference(state, "target", target_card)
        _memory(state)["last_counter_id"] = counter_card["instance_id"]
        _memory(state)["last_card_id"] = counter_card["instance_id"]
        log_opponent_intake_event(state, "battle", f"Shorthand counter report: {counter_ref} on {target_ref}", {"result": result, "source": command})
        return True

    if verb == "trigger" and (len(parts) >= 2 or parsed is not None):
        trigger_ref = parsed["card_ref"] if parsed is not None and parsed["kind"] == "trigger" else " ".join(parts[1:])
        trigger_card = resolve_card_on_player(engine, state, HUMAN_PLAYER, trigger_ref, ["life_cards"], choose_from_menu, card_label)
        if trigger_card is None:
            printer(f"Could not find a life card matching '{trigger_ref}'.")
            return False
        result = engine.manual_activate_trigger(state, HUMAN_PLAYER, trigger_card["instance_id"])
        printer(f"Trigger: {result}")
        remember_card_reference(state, "card", trigger_card)
        log_opponent_intake_event(state, "battle", f"Shorthand trigger report: {trigger_ref}", {"result": result, "source": command})
        return True

    if verb == "ko" and (len(parts) >= 2 or parsed is not None):
        target_ref = parsed["target_ref"] if parsed is not None and parsed["kind"] == "ko" else " ".join(parts[1:])
        target_card = resolve_card_on_player(engine, state, AI_PLAYER, target_ref, ["board"], choose_from_menu, card_label)
        if target_card is None:
            target_card = resolve_card_on_player(engine, state, HUMAN_PLAYER, target_ref, ["board"], choose_from_menu, card_label)
        if target_card is None:
            printer(f"Could not find a board card matching '{target_ref}' to K.O.")
            return False
        owner = AI_PLAYER if target_card["instance_id"].startswith("P1-") else HUMAN_PLAYER
        result = engine.manual_ko(state, owner, target_card["instance_id"])
        printer(f"KO: {result}")
        remember_card_reference(state, "card", target_card)
        log_opponent_intake_event(state, "battle", f"Shorthand KO report: {target_ref}", {"result": result, "source": command})
        return True

    if parsed is not None and _run_manual_state_adjustment(engine, state, parsed, command, choose_from_menu, card_label, printer):
        return True

    return False
