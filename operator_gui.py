import argparse
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional, Tuple

from cli_game import (
    action_label,
    begin_opponent_intake_session,
    build_local_planning_agent,
    card_label,
    format_battle_context_lines,
    finish_opponent_intake_session,
    get_last_battle_context,
    load_state,
    log_opponent_intake_event,
    parse_command_to_action,
    run_manual_command,
    save_state,
    start_manual_turn,
)
from cli_intake import handle_shorthand_report as intake_handle_shorthand_report
from glat_engine import GLATEngine, InvalidActionError


AI_PLAYER = "P1"
HUMAN_PLAYER = "P2"
SUPPORTED_AUTOMATIC_EFFECT_IDS = {
    "EB03-042",
    "EB03-053",
    "OP06-115",
    "OP10-109",
    "OP12-086",
    "OP12-087",
    "OP12-089",
    "OP12-094",
    "OP12-097",
    "OP12-098",
    "OP12-112",
    "OP12-119",
    "OP14-108",
}


def format_summary_lines(state: Dict[str, Any]) -> List[str]:
    lines = [
        f"Turn {state['turn']} | Active: {state['active_player']} | Phase: {state['phase']} | Winner: {state['winner'] or '-'}"
    ]
    for player_id in (AI_PLAYER, HUMAN_PLAYER):
        player = state["players"][player_id]
        label = "AI" if player_id == AI_PLAYER else "Human"
        attached = sum(player["attached_don"].values())
        lines.append(
            f"{player_id} ({label}) | Life {player['life']} | Hand {len(player['hand'])} | "
            f"Board {len(player['board'])} | Deck {len(player['deck'])} | Trash {len(player['trash'])} | "
            f"DON area {len(player['don_area'])} | Attached DON {attached}"
        )
    return lines


def collect_recent_log_lines(state: Dict[str, Any], count: int = 12) -> List[str]:
    logs = state.get("logs", [])[-count:]
    return [
        f"Turn {log['turn']} {log['player']} {log['action']['type']}: {log['result']}"
        for log in logs
    ]


def summarize_action_result(result: Dict[str, Any]) -> str:
    if result.get("ended_turn"):
        return "ended turn"
    if result.get("played"):
        parts = [f"played {result['played']}"]
        if result.get("destination"):
            parts.append(f"to {result['destination']}")
        if result.get("paid_cost") is not None:
            parts.append(f"paid {result['paid_cost']}")
        if result.get("effect_result"):
            effect = result["effect_result"].get("effect", "effect")
            skipped = result["effect_result"].get("skipped")
            parts.append(f"{effect} skipped: {skipped}" if skipped else f"{effect} resolved")
        return ", ".join(parts)
    if result.get("attached_to"):
        return f"attached {result.get('amount', 0)} DON to {result['attached_to']}"
    if result.get("target"):
        parts = [f"attacked {result['target']}"]
        if result.get("final_target") and result["final_target"] != result["target"]:
            parts.append(f"redirected to {result['final_target']}")
        if result.get("attacker_power") is not None and result.get("defender_power") is not None:
            parts.append(f"{result['attacker_power']} vs {result['defender_power']}")
        if result.get("life_after") is not None:
            parts.append(f"life now {result['life_after']}")
        if result.get("ko") is not None:
            parts.append("K.O." if result["ko"] else "no K.O.")
        if result.get("blocked_or_countered"):
            parts.append("blocked/countered")
        return ", ".join(parts)
    text = str(result)
    return text if len(text) <= 180 else f"{text[:177]}..."


def summarize_action_log_entry(log: Dict[str, Any]) -> str:
    return f"{action_label(log['action'])}: {summarize_action_result(log.get('result', {}))}"


def append_console_entry(
    state: Dict[str, Any],
    speaker: str,
    message: str,
    kind: str = "system",
    command: Optional[str] = None,
) -> Dict[str, Any]:
    entry = {
        "index": len(state.setdefault("command_console", [])) + 1,
        "turn": state.get("turn"),
        "phase": state.get("phase"),
        "speaker": speaker,
        "kind": kind,
        "message": message,
    }
    if command is not None:
        entry["command"] = command
    state["command_console"].append(entry)
    return entry


def format_console_lines(state: Dict[str, Any], count: int = 24) -> List[str]:
    entries = state.get("command_console", [])[-count:]
    pending = state.get("pending_console_prompt")
    if not entries and not pending:
        return ["No console messages yet."]
    lines = []
    for entry in entries:
        speaker = entry.get("speaker", "System")
        message = entry.get("message", "")
        turn = entry.get("turn", "-")
        lines.append(f"#{entry.get('index')} | T{turn} | {speaker}: {message}")
    if pending:
        lines.append(f"PENDING | {_pending_effect_prompt_message(pending)}")
    return lines


def _extract_physical_play_card_id(command: str) -> Optional[str]:
    report = _extract_physical_play_report(command)
    return report["card_id"] if report is not None else None


def _extract_physical_play_report(command: str) -> Optional[Dict[str, str]]:
    match = re.match(
        r"^(?:i\s+)?(?:play|played)\s+(?:(rested)\s+)?([A-Za-z]{2,4}\d{2}-\d{3})(?:\s+(rested))?\s*$",
        command.strip(),
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    initial_state = "rested" if match.group(1) or match.group(3) else "active"
    return {"card_id": match.group(2).upper(), "state": initial_state}


def _card_has_unresolved_text(card_data: Dict[str, Any]) -> bool:
    return bool(card_data.get("effect") or card_data.get("trigger"))


def _unsupported_effect_message(card_id: str) -> str:
    return (
        f"{card_id} has rules text that was not auto-resolved. "
        "Resolve it manually, then type 'manual done'; or type 'skip', 'note <text>', or 'implement later'."
    )


def _pending_effect_prompt_message(pending: Dict[str, Any]) -> str:
    card_id = pending.get("card_id", "the card")
    return (
        f"Unsupported effect for {card_id}. "
        "Options: skip, manual done, note <text>, implement later."
    )


def _record_unsupported_effect_resolution(
    state: Dict[str, Any],
    pending: Dict[str, Any],
    resolution: str,
    note: Optional[str] = None,
) -> None:
    entry = {
        "turn": state.get("turn"),
        "phase": state.get("phase"),
        "card_id": pending.get("card_id"),
        "instance_id": pending.get("instance_id"),
        "resolution": resolution,
    }
    if note is not None:
        entry["note"] = note
    state.setdefault("unsupported_effect_resolutions", []).append(entry)


def _handle_pending_console_prompt(
    state: Dict[str, Any],
    command: str,
) -> Optional[Tuple[bool, str]]:
    pending = state.get("pending_console_prompt")
    if not pending:
        return None

    raw = command.strip()
    normalized = raw.lower()
    if normalized in {"pending", "effect", "effect status", "options"}:
        return False, _pending_effect_prompt_message(pending)

    if normalized in {"skip", "skip effect"}:
        card_id = pending.get("card_id", "the card")
        _record_unsupported_effect_resolution(state, pending, "skipped")
        state["pending_console_prompt"] = None
        return True, f"Skipped pending effect for {card_id}."

    if normalized in {"manual done", "manual resolved", "effect done", "resolved", "done"}:
        card_id = pending.get("card_id", "the card")
        _record_unsupported_effect_resolution(state, pending, "manual_resolved")
        state["pending_console_prompt"] = None
        return True, f"Marked unsupported effect for {card_id} as manually resolved."

    if normalized in {"implement later", "later", "backlog"}:
        card_id = pending.get("card_id", "the card")
        _record_unsupported_effect_resolution(state, pending, "implement_later")
        state.setdefault("unsupported_effect_backlog", []).append(
            {
                "turn": state.get("turn"),
                "phase": state.get("phase"),
                "card_id": pending.get("card_id"),
                "instance_id": pending.get("instance_id"),
            }
        )
        state["pending_console_prompt"] = None
        return True, f"Recorded {card_id} for later effect implementation."

    if normalized in {"manual", "manual resolve", "resolve manually"}:
        return (
            False,
            "Use correction/manual commands to update the state, then type 'manual done' to clear the pending effect.",
        )

    if normalized.startswith("note "):
        note_text = raw[5:].strip()
        if not note_text:
            return False, "Enter note text after 'note'."
        state.setdefault("operator_notes", []).append(
            {
                "turn": state.get("turn"),
                "phase": state.get("phase"),
                "prompt": dict(pending),
                "note": note_text,
            }
        )
        card_id = pending.get("card_id", "the card")
        _record_unsupported_effect_resolution(state, pending, "note_only", note=note_text)
        state["pending_console_prompt"] = None
        return True, f"Recorded note for {card_id}."

    return None


def apply_physical_reported_play(
    engine: GLATEngine,
    state: Dict[str, Any],
    card_id: str,
    initial_state: str = "active",
) -> Tuple[bool, str]:
    if state.get("match_mode") != "physical_reported":
        return False, "Physical reported plays are only available in physical_reported mode."
    if initial_state not in {"active", "rested"}:
        return False, "Physical reported plays can only start active or rested."

    card_data = engine.lookup_card_data(card_id)
    if card_data is None:
        return False, f"Unknown card id: {card_id}"
    if card_data.get("category") == "Leader":
        return False, "Leader cards cannot be played into the physical report zones."

    player = state["players"][HUMAN_PLAYER]
    if card_data.get("category") == "Character" and len(player["board"]) >= 5:
        return False, "Human character area is full."

    before_snapshot = engine._snapshot_state_for_replay(state)
    card = engine.build_card_instance(HUMAN_PLAYER, card_id)
    card["played_turn"] = state.get("turn")
    card["state"] = initial_state

    destination = "board" if card.get("category") == "Character" else "trash"
    if destination == "board":
        player["board"].append(card)
    else:
        player["trash"].append(card)

    unsupported_effect = (
        _card_has_unresolved_text(card_data)
        and card["card_id"] not in SUPPORTED_AUTOMATIC_EFFECT_IDS
    )
    result: Dict[str, Any] = {
        "reported_play": card["card_id"],
        "destination": destination,
        "mode": "physical_reported",
        "state": card.get("state"),
        "cost_handled_by": "physical_table",
        "effect_status": "unsupported_manual_required" if unsupported_effect else "no_auto_prompt",
    }
    if unsupported_effect:
        result["manual_effect_options"] = ["skip", "manual done", "note <text>", "implement later"]
        state["pending_console_prompt"] = {
            "type": "unsupported_effect",
            "card_id": card["card_id"],
            "instance_id": card["instance_id"],
            "options": result["manual_effect_options"],
        }

    engine.validate_state(state)
    engine.log_action(
        state,
        HUMAN_PLAYER,
        {"type": "physical_reported_play", "payload": {"card_id": card["card_id"]}},
        result,
        before_snapshot=before_snapshot,
    )
    message = f"Recorded physical play: {card['card_id']} to {destination} {card.get('state', 'active')}."
    if unsupported_effect:
        message = f"{message} {_unsupported_effect_message(card['card_id'])}"
    return True, message


def collect_replay_log_lines(state: Dict[str, Any], count: Optional[int] = None) -> List[str]:
    entries = state.get("replay_log", [])
    if count is not None:
        entries = entries[-count:]
    if not entries:
        return ["No replay entries yet."]
    return [
        (
            f"#{entry['index']} | Turn {entry['turn']} {entry['player']} "
            f"{entry['action']['type']} | "
            f"{', '.join(entry.get('diff_lines', [])[:2]) or 'no visible state change'}"
        )
        for entry in entries
    ]


def collect_latest_diff_lines(state: Dict[str, Any]) -> List[str]:
    entries = state.get("replay_log", [])
    if not entries:
        return ["No replay diff yet."]
    latest = entries[-1]
    header = (
        f"Step {latest['index']} | Turn {latest['turn']} | "
        f"{latest['player']} {latest['action']['type']}"
    )
    return [header, *(latest.get("diff_lines") or ["No visible state changes recorded."])]


def collect_replay_diff_lines(state: Dict[str, Any], position: Optional[int] = None) -> List[str]:
    entries = state.get("replay_log", [])
    if not entries:
        return ["No replay diff yet."]
    if position is None:
        return collect_latest_diff_lines(state)
    position = max(0, min(position, len(entries) - 1))
    entry = entries[position]
    header = (
        f"Step {entry['index']} | Turn {entry['turn']} | "
        f"{entry['player']} {entry['action']['type']}"
    )
    return [header, *(entry.get("diff_lines") or ["No visible state changes recorded."])]


def collect_ai_debug_lines(state: Dict[str, Any], count: int = 2) -> List[str]:
    entries = state.get("ai_debug_history", [])[-count:]
    if not entries:
        return ["No AI debug history yet."]
    lines: List[str] = []
    for entry in entries:
        lines.append(
            f"Turn {entry['turn']} {entry['player']} | legal {len(entry.get('legal_actions', []))} | "
            f"planned {entry.get('planned_indices', [])} | fallback_end_turn={entry.get('fallback_end_turn', False)}"
        )
        for scored in entry.get("scored_actions", [])[:3]:
            action = scored.get("action", {})
            lines.append(
                f"  score {scored.get('score', '-')}: {action.get('type', '-')} "
                f"#{scored.get('index', '-')}"
            )
        for executed in entry.get("executed_actions", []):
            action = executed.get("action", {})
            lines.append(f"  {executed.get('status', 'unknown')}: {action.get('type', '-')}")
    return lines


def collect_intake_log_lines(state: Dict[str, Any]) -> List[str]:
    context = state.get("cli_context", {})
    history = context.get("opponent_turn_history", [])
    if not history:
        return ["No opponent intake history yet."]
    session = history[-1]
    header = (
        f"Turn {session['turn']} | Phase {session.get('phase', '-')} | "
        f"Status {session.get('status', '-')}"
    )
    lines = [header]
    for event in session.get("events", [])[-12:]:
        lines.append(f"{event['index']}. [{event['stage']}] {event['summary']}")
    return lines


def collect_battle_trace_lines(state: Dict[str, Any]) -> List[str]:
    live_context = state.get("battle_context")
    if live_context:
        return format_battle_context_lines(live_context)
    last_context = get_last_battle_context(state)
    if last_context:
        return format_battle_context_lines(last_context)
    return ["No battle trace yet."]


def card_tile_lines(card: Dict[str, Any]) -> List[str]:
    if card.get("face_down"):
        label = card.get("name") or "Face-down card"
        return [card.get("card_id", "Hidden"), label, card.get("state", "hidden")]

    card_id = card.get("card_id", "?")
    name = card.get("name") or card_id
    if len(name) > 24:
        name = f"{name[:21]}..."
    lines = [card_id, name]
    details: List[str] = []
    if card.get("cost") is not None:
        details.append(f"cost {card.get('cost')}")
    if card.get("power") is not None:
        details.append(f"pow {card.get('power')}")
    if details:
        lines.append(" | ".join(details))
    state = card.get("state")
    attached = card.get("attached_don", 0)
    if state or attached:
        lines.append(f"{state or 'active'} | DON {attached}")
    return lines


def card_detail_lines(
    card: Optional[Dict[str, Any]],
    player_id: Optional[str] = None,
    zone: Optional[str] = None,
) -> List[str]:
    if not card:
        return ["Select a card to inspect it."]
    if card.get("face_down"):
        lines = []
        if player_id or zone:
            lines.append(f"Location: {player_id or '-'} / {zone or '-'}")
        lines.extend(
            [
                "card: Face-down life card",
                "details: Hidden until revealed by damage or an effect.",
            ]
        )
        return lines
    lines = []
    if player_id or zone:
        lines.append(f"Location: {player_id or '-'} / {zone or '-'}")
    for key in (
        "card_id",
        "name",
        "instance_id",
        "category",
        "cost",
        "base_cost",
        "power",
        "counter",
        "state",
        "attached_don",
        "played_turn",
    ):
        if key in card:
            lines.append(f"{key}: {card[key]}")
    if card.get("types"):
        lines.append(f"types: {', '.join(card['types'])}")
    return lines


def replay_snapshot_to_display_state(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    players: Dict[str, Any] = {}
    for player_id, player in snapshot.get("players", {}).items():
        hand_cards = [
            {
                "instance_id": f"{player_id}-HAND-{index + 1:02d}",
                "card_id": card_id,
                "name": card_id,
                "state": "known",
            }
            for index, card_id in enumerate(player.get("hand_cards", []))
        ]
        life_cards = build_hidden_life_cards(player_id, player.get("life_cards_count", len(player.get("life_cards", []))))
        trash_cards = [
            {
                "instance_id": f"{player_id}-TRASH-{index + 1:02d}",
                "card_id": card_id,
                "name": card_id,
                "state": "trash",
            }
            for index, card_id in enumerate(player.get("trash_cards", []))
        ]
        leader = dict(player.get("leader", {}))
        leader.setdefault("name", leader.get("card_id", "Leader"))
        players[player_id] = {
            "life": player.get("life", player.get("life_cards_count", 0)),
            "deck": [{"card_id": "DECK", "instance_id": f"{player_id}-DECK"}]
            * player.get("deck_count", 0),
            "hand": hand_cards,
            "board": [dict(card) for card in player.get("board", [])],
            "trash": trash_cards,
            "life_cards": life_cards,
            "don_deck": ["DON"] * max(0, 10 - player.get("don_area_count", 0) - player.get("spent_don_count", 0) - player.get("attached_don_total", 0)),
            "don_area": ["DON"] * player.get("don_area_count", 0),
            "spent_don": ["DON"] * player.get("spent_don_count", 0),
            "attached_don": dict(player.get("attached_don", {})),
            "leader": leader,
            "turn_flags": {},
        }
    return {
        "turn": snapshot.get("turn", "-"),
        "match_mode": snapshot.get("match_mode", "digital_strict"),
        "active_player": snapshot.get("active_player", "-"),
        "phase": snapshot.get("phase", "-"),
        "winner": snapshot.get("winner"),
        "players": players,
        "logs": [],
        "replay_log": [],
        "ai_debug_history": [],
        "battle_context": None,
    }


def replay_entry_label(entry: Dict[str, Any]) -> str:
    action = entry.get("action", {})
    return (
        f"Replay #{entry.get('index')} | Turn {entry.get('turn')} | "
        f"{entry.get('player')} {action.get('type', '-')}"
    )


def build_hidden_life_cards(player_id: str, count: int) -> List[Dict[str, Any]]:
    return [
        {
            "instance_id": f"{player_id}-LIFE-{index + 1:02d}",
            "card_id": "Life",
            "name": f"Face-down Life {index + 1}",
            "state": "face down",
            "face_down": True,
        }
        for index in range(count)
    ]


def ensure_human_turn_ready(engine: GLATEngine, state: Dict[str, Any]) -> Tuple[bool, str]:
    if state["active_player"] != HUMAN_PLAYER:
        return False, "It is not the human/opponent turn."
    if state["phase"] != "main":
        start_manual_turn(engine, state)
        begin_opponent_intake_session(state)
        log_opponent_intake_event(
            state,
            "turn_start",
            "Started operator GUI opponent turn intake after refresh/draw/DON.",
            {
                "turn": state["turn"],
                "active_player": state["active_player"],
                "phase": state["phase"],
            },
        )
        return True, "Human turn prepared for opponent intake."
    return False, "Human turn already in main phase."


def _normalize_player_id(value: str) -> Optional[str]:
    normalized = value.strip().upper()
    if normalized in {"P1", "AI", "OPPONENT"}:
        return AI_PLAYER
    if normalized in {"P2", "HUMAN", "PLAYER", "ME"}:
        return HUMAN_PLAYER
    return None


def _normalize_zone(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "life": "life_cards",
        "lives": "life_cards",
        "life_card": "life_cards",
        "life_cards": "life_cards",
        "characters": "board",
        "character": "board",
        "field": "board",
        "board": "board",
        "hand": "hand",
        "deck": "deck",
        "trash": "trash",
        "discard": "trash",
    }
    return aliases.get(normalized, normalized)


def _parse_power_delta(value: str) -> Optional[int]:
    normalized = value.strip().lower()
    match = re.fullmatch(r"([+-])\s*(\d+)", normalized)
    if match:
        amount = int(match.group(2))
        return amount if match.group(1) == "+" else -amount
    match = re.fullmatch(r"(?:add|plus)\s+(\d+)", normalized)
    if match:
        return int(match.group(1))
    match = re.fullmatch(r"(?:remove|minus)\s+(\d+)", normalized)
    if match:
        return -int(match.group(1))
    return None


def _card_ref_matches(card: Dict[str, Any], reference: str) -> bool:
    normalized = reference.strip().upper()
    return (
        card.get("instance_id", "").upper() == normalized
        or card.get("card_id", "").upper() == normalized
        or card.get("name", "").upper() == normalized
    )


def _resolve_correction_card(
    state: Dict[str, Any],
    reference: str,
    player_id: Optional[str] = None,
    zone: Optional[str] = None,
    include_leader: bool = False,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str], str]:
    zones = [_normalize_zone(zone)] if zone else ["board", "hand", "trash", "life_cards", "deck"]
    players = [player_id] if player_id else [HUMAN_PLAYER, AI_PLAYER]
    matches: List[Tuple[Dict[str, Any], str, str]] = []

    for candidate_player in players:
        player = state["players"][candidate_player]
        if include_leader and _card_ref_matches(player["leader"], reference):
            matches.append((player["leader"], candidate_player, "leader"))
        for candidate_zone in zones:
            for card in player.get(candidate_zone, []):
                if _card_ref_matches(card, reference):
                    matches.append((card, candidate_player, candidate_zone))

    if not matches:
        return None, None, None, f"Could not find card matching '{reference}'."
    if len(matches) > 1:
        labels = [
            _format_correction_card_match(card, found_player, found_zone)
            for card, found_player, found_zone in matches[:5]
        ]
        return (
            None,
            None,
            None,
            f"Ambiguous card reference '{reference}': {'; '.join(labels)}. Use the instance id, or try 'find {reference}'.",
        )
    card, found_player, found_zone = matches[0]
    return card, found_player, found_zone, ""


def _find_correction_card_matches(
    state: Dict[str, Any],
    reference: str,
    player_id: Optional[str] = None,
    zone: Optional[str] = None,
    include_leader: bool = True,
) -> List[Tuple[Dict[str, Any], str, str]]:
    zones = [_normalize_zone(zone)] if zone else ["board", "hand", "trash", "life_cards", "deck"]
    players = [player_id] if player_id else [HUMAN_PLAYER, AI_PLAYER]
    matches: List[Tuple[Dict[str, Any], str, str]] = []
    for candidate_player in players:
        player = state["players"][candidate_player]
        if include_leader and _card_ref_matches(player["leader"], reference):
            matches.append((player["leader"], candidate_player, "leader"))
        for candidate_zone in zones:
            for card in player.get(candidate_zone, []):
                if _card_ref_matches(card, reference):
                    matches.append((card, candidate_player, candidate_zone))
    return matches


def _format_correction_card_match(card: Dict[str, Any], player_id: str, zone: str) -> str:
    return (
        f"{player_id}/{zone}/{card.get('instance_id')} "
        f"{card.get('card_id')} {card.get('name', '')}".strip()
    )


def _new_hidden_life_correction_card(player_id: str, index: int) -> Dict[str, Any]:
    return {
        "instance_id": f"{player_id}-LIFE-CORRECTION-{index:03d}",
        "card_id": "Life",
        "name": "Face-down Life",
        "category": "Unknown",
        "cost": 0,
        "base_cost": 0,
        "power": 0,
        "counter": None,
        "state": "face down",
        "played_turn": None,
        "battle_power_bonus": 0,
        "temporary_cost_bonus": 0,
        "temporary_cost_bonus_expires": None,
        "face_down": True,
    }


def _record_correction(
    state: Dict[str, Any],
    command: str,
    result: Dict[str, Any],
) -> None:
    state.setdefault("operator_corrections", []).append(
        {
            "turn": state.get("turn"),
            "phase": state.get("phase"),
            "command": command,
            "result": result,
        }
    )


def _silent_menu_choice(prompt: str, labels: List[str], allow_back: bool = False) -> Optional[int]:
    return None if allow_back or len(labels) != 1 else 0


def apply_operator_action(
    engine: GLATEngine,
    state: Dict[str, Any],
    action: Dict[str, Any],
) -> bool:
    if not engine.is_valid_action(state, action):
        return False
    try:
        engine.apply_action(state, action)
    except InvalidActionError:
        return False
    return True


def handle_operator_shorthand_report(
    engine: GLATEngine,
    state: Dict[str, Any],
    command: str,
) -> bool:
    return intake_handle_shorthand_report(
        engine,
        state,
        command,
        _silent_menu_choice,
        apply_operator_action,
        card_label,
        action_label,
        lambda _message: None,
    )


def _correct_life_total(
    engine: GLATEngine,
    state: Dict[str, Any],
    player_id: str,
    target_life: int,
    command: str,
) -> Tuple[bool, str]:
    if target_life < 0:
        return False, "Life total cannot be negative."
    player = state["players"][player_id]
    before_life = player["life"]
    before_snapshot = engine._snapshot_state_for_replay(state)

    while len(player["life_cards"]) > target_life:
        player["life_cards"].pop(0)
    next_index = len(player["life_cards"]) + 1
    existing_ids = {
        card["instance_id"]
        for candidate in state["players"].values()
        for zone in ("board", "hand", "deck", "trash", "life_cards")
        for card in candidate.get(zone, [])
    }
    while len(player["life_cards"]) < target_life:
        while f"{player_id}-LIFE-CORRECTION-{next_index:03d}" in existing_ids:
            next_index += 1
        card = _new_hidden_life_correction_card(player_id, next_index)
        existing_ids.add(card["instance_id"])
        player["life_cards"].append(card)
        next_index += 1
    player["life"] = target_life

    result = {"player": player_id, "from": before_life, "to": target_life}
    engine.validate_state(state)
    engine.log_action(
        state,
        player_id,
        {"type": "operator_correction_life", "payload": {"life": target_life}},
        result,
        before_snapshot=before_snapshot,
    )
    _record_correction(state, command, result)
    return True, f"Correction recorded: {player_id} life {before_life} -> {target_life}."


def process_correction_command(
    engine: GLATEngine,
    state: Dict[str, Any],
    command: str,
) -> Optional[Tuple[bool, str]]:
    raw = command.strip()
    parts = raw.split()
    if not parts:
        return None
    verb = parts[0].lower()

    if verb in {"find", "where"} and len(parts) in (2, 3, 4):
        reference = parts[1]
        player_id = None
        zone = None
        for token in parts[2:]:
            normalized_player = _normalize_player_id(token)
            if normalized_player is not None:
                player_id = normalized_player
            else:
                zone = _normalize_zone(token)
        matches = _find_correction_card_matches(
            state,
            reference,
            player_id=player_id,
            zone=zone,
            include_leader=True,
        )
        if not matches:
            return False, f"No tracked cards match '{reference}'."
        labels = [
            _format_correction_card_match(card, found_player, found_zone)
            for card, found_player, found_zone in matches[:8]
        ]
        suffix = "" if len(matches) <= 8 else f"; +{len(matches) - 8} more"
        return False, f"Matches for {reference}: {'; '.join(labels)}{suffix}"

    if verb == "correct" and len(parts) == 4 and parts[1].lower() == "life":
        player_id = _normalize_player_id(parts[2])
        if player_id is None:
            return False, "Use P1/AI or P2/Human for life correction."
        try:
            target_life = int(parts[3])
        except ValueError:
            return False, "Life correction needs a whole number."
        return _correct_life_total(engine, state, player_id, target_life, raw)

    if verb in {"power", "pow"} and len(parts) == 4:
        player_id = _normalize_player_id(parts[1])
        if player_id is None:
            return False, "Use P1/AI or P2/Human for power adjustment."
        target_token = parts[2].lower()
        if target_token in {"leader", "lead"}:
            target = "leader"
        elif _normalize_zone(target_token) == "board":
            target = "board"
        else:
            return False, "Power target must be leader or board."
        amount = _parse_power_delta(parts[3])
        if amount is None:
            return False, "Power adjustment needs +1000 or -1000."
        result = engine.manual_adjust_power(state, player_id, target, amount)
        _record_correction(state, raw, result)
        sign = "+" if amount > 0 else ""
        return True, f"Correction recorded: {player_id} {target} power {sign}{amount}."

    if verb == "remove" and len(parts) in (2, 3, 4):
        player_id = None
        zone = None
        for token in parts[2:]:
            normalized_player = _normalize_player_id(token)
            if normalized_player is not None:
                player_id = normalized_player
            else:
                zone = _normalize_zone(token)
        card, found_player, found_zone, message = _resolve_correction_card(
            state,
            parts[1],
            player_id=player_id,
            zone=zone,
            include_leader=False,
        )
        if card is None or found_player is None or found_zone is None:
            return False, message
        if found_zone == "trash":
            return False, f"{card['card_id']} is already in trash."
        result = engine.manual_move_card(
            state,
            found_player,
            card["instance_id"],
            found_zone,
            "trash",
            position="bottom",
        )
        _record_correction(state, raw, result)
        return True, f"Correction recorded: moved {card['card_id']} from {found_zone} to trash."

    if verb == "move" and len(parts) in (4, 5, 6):
        source_zone = _normalize_zone(parts[2])
        destination_zone = _normalize_zone(parts[3])
        position = "bottom"
        player_id = None
        for token in parts[4:]:
            normalized_player = _normalize_player_id(token)
            if normalized_player is not None:
                player_id = normalized_player
            elif token.lower() in {"top", "bottom"}:
                position = token.lower()
            else:
                return False, f"Unknown move option: {token}"
        card, found_player, found_zone, message = _resolve_correction_card(
            state,
            parts[1],
            player_id=player_id,
            zone=source_zone,
            include_leader=False,
        )
        if card is None or found_player is None:
            return False, message
        result = engine.manual_move_card(
            state,
            found_player,
            card["instance_id"],
            found_zone or source_zone,
            destination_zone,
            position=position,
        )
        _record_correction(state, raw, result)
        return True, f"Correction recorded: moved {card['card_id']} from {source_zone} to {destination_zone}."

    if verb in {"set", "correct"} and len(parts) in (3, 4):
        if verb == "correct" and len(parts) == 4 and parts[1].lower() == "state":
            reference = parts[2]
            new_state = parts[3].lower()
        elif verb == "set" and len(parts) == 3:
            reference = parts[1]
            new_state = parts[2].lower()
        else:
            return None
        card, found_player, _found_zone, message = _resolve_correction_card(
            state,
            reference,
            zone="board",
            include_leader=True,
        )
        if card is None or found_player is None:
            return False, message
        result = engine.manual_set_card_state(state, found_player, card["instance_id"], new_state)
        _record_correction(state, raw, result)
        return True, f"Correction recorded: set {card['card_id']} to {new_state}."

    return None


def process_operator_command(
    engine: GLATEngine,
    state: Dict[str, Any],
    command: str,
) -> Tuple[bool, str]:
    raw = command.strip()
    if not raw:
        return False, "Enter a command first."

    pending_result = _handle_pending_console_prompt(state, raw)
    if pending_result is not None:
        return pending_result

    physical_play_report = _extract_physical_play_report(raw)
    if physical_play_report is not None and state.get("match_mode") == "physical_reported":
        return apply_physical_reported_play(
            engine,
            state,
            physical_play_report["card_id"],
            physical_play_report["state"],
        )

    correction_result = process_correction_command(engine, state, raw)
    if correction_result is not None:
        return correction_result

    if handle_operator_shorthand_report(engine, state, raw):
        return True, f"Applied shorthand: {raw}"

    if run_manual_command(engine, state, raw):
        return True, f"Applied manual command: {raw}"

    action = parse_command_to_action(raw)
    if action and apply_operator_action(engine, state, action):
        if action["type"] == "end_turn":
            engine.end_phase(state)
            finish_opponent_intake_session(state, "completed")
        return True, f"Applied action: {action_label(action)}"

    if physical_play_report is not None:
        return apply_physical_reported_play(
            engine,
            state,
            physical_play_report["card_id"],
            physical_play_report["state"],
        )

    return False, f"Could not apply command: {raw}"


def process_console_command(
    engine: GLATEngine,
    state: Dict[str, Any],
    command: str,
) -> Tuple[bool, str]:
    raw = command.strip()
    if not raw:
        message = "Enter a command first."
        append_console_entry(state, "System", message, kind="warning")
        return False, message

    append_console_entry(state, "You", raw, kind="command", command=raw)
    changed, message = process_operator_command(engine, state, raw)
    append_console_entry(
        state,
        "System",
        message,
        kind="success" if changed else "warning",
        command=raw,
    )
    return changed, message


class OperatorGUI(tk.Tk):
    def __init__(
        self,
        state_path: str,
        use_fake_ai: bool = False,
        ai_mode: str = "gemini",
        seed: int = 7,
        match_mode: str = "digital_strict",
    ) -> None:
        super().__init__()
        self.title("GLAT Operator Panel")
        self.geometry("1480x920")
        self.minsize(1240, 780)

        self.state_path = Path(state_path)
        agent = build_local_planning_agent(ai_mode, use_fake_ai=use_fake_ai)
        self.engine = GLATEngine(
            agent=agent,
            effect_choice_provider=self.gui_effect_choice,
            defense_choice_provider=self.gui_defense_choice,
            trigger_choice_provider=self.gui_trigger_choice,
        )
        if self.state_path.exists():
            self.state = load_state(self.engine, str(self.state_path))
        else:
            self.state = self.engine.create_initial_state(seed=seed, match_mode=match_mode)
            append_console_entry(
                self.state,
                "System",
                f"Started {self.state['match_mode']} match.",
                kind="system",
            )
            save_state(self.engine, self.state, str(self.state_path))

        self.status_var = tk.StringVar(value="Ready")
        self.command_var = tk.StringVar()
        self.console_popup_command_var = tk.StringVar()
        self.console_window: Optional[tk.Toplevel] = None
        self.console_text: Optional[tk.Text] = None
        self.replay_position: Optional[int] = None
        self.replay_side = "after"
        self.selected_card: Optional[Dict[str, Any]] = None
        self.selected_location = ("-", "-")
        self.zone_frames: Dict[str, ttk.Frame] = {}
        self.zone_canvases: Dict[str, tk.Canvas] = {}

        self._build_layout()
        self.refresh_view()

    def _build_layout(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=7)
        root.columnconfigure(1, weight=3)
        root.rowconfigure(1, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="GLAT Table Operator", font=("Georgia", 18, "bold")).grid(row=0, column=0, sticky="w")
        self.header_label = ttk.Label(header, text="")
        self.header_label.grid(row=0, column=1, sticky="e")

        table = ttk.Frame(root)
        table.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)

        right = ttk.Frame(root)
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)
        right.rowconfigure(4, weight=1)

        self._build_table(table)
        self._build_controls(right)
        self._build_debug_panels(right)
        self._build_card_details(right)

        footer = ttk.Label(root, textvariable=self.status_var, anchor="w")
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))

    def _build_debug_panels(self, parent: ttk.Frame) -> None:
        debug_frame = ttk.LabelFrame(parent, text="Debug Panels", padding=10)
        debug_frame.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        debug_frame.columnconfigure(0, weight=1)
        debug_frame.rowconfigure(0, weight=1)
        self.debug_notebook = ttk.Notebook(debug_frame)
        self.debug_notebook.grid(row=0, column=0, sticky="nsew")
        self.replay_list = tk.Listbox(self.debug_notebook, exportselection=False)
        self.diff_list = tk.Listbox(self.debug_notebook, exportselection=False)
        self.battle_list = tk.Listbox(self.debug_notebook, exportselection=False)
        self.ai_debug_list = tk.Listbox(self.debug_notebook, exportselection=False)
        self.intake_list = tk.Listbox(self.debug_notebook, exportselection=False)
        self.debug_notebook.add(self.replay_list, text="Replay")
        self.debug_notebook.add(self.diff_list, text="Diff")
        self.debug_notebook.add(self.battle_list, text="Battle")
        self.debug_notebook.add(self.ai_debug_list, text="AI")
        self.debug_notebook.add(self.intake_list, text="Intake")
        self.replay_list.bind("<<ListboxSelect>>", self.select_replay_entry)

    def _build_table(self, parent: ttk.Frame) -> None:
        table = ttk.LabelFrame(parent, text="Board", padding=10)
        table.grid(row=0, column=0, sticky="nsew")
        for col in range(5):
            table.columnconfigure(col, weight=1)
        for row in range(8):
            table.rowconfigure(row, weight=1)

        self.match_banner = ttk.Label(table, anchor="center", font=("Segoe UI", 12, "bold"))
        self.match_banner.grid(row=0, column=0, columnspan=5, sticky="ew", pady=(0, 8))

        self._zone(table, "P1_hand", "AI Hand", 1, 0, 3)
        self._zone(table, "P1_life", "AI Life", 1, 3, 1)
        self._zone(table, "P1_piles", "AI Deck / Trash", 1, 4, 1)
        self._zone(table, "P1_leader", "AI Leader", 2, 0, 1)
        self._zone(table, "P1_board", "AI Character Area", 2, 1, 3)
        self._zone(table, "P1_don", "AI Cost / DON Area", 2, 4, 1)

        center = ttk.LabelFrame(table, text="Shared Battle / Selected Action", padding=8)
        center.grid(row=3, column=0, columnspan=5, sticky="nsew", pady=8)
        center.columnconfigure(0, weight=1)
        self.center_text = tk.Text(center, height=5, wrap="word")
        self.center_text.grid(row=0, column=0, sticky="nsew")
        self.center_text.configure(state="disabled")

        self._zone(table, "P2_don", "Human Cost / DON Area", 4, 0, 1)
        self._zone(table, "P2_board", "Human Character Area", 4, 1, 3)
        self._zone(table, "P2_leader", "Human Leader", 4, 4, 1)
        self._zone(table, "P2_piles", "Human Deck / Trash", 5, 0, 1)
        self._zone(table, "P2_life", "Human Life", 5, 1, 1)
        self._zone(table, "P2_hand", "Human Hand", 5, 2, 3)

    def _zone(self, parent: ttk.Frame, key: str, title: str, row: int, column: int, columnspan: int) -> None:
        frame = ttk.LabelFrame(parent, text=title, padding=6)
        frame.grid(row=row, column=column, columnspan=columnspan, sticky="nsew", padx=4, pady=4)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        canvas = tk.Canvas(frame, height=100, highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame, orient="horizontal", command=canvas.xview)
        canvas.configure(xscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=1, column=0, sticky="ew")

        inner = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind(
            "<Configure>",
            lambda _event, zone_key=key: self.zone_canvases[zone_key].configure(
                scrollregion=self.zone_canvases[zone_key].bbox("all")
            ),
        )
        self.zone_frames[key] = inner
        self.zone_canvases[key] = canvas

    def _build_controls(self, parent: ttk.Frame) -> None:
        actions = ttk.LabelFrame(parent, text="Operator Controls", padding=10)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)

        ttk.Button(actions, text="Refresh View", command=self.refresh_view).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(actions, text="New Game", command=self.new_game).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Button(actions, text="Load State", command=self.load_state_dialog).grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(8, 0))
        ttk.Button(actions, text="Save State", command=self.save_current_state).grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(8, 0))
        ttk.Button(actions, text="Run AI Turn", command=self.run_ai_turn).grid(row=2, column=0, sticky="ew", padx=(0, 6), pady=(8, 0))
        ttk.Button(actions, text="Prepare Human Turn", command=self.prepare_human_turn).grid(row=2, column=1, sticky="ew", padx=(6, 0), pady=(8, 0))
        ttk.Button(actions, text="End Human Turn", command=self.end_human_turn).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        replay = ttk.LabelFrame(parent, text="Replay View", padding=10)
        replay.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        replay.columnconfigure(0, weight=1)
        replay.columnconfigure(1, weight=1)
        replay.columnconfigure(2, weight=1)
        self.replay_label = ttk.Label(replay, text="Live state", anchor="center")
        self.replay_label.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        ttk.Button(replay, text="<", command=self.replay_back).grid(row=1, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(replay, text="Return Live", command=self.return_to_live).grid(row=1, column=1, sticky="ew", padx=4)
        ttk.Button(replay, text=">", command=self.replay_forward).grid(row=1, column=2, sticky="ew", padx=(4, 0))
        ttk.Button(replay, text="Before", command=self.replay_before).grid(row=2, column=0, sticky="ew", padx=(0, 4), pady=(8, 0))
        ttk.Button(replay, text="After", command=self.replay_after).grid(row=2, column=1, sticky="ew", padx=4, pady=(8, 0))
        ttk.Button(replay, text="Restart This Turn", command=self.replay_restart_turn).grid(
            row=2, column=2, sticky="ew", padx=(4, 0), pady=(8, 0)
        )

        command_frame = ttk.LabelFrame(parent, text="Console Command", padding=10)
        command_frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        command_frame.columnconfigure(0, weight=1)
        ttk.Button(command_frame, text="Open Match Console", command=self.open_match_console).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8)
        )
        ttk.Entry(command_frame, textvariable=self.command_var).grid(row=1, column=0, sticky="ew")
        ttk.Button(command_frame, text="Apply Command", command=self.apply_command).grid(row=1, column=1, padx=(8, 0))
        ttk.Label(
            command_frame,
            text='Examples: "played OP12-086 then attach 1 leader", "attack OP12-119 leader", "discard other one"',
            wraplength=420,
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _build_card_details(self, parent: ttk.Frame) -> None:
        details = ttk.LabelFrame(parent, text="Card Details", padding=10)
        details.grid(row=3, column=0, sticky="nsew")
        details.columnconfigure(0, weight=1)
        details.rowconfigure(0, weight=1)
        self.card_details = tk.Text(details, wrap="word", height=18)
        self.card_details.grid(row=0, column=0, sticky="nsew")
        self.card_details.configure(state="disabled")

    def _set_text(self, widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _set_listbox(self, widget: tk.Listbox, items: List[str]) -> None:
        widget.delete(0, tk.END)
        for item in items:
            widget.insert(tk.END, item)

    def _choose_from_dialog(
        self,
        title: str,
        prompt: str,
        labels: List[str],
        optional: bool = True,
    ) -> Optional[int]:
        if not labels:
            return None

        result: Dict[str, Optional[int]] = {"index": None}
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.geometry("720x420")
        dialog.minsize(520, 300)
        dialog.transient(self.console_window if self.console_window is not None else self)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)

        ttk.Label(dialog, text=prompt, wraplength=660, justify="left").grid(
            row=0,
            column=0,
            sticky="ew",
            padx=12,
            pady=(12, 8),
        )

        list_frame = ttk.Frame(dialog)
        list_frame.grid(row=1, column=0, sticky="nsew", padx=12)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        choices = tk.Listbox(list_frame, exportselection=False)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=choices.yview)
        choices.configure(yscrollcommand=scrollbar.set)
        choices.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        for label in labels:
            choices.insert(tk.END, label)
        choices.selection_set(0)

        buttons = ttk.Frame(dialog)
        buttons.grid(row=2, column=0, sticky="ew", padx=12, pady=12)
        buttons.columnconfigure(0, weight=1)

        def choose_selected() -> None:
            selected = choices.curselection()
            result["index"] = selected[0] if selected else 0
            dialog.destroy()

        def skip_choice() -> None:
            result["index"] = None
            dialog.destroy()

        ttk.Button(buttons, text="Choose", command=choose_selected).grid(row=0, column=1, padx=(8, 0))
        if optional:
            ttk.Button(buttons, text="Skip", command=skip_choice).grid(row=0, column=2, padx=(8, 0))
        choices.bind("<Double-Button-1>", lambda _event: choose_selected())
        choices.bind("<Return>", lambda _event: choose_selected())
        dialog.protocol("WM_DELETE_WINDOW", skip_choice if optional else choose_selected)
        dialog.wait_window()
        return result["index"]

    def gui_effect_choice(
        self,
        state: Dict[str, Any],
        player_id: str,
        prompt: str,
        options: List[Dict[str, Any]],
        optional: bool,
    ) -> Optional[str]:
        if player_id != HUMAN_PLAYER:
            return "__default__"
        selection = self._choose_from_dialog(
            "Effect Choice",
            prompt,
            [card_label(card) for card in options],
            optional=optional,
        )
        if selection is None:
            append_console_entry(state, "System", f"Skipped effect choice: {prompt}", kind="system")
            return None
        chosen = options[selection]
        append_console_entry(state, "System", f"Effect choice: {card_label(chosen)}", kind="system")
        return chosen["instance_id"]

    def gui_trigger_choice(
        self,
        state: Dict[str, Any],
        player_id: str,
        card: Dict[str, Any],
    ) -> Optional[bool]:
        if player_id != HUMAN_PLAYER:
            return None
        selection = self._choose_from_dialog(
            "Trigger Choice",
            f"Activate this trigger?\n{card_label(card)}",
            ["Yes", "No"],
            optional=False,
        )
        activate = selection == 0
        append_console_entry(
            state,
            "System",
            f"Trigger {'activated' if activate else 'declined'}: {card_label(card)}",
            kind="system",
        )
        return activate

    def gui_defense_choice(
        self,
        state: Dict[str, Any],
        defender_id: str,
        attacker: Dict[str, Any],
        target: str,
        blocker_options: List[Dict[str, Any]],
        counter_options: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if defender_id != HUMAN_PLAYER:
            return {"mode": "default"}

        blocker_id = None
        if blocker_options:
            blocker_selection = self._choose_from_dialog(
                "Defense Choice",
                f"Choose a blocker for attack from {card_label(attacker)} to {target}.",
                [card_label(card) for card in blocker_options],
                optional=True,
            )
            if blocker_selection is not None:
                blocker_id = blocker_options[blocker_selection]["instance_id"]

        counter_ids: List[str] = []
        available_counters = list(counter_options)
        while available_counters:
            counter_selection = self._choose_from_dialog(
                "Counter Choice",
                f"Use a counter against {card_label(attacker)}?",
                [card_label(card) for card in available_counters],
                optional=True,
            )
            if counter_selection is None:
                break
            chosen = available_counters.pop(counter_selection)
            counter_ids.append(chosen["instance_id"])

        if blocker_id or counter_ids:
            append_console_entry(
                state,
                "System",
                f"Defense choice: blocker {blocker_id or '-'}, counters {', '.join(counter_ids) or '-'}",
                kind="system",
            )
        return {"blocker_id": blocker_id, "counter_ids": counter_ids}

    def _display_state(self) -> Dict[str, Any]:
        entries = self.state.get("replay_log", [])
        if self.replay_position is None or not entries:
            return self.state
        position = max(0, min(self.replay_position, len(entries) - 1))
        self.replay_position = position
        return replay_snapshot_to_display_state(entries[position][self.replay_side])

    def select_replay_entry(self, _event: Optional[tk.Event] = None) -> None:
        selection = self.replay_list.curselection()
        entries = self.state.get("replay_log", [])
        if not selection or not entries:
            return
        position = selection[0]
        if position >= len(entries):
            return
        self.replay_position = position
        self.replay_side = "after"
        self.refresh_view()
        self.status_var.set(replay_entry_label(entries[position]))

    def _render_zone(self, key: str, cards: List[Dict[str, Any]], empty_text: str = "empty") -> None:
        frame = self.zone_frames[key]
        for child in frame.winfo_children():
            child.destroy()
        if not cards:
            ttk.Label(frame, text=empty_text, foreground="#666").pack(anchor="w")
            self.zone_canvases[key].xview_moveto(0)
            return
        for card in cards:
            text = "\n".join(card_tile_lines(card))
            button = tk.Button(
                frame,
                text=text,
                justify="left",
                anchor="nw",
                width=18,
                height=4,
                relief=tk.RIDGE,
                bg="#f8f2df",
                activebackground="#fff7df",
                command=lambda selected=card, zone=key: self.select_card(selected, zone),
            )
            button.pack(side=tk.LEFT, padx=3, pady=3)
        frame.update_idletasks()
        self.zone_canvases[key].configure(scrollregion=self.zone_canvases[key].bbox("all"))
        self.zone_canvases[key].xview_moveto(0)

    def _render_pile_zone(self, key: str, player: Dict[str, Any], player_id: str) -> None:
        cards = [
            {
                "instance_id": f"{player_id}-DECK",
                "card_id": "Deck",
                "name": f"{len(player.get('deck', []))} cards",
                "state": "hidden",
            },
            {
                "instance_id": f"{player_id}-TRASH",
                "card_id": "Trash",
                "name": f"{len(player.get('trash', []))} cards",
                "state": "open",
            },
        ]
        self._render_zone(key, cards)

    def _render_don_zone(self, key: str, player: Dict[str, Any], player_id: str) -> None:
        cards = [
            {
                "instance_id": f"{player_id}-DON-AREA",
                "card_id": "DON Area",
                "name": f"{len(player.get('don_area', []))} available",
            },
            {
                "instance_id": f"{player_id}-DON-SPENT",
                "card_id": "Spent DON",
                "name": f"{len(player.get('spent_don', []))} spent",
            },
            {
                "instance_id": f"{player_id}-DON-ATTACHED",
                "card_id": "Attached DON",
                "name": f"{sum(player.get('attached_don', {}).values())} attached",
            },
        ]
        self._render_zone(key, cards)

    def _render_player(self, display_state: Dict[str, Any], player_id: str) -> None:
        player = display_state["players"][player_id]
        leader = dict(player["leader"])
        leader["attached_don"] = player.get("attached_don", {}).get(leader.get("instance_id"), leader.get("attached_don", 0))
        board_cards = []
        for card in player.get("board", []):
            view_card = dict(card)
            view_card["attached_don"] = player.get("attached_don", {}).get(
                view_card.get("instance_id"),
                view_card.get("attached_don", 0),
            )
            board_cards.append(view_card)
        self._render_zone(f"{player_id}_leader", [leader])
        self._render_zone(f"{player_id}_board", board_cards, "no characters")
        self._render_zone(f"{player_id}_hand", player.get("hand", []), "no hand cards")
        self._render_zone(
            f"{player_id}_life",
            build_hidden_life_cards(player_id, player.get("life", len(player.get("life_cards", [])))),
            "no life",
        )
        self._render_pile_zone(f"{player_id}_piles", player, player_id)
        self._render_don_zone(f"{player_id}_don", player, player_id)

    def _action_log_lines(self) -> List[str]:
        console_lines = format_console_lines(self.state)
        if console_lines != ["No console messages yet."]:
            return console_lines
        lines = collect_recent_log_lines(self.state, count=18)
        if not lines:
            return ["No actions logged yet."]
        return lines

    def open_match_console(self) -> None:
        if self.console_window is not None and self.console_window.winfo_exists():
            self._refresh_console_window()
            self.console_window.lift()
            return

        window = tk.Toplevel(self)
        window.title("GLAT Match Console")
        window.geometry("760x540")
        window.minsize(520, 360)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)

        history_frame = ttk.LabelFrame(window, text="Match Console", padding=10)
        history_frame.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 8))
        history_frame.columnconfigure(0, weight=1)
        history_frame.rowconfigure(0, weight=1)

        self.console_text = tk.Text(history_frame, wrap="word", height=20)
        scrollbar = ttk.Scrollbar(history_frame, orient="vertical", command=self.console_text.yview)
        self.console_text.configure(yscrollcommand=scrollbar.set, state="disabled")
        self.console_text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        command_frame = ttk.Frame(window, padding=(12, 0, 12, 12))
        command_frame.grid(row=1, column=0, sticky="ew")
        command_frame.columnconfigure(0, weight=1)
        command_entry = ttk.Entry(command_frame, textvariable=self.console_popup_command_var)
        command_entry.grid(row=0, column=0, sticky="ew")
        command_entry.bind("<Return>", lambda _event: self.apply_console_window_command())
        ttk.Button(command_frame, text="Send", command=self.apply_console_window_command).grid(
            row=0, column=1, padx=(8, 0)
        )

        window.protocol("WM_DELETE_WINDOW", self.close_match_console)
        self.console_window = window
        self._refresh_console_window()
        command_entry.focus_set()

    def close_match_console(self) -> None:
        if self.console_window is not None and self.console_window.winfo_exists():
            self.console_window.destroy()
        self.console_window = None
        self.console_text = None

    def _refresh_console_window(self) -> None:
        if self.console_text is None:
            return
        lines = self._action_log_lines()
        self.console_text.configure(state="normal")
        self.console_text.delete("1.0", tk.END)
        self.console_text.insert("1.0", "\n".join(lines))
        self.console_text.configure(state="disabled")
        self.console_text.see(tk.END)

    def apply_console_window_command(self) -> None:
        changed = self.execute_match_console_command(
            self.console_popup_command_var.get(),
            error_parent=self.console_window,
        )
        if changed:
            self.console_popup_command_var.set("")

    def _run_ai_turn_from_console(self) -> Tuple[bool, str]:
        if self.state["active_player"] != AI_PLAYER:
            return False, "It is not the AI turn."

        before_log_count = len(self.state.get("logs", []))
        try:
            self.engine.run_turn(self.state)
        except Exception as exc:
            raise RuntimeError(f"AI turn failed: {exc}") from exc

        ai_logs = [
            log
            for log in self.state.get("logs", [])[before_log_count:]
            if log.get("player") == AI_PLAYER
        ]
        if not ai_logs:
            append_console_entry(self.state, "AI", "Turn completed with no logged actions.", kind="ai")
        for log in ai_logs:
            append_console_entry(self.state, "AI", summarize_action_log_entry(log), kind="ai")
        return True, "Ran AI turn."

    def _prepare_human_turn_from_console(self) -> Tuple[bool, str]:
        changed, message = ensure_human_turn_ready(self.engine, self.state)
        return changed, message

    def _end_human_turn_from_console(self) -> Tuple[bool, str]:
        if self.state["active_player"] != HUMAN_PLAYER:
            return False, "It is not the human turn."
        end_action = {"type": "end_turn", "payload": {}}
        if not apply_operator_action(self.engine, self.state, end_action):
            return False, "Could not end the human turn."
        self.engine.end_phase(self.state)
        finish_opponent_intake_session(self.state, "completed")
        return True, "Ended human turn."

    def _handle_gui_console_command(self, command: str) -> Optional[Tuple[bool, str]]:
        normalized = command.strip().lower()
        if normalized in {"help", "commands"}:
            return (
                False,
                "Console commands: run ai, prepare human, end human turn, save, refresh, return live, replay back, replay forward, restart turn, find <card>.",
            )
        if normalized in {"run ai", "run ai turn", "ai", "ai turn"}:
            return self._run_ai_turn_from_console()
        if normalized in {"prepare human", "prepare human turn", "prepare turn"}:
            return self._prepare_human_turn_from_console()
        if normalized in {"end human", "end human turn", "end turn"}:
            return self._end_human_turn_from_console()
        if normalized in {"save", "save state"}:
            save_state(self.engine, self.state, str(self.state_path))
            return True, "Saved current state."
        if normalized in {"refresh", "refresh view"}:
            return False, "Refreshed view."
        if normalized in {"return live", "live"}:
            self.replay_position = None
            self.replay_side = "after"
            return False, "Returned to live state."
        if normalized in {"replay back", "back"}:
            self.replay_back()
            return False, "Showing earlier replay snapshot."
        if normalized in {"replay forward", "forward"}:
            self.replay_forward()
            return False, "Showing later replay snapshot."
        if normalized in {"replay before", "before"}:
            self.replay_before()
            return False, "Showing replay before snapshot."
        if normalized in {"replay after", "after"}:
            self.replay_after()
            return False, "Showing replay after snapshot."
        if normalized in {"restart turn", "restart this turn"}:
            self.replay_restart_turn()
            return False, "Showing start of turn."
        return None

    def execute_match_console_command(
        self,
        command: str,
        error_parent: Optional[tk.Misc] = None,
    ) -> bool:
        raw = command.strip()
        if not raw:
            message = "Enter a command first."
            append_console_entry(self.state, "System", message, kind="warning")
            self._save_and_refresh(message)
            return False

        append_console_entry(self.state, "You", raw, kind="command", command=raw)
        try:
            handled = self._handle_gui_console_command(raw)
            if handled is None:
                changed, message = process_operator_command(self.engine, self.state, raw)
            else:
                changed, message = handled
        except Exception as exc:
            messagebox.showerror("Command failed", str(exc), parent=error_parent)
            append_console_entry(self.state, "System", f"Command failed: {exc}", kind="error")
            self._save_and_refresh(f"Command failed: {exc}")
            return False

        append_console_entry(
            self.state,
            "System",
            message,
            kind="success" if changed else "warning",
            command=raw,
        )
        self._save_and_refresh(message)
        return changed

    def select_card(self, card: Dict[str, Any], zone: str) -> None:
        self.selected_card = card
        parts = zone.split("_", 1)
        self.selected_location = (parts[0], parts[1] if len(parts) > 1 else zone)
        self._set_text(
            self.card_details,
            "\n".join(card_detail_lines(card, self.selected_location[0], self.selected_location[1])),
        )

    def refresh_view(self) -> None:
        display_state = self._display_state()
        self.header_label.config(text=f"State file: {self.state_path.resolve()}")
        mode = "LIVE"
        if self.replay_position is not None and self.state.get("replay_log"):
            entry = self.state["replay_log"][self.replay_position]
            mode = f"REPLAY {entry['index']} {self.replay_side.upper()}"
            self.replay_label.config(text=replay_entry_label(entry))
        else:
            self.replay_label.config(text="Live state")
        self.match_banner.config(
            text=(
                f"{mode} | {display_state.get('match_mode', 'digital_strict')} | "
                f"Turn {display_state['turn']} | Active {display_state['active_player']} | "
                f"Phase {display_state['phase']} | Winner {display_state.get('winner') or '-'}"
            )
        )
        self._set_text(self.center_text, "\n".join(format_summary_lines(display_state)))
        self._refresh_console_window()
        self._set_listbox(self.replay_list, collect_replay_log_lines(self.state))
        if self.replay_position is not None and self.state.get("replay_log"):
            self.replay_list.selection_clear(0, tk.END)
            self.replay_list.selection_set(self.replay_position)
            self.replay_list.see(self.replay_position)
        self._set_listbox(self.intake_list, collect_intake_log_lines(self.state))
        self._set_listbox(self.diff_list, collect_replay_diff_lines(self.state, self.replay_position))
        self._set_listbox(self.battle_list, collect_battle_trace_lines(self.state))
        self._set_listbox(self.ai_debug_list, collect_ai_debug_lines(self.state))

        for player_id in (AI_PLAYER, HUMAN_PLAYER):
            self._render_player(display_state, player_id)
        self._set_text(
            self.card_details,
            "\n".join(card_detail_lines(self.selected_card, *self.selected_location)),
        )

    def _save_and_refresh(self, message: str) -> None:
        self.replay_position = None
        save_state(self.engine, self.state, str(self.state_path))
        self.refresh_view()
        self.status_var.set(message)

    def return_to_live(self) -> None:
        self.replay_position = None
        self.replay_side = "after"
        self.refresh_view()
        self.status_var.set("Returned to live state.")

    def replay_back(self) -> None:
        entries = self.state.get("replay_log", [])
        if not entries:
            self.status_var.set("No replay entries yet.")
            return
        if self.replay_position is None:
            self.replay_position = len(entries) - 1
        else:
            self.replay_position = max(0, self.replay_position - 1)
        self.replay_side = "after"
        self.refresh_view()
        self.status_var.set("Showing earlier replay snapshot.")

    def replay_forward(self) -> None:
        entries = self.state.get("replay_log", [])
        if not entries:
            self.status_var.set("No replay entries yet.")
            return
        if self.replay_position is None:
            self.replay_position = 0
        elif self.replay_position >= len(entries) - 1:
            self.return_to_live()
            return
        else:
            self.replay_position += 1
        self.replay_side = "after"
        self.refresh_view()
        self.status_var.set("Showing later replay snapshot.")

    def replay_before(self) -> None:
        entries = self.state.get("replay_log", [])
        if not entries:
            self.status_var.set("No replay entries yet.")
            return
        if self.replay_position is None:
            self.replay_position = len(entries) - 1
        self.replay_side = "before"
        self.refresh_view()
        self.status_var.set("Showing replay before snapshot.")

    def replay_after(self) -> None:
        entries = self.state.get("replay_log", [])
        if not entries:
            self.status_var.set("No replay entries yet.")
            return
        if self.replay_position is None:
            self.replay_position = len(entries) - 1
        self.replay_side = "after"
        self.refresh_view()
        self.status_var.set("Showing replay after snapshot.")

    def replay_restart_turn(self) -> None:
        entries = self.state.get("replay_log", [])
        if not entries:
            self.status_var.set("No replay entries yet.")
            return
        turn = self.state["turn"] if self.replay_position is None else entries[self.replay_position]["turn"]
        for index, entry in enumerate(entries):
            if entry.get("turn") == turn:
                self.replay_position = index
                self.replay_side = "before"
                self.refresh_view()
                self.status_var.set(f"Showing start of turn {turn}.")
                return
        self.status_var.set(f"No replay entry found for turn {turn}.")

    def new_game(self) -> None:
        match_mode = self.state.get("match_mode", "digital_strict")
        self.state = self.engine.create_initial_state(seed=7, match_mode=match_mode)
        append_console_entry(self.state, "System", f"Started {match_mode} match.", kind="system")
        self._save_and_refresh("Started a new game.")

    def load_state_dialog(self) -> None:
        selected = filedialog.askopenfilename(
            title="Load GLAT state",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir=str(self.state_path.parent),
        )
        if not selected:
            return
        try:
            self.state_path = Path(selected)
            self.state = load_state(self.engine, selected)
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))
            self.status_var.set(f"Load failed: {exc}")
            return
        self._save_and_refresh(f"Loaded state from {selected}")

    def save_current_state(self) -> None:
        try:
            save_state(self.engine, self.state, str(self.state_path))
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            self.status_var.set(f"Save failed: {exc}")
            return
        self.refresh_view()
        self.status_var.set("Saved current state.")

    def run_ai_turn(self) -> None:
        try:
            changed, message = self._run_ai_turn_from_console()
        except Exception as exc:
            messagebox.showerror("AI turn failed", str(exc))
            self.status_var.set(f"AI turn failed: {exc}")
            return
        if not changed:
            append_console_entry(self.state, "System", message, kind="warning")
        self._save_and_refresh(message)

    def prepare_human_turn(self) -> None:
        try:
            _, message = ensure_human_turn_ready(self.engine, self.state)
        except Exception as exc:
            messagebox.showerror("Prepare failed", str(exc))
            self.status_var.set(f"Prepare failed: {exc}")
            return
        append_console_entry(self.state, "System", message, kind="system")
        self._save_and_refresh(message)

    def end_human_turn(self) -> None:
        if self.state["active_player"] != HUMAN_PLAYER:
            self.status_var.set("It is not the human turn.")
            return
        end_action = {"type": "end_turn", "payload": {}}
        if not apply_operator_action(self.engine, self.state, end_action):
            self.status_var.set("Could not end the human turn.")
            return
        self.engine.end_phase(self.state)
        finish_opponent_intake_session(self.state, "completed")
        append_console_entry(self.state, "System", "Ended human turn.", kind="system")
        self._save_and_refresh("Ended human turn.")

    def apply_command(self) -> None:
        changed = self.execute_match_console_command(self.command_var.get(), error_parent=self)
        if changed:
            self.command_var.set("")


def main() -> None:
    parser = argparse.ArgumentParser(description="GLAT local operator GUI")
    parser.add_argument("--state-out", default="cli_game_state.json")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--fake-ai", action="store_true", help="Use deterministic fake AI instead of Gemini")
    parser.add_argument(
        "--ai",
        choices=["gemini", "fake", "heuristic"],
        default="gemini",
        help="AI policy to use for P1 turns",
    )
    parser.add_argument(
        "--match-mode",
        choices=["digital_strict", "physical_reported"],
        default="digital_strict",
        help="Match mode for new state files",
    )
    args = parser.parse_args()

    app = OperatorGUI(
        state_path=args.state_out,
        use_fake_ai=args.fake_ai,
        ai_mode=args.ai,
        seed=args.seed,
        match_mode=args.match_mode,
    )
    app.mainloop()


if __name__ == "__main__":
    main()
